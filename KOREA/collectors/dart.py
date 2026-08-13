"""
DART (금융감독원 전자공시) Open API 수집기

Endpoints:
- /api/corpCode.xml      → 회사 고유코드 ↔ 종목코드 매핑 (1회 다운로드 후 캐시)
- /api/list.json         → 공시 검색 (주요사항보고서, 정기보고서 등)
- /api/document.xml      → 공시 원문 다운로드 (XML in zip)
- /api/alotMatter.json   → 정기보고서 내 배당사항 (과거 백필용)

용도:
1. 정관변경 공시 추적 → charter_group A/B 분류
2. 현금ㆍ현물배당결정 공시 실시간 수집 → 확정 배당 데이터
3. 정기보고서 배당사항 → 과거 배당 백필

비고:
- API 키: https://opendart.fss.or.kr 무료 발급, .env DART_API_KEY
- Rate limit: 분당 60회, 일일 20,000회
"""

import io
import re
import sys
import time
import threading
import zipfile
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

import requests

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings


BASE_URL   = settings.DART_BASE_URL
# 모듈 로드 시점의 키 — 폴백용. 실제 호출은 아래 _current_api_key()가 매번 .env를 다시 읽는다.
API_KEY    = settings.DART_API_KEY


def _current_api_key() -> str:
    """호출 시점의 .env DART_API_KEY를 읽는다.

    모듈 로드 시점 상수로 굳히면 **키를 교체해도 장수명 프로세스가 옛 키를 계속 쓴다**.
    2026-08-12에 만료 키를 새 키로 바꿨는데 스케줄러(8/11 기동)가 옛 키를 물고 있어
    8/13 02:00 배당 수집이 같은 901로 또 죽었다 — 재시작해야만 반영되는 걸 아무도
    모르는 상태였다. 이제 DartClient를 새로 만들 때마다 현재 값을 읽으므로
    .env만 고치면 다음 실행부터 먹는다.

    env_file이 상대경로('.env')라 CWD에 좌우되지 않도록 절대경로를 명시한다.
    """
    try:
        from config.settings import Settings
        return (Settings(_env_file=str(project_root / ".env")).DART_API_KEY or "").strip()
    except Exception as e:
        # .env 재읽기 실패는 치명적이지 않다 — 모듈 로드 시점 값으로 계속 간다.
        print(f"⚠️  DART 키 재읽기 실패, 기존 값 사용: {e}")
        return (API_KEY or "").strip()
REQ_DELAY  = 1.05   # 초 (분당 60회 제한)
MAX_RETRY  = 3
RETRY_WAIT = 5.0
TIMEOUT    = 30


# DART status 코드 중 **호출자가 계속 진행하면 안 되는** 것들.
# 013(데이터 없음)·014(파일 없음)·100(필드 부적절)은 정상 흐름의 일부라 제외 —
# 여기 든 것은 키·권한·한도·시스템 문제라 재시도해도 같은 결과이고, 조용히 넘기면
# "0건 수집"으로 위장돼 장애가 묻힌다.
_RAISE_STATUSES = {
    "010": "등록되지 않은 인증키",
    "011": "사용할 수 없는 인증키 (활동중지/폐기)",
    "012": "접근할 수 없는 IP",
    "020": "요청 제한 초과 (일일 한도)",
    "021": "조회 가능한 회사 개수 초과",
    "101": "부적절한 접근",
    "800": "DART 시스템 점검 중",
    "900": "정의되지 않은 오류",
    "901": "사용자 계정의 개인정보 보유기간 만료 — opendart.fss.or.kr에서 재동의/키 재발급 필요",
}


class DartApiError(RuntimeError):
    """DART가 명시적 오류 status를 반환. `.status`로 코드 분기 가능."""

    def __init__(self, status: str, message: str = "", endpoint: str = ""):
        self.status = status
        self.dart_message = (message or "").strip()
        hint = _RAISE_STATUSES.get(status, "")
        where = f" [{endpoint}]" if endpoint else ""
        super().__init__(
            f"DART API 오류 status={status}{where}"
            + (f" — {hint}" if hint else "")
            + (f" | 응답: {self.dart_message}" if self.dart_message else "")
        )


def _raise_for_dart_status(status, message, endpoint: str = "") -> None:
    """치명적 status면 DartApiError를 올린다. 그 외(None/000/013 등)는 통과."""
    if status in _RAISE_STATUSES:
        raise DartApiError(status, message, endpoint)


def _peek_error_status(content: bytes) -> tuple[Optional[str], str]:
    """zip을 기대한 응답이 실은 오류 XML/JSON인지 훑어본다.

    정상 zip은 'PK'로 시작하므로 그 경우 즉시 빠진다 (수 MB 파싱 회피).
    반환: (status, message). 오류가 아니면 (None, "").
    """
    if not content or content[:2] == b"PK":
        return None, ""
    head = content[:2048]
    if not (head.lstrip()[:1] in (b"<", b"{")):
        return None, ""
    text = head.decode("utf-8", errors="replace")
    m_status = re.search(r'[<"]status[>"]\s*:?\s*"?([0-9]{3})', text)
    if not m_status:
        return None, ""
    m_msg = re.search(r'[<"]message[>"]\s*:?\s*"?([^<"]*)', text)
    return m_status.group(1), (m_msg.group(1) if m_msg else "")


# 배당결정 공시명 패턴 (DART 공시명에서 매칭)
# - "현금ㆍ현물배당결정", "현금배당결정" 등 변형 흡수
DIVIDEND_DECISION_PATTERN = re.compile(r"(현금[·ㆍ・]?(현물)?배당결정)")

# 정관변경 공시명 패턴
CHARTER_CHANGE_PATTERN = re.compile(r"정관(\s*일부\s*)?변경")

# 정관 본문에서 새 배당기준일 조항을 식별하는 키워드
# A 그룹 (변경): "이사회가 정하는 날" / "이사회 결의로 정하는 날" 등
CHARTER_NEW_BASIS_PATTERN = re.compile(r"이사회(\s*결의)?\s*(가|로)?\s*정하는\s*날")
# B 그룹 (미변경): "매 결산기 말일" / "사업연도 말일" 등
CHARTER_OLD_BASIS_PATTERN = re.compile(r"(결산기\s*말일|사업연도\s*말일)")


def _decode_xml_bytes(data: bytes) -> str:
    """
    XML/HTML 본문 바이트에서 인코딩 자동 감지.
    1) <?xml encoding="..."?> 선언 우선
    2) UTF-8 strict 시도
    3) CP949 (EUC-KR superset) 폴백
    """
    head = data[:300].lower()
    for enc_label, enc in [
        (b'encoding="utf-8"',  "utf-8"),
        (b"encoding='utf-8'",  "utf-8"),
        (b'encoding="euc-kr"', "cp949"),
        (b"encoding='euc-kr'", "cp949"),
        (b'encoding="cp949"',  "cp949"),
        (b"encoding='cp949'",  "cp949"),
        (b'charset=utf-8',     "utf-8"),
        (b'charset=euc-kr',    "cp949"),
    ]:
        if enc_label in head:
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                pass
    # 휴리스틱: utf-8 strict 시도
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # 한국어 본문은 보통 cp949
        return data.decode("cp949", errors="replace")


class DartClient:
    """DART Open API 클라이언트 (thread-safe)"""

    # 모든 인스턴스/스레드 공유 rate limiter
    _rate_lock      = threading.Lock()
    _rate_last_call = 0.0

    def __init__(self):
        # 인스턴스 생성 시점의 .env 값 — 프로세스가 오래 살아도 키 교체가 반영된다.
        self.api_key = _current_api_key()
        if not self.api_key:
            raise RuntimeError(
                "DART_API_KEY가 설정되지 않았습니다. "
                "https://opendart.fss.or.kr 에서 키 발급 후 .env에 등록하세요."
            )
        self.session = requests.Session()

    # ---------------- 내부 ----------------

    def _throttle(self):
        with DartClient._rate_lock:
            elapsed = time.time() - DartClient._rate_last_call
            if elapsed < REQ_DELAY:
                time.sleep(REQ_DELAY - elapsed)
            DartClient._rate_last_call = time.time()

    def _request(self, endpoint: str, params: dict, expect: str = "json"):
        """
        DART API GET 요청 (rate limit + 재시도).
        expect: 'json' | 'bytes'

        키/권한/한도/시스템 계열 status는 `DartApiError`로 올린다 (`_RAISE_STATUSES`).
        조용히 None을 반환하면 "0건 수집"과 구분이 안 돼 장애가 며칠씩 묻힌다 —
        2026-08-01~08-10 배당 파이프라인 10일 정지가 정확히 그 사례
        (901 만료 키인데 zip 파서까지 흘러가 "File is not a zip file"로만 보였음).
        """
        url = f"{BASE_URL}{endpoint}"
        params = {**params, "crtfc_key": self.api_key}

        for attempt in range(1, MAX_RETRY + 1):
            self._throttle()
            try:
                r = self.session.get(url, params=params, timeout=TIMEOUT)
                if r.status_code == 200:
                    if expect == "json":
                        data = r.json()
                        _raise_for_dart_status(
                            data.get("status"), data.get("message"), endpoint)
                        # DART status: '000'=정상, '013'=조회된 데이터 없음 (정상)
                        if data.get("status") in ("000", "013"):
                            return data
                        return None
                    else:
                        # zip/xml 원본 경로. 오류 응답은 zip이 아니라 작은 XML/JSON으로 오므로
                        # 파서에 넘기기 전에 여기서 걸러 원인을 그대로 드러낸다.
                        status, message = _peek_error_status(r.content)
                        _raise_for_dart_status(status, message, endpoint)
                        return r.content
                if r.status_code == 429:
                    time.sleep(RETRY_WAIT * attempt)
                    continue
            except requests.RequestException:
                if attempt < MAX_RETRY:
                    time.sleep(RETRY_WAIT)
                    continue
                raise
        return None

    # ---------------- 회사 코드 매핑 ----------------

    def get_corp_code_map(self) -> dict[str, str]:
        """
        전체 회사 corp_code ↔ stock_code 매핑 다운로드.
        반환: {stock_code: corp_code}
        ※ 응답이 zip(CORPCODE.xml). 비상장사는 stock_code 비어있음 → 제외.
        """
        content = self._request("/corpCode.xml", {}, expect="bytes")
        if not content:
            return {}

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            with zf.open("CORPCODE.xml") as f:
                tree = ET.parse(f)

        mapping = {}
        for node in tree.getroot().findall("list"):
            stock_code = (node.findtext("stock_code") or "").strip()
            corp_code = (node.findtext("corp_code") or "").strip()
            if stock_code and corp_code:
                mapping[stock_code] = corp_code
        return mapping

    # ---------------- 공시 검색 ----------------

    def search_disclosures(
        self,
        corp_code: Optional[str] = None,
        bgn_de: Optional[str] = None,    # YYYYMMDD
        end_de: Optional[str] = None,
        pblntf_ty: Optional[str] = None, # 'A'=정기, 'B'=주요사항, ...
        page_count: int = 100,
        max_pages: int = 50,
    ) -> list[dict]:
        """
        공시 검색 (전 페이지 자동 순회).
        반환: [{rcept_no, corp_code, corp_name, report_nm, rcept_dt, ...}, ...]
        """
        results: list[dict] = []
        for page_no in range(1, max_pages + 1):
            params = {"page_no": page_no, "page_count": page_count}
            if corp_code:  params["corp_code"] = corp_code
            if bgn_de:     params["bgn_de"] = bgn_de
            if end_de:     params["end_de"] = end_de
            if pblntf_ty:  params["pblntf_ty"] = pblntf_ty

            data = self._request("/list.json", params)
            if not data or data.get("status") == "013":
                break

            page_items = data.get("list", []) or []
            results.extend(page_items)

            total_page = int(data.get("total_page") or 1)
            if page_no >= total_page:
                break

        return results

    def get_dividend_decisions(
        self,
        corp_code: Optional[str] = None,
        bgn_de: Optional[str] = None,
        end_de: Optional[str] = None,
    ) -> list[dict]:
        """
        '현금ㆍ현물배당결정' 공시 필터.
        ※ pblntf_ty 필터 사용 금지: 같은 공시명이 주요사항보고서(B)·
           거래소공시(I) 등 다양한 유형으로 들어옴. report_nm 키워드로 매칭.
        ※ '(자회사의 주요경영사항)' 공시는 모회사 corp_code로 들어오지만
           실제로는 비상장 자회사의 배당이라 1주당 금액이 모회사 주가와 무관 → 제외.
        """
        items = self.search_disclosures(
            corp_code=corp_code, bgn_de=bgn_de, end_de=end_de
        )
        out = []
        for x in items:
            name = (x.get("report_nm") or "").strip()
            if not DIVIDEND_DECISION_PATTERN.search(name):
                continue
            if "자회사" in name:  # 자회사·종속회사 등 모회사 외 공시 제외
                continue
            out.append(x)
        return out

    def get_charter_changes(
        self,
        corp_code: Optional[str] = None,
        bgn_de: Optional[str] = None,
        end_de: Optional[str] = None,
    ) -> list[dict]:
        """주주총회 '정관일부변경' 관련 공시 필터."""
        # 정관변경은 주주총회소집결의(C), 임시공시(I) 등 여러 유형으로 들어옴.
        # 폭넓게 잡고 report_nm으로 후필터.
        items = self.search_disclosures(
            corp_code=corp_code, bgn_de=bgn_de, end_de=end_de
        )
        return [x for x in items
                if CHARTER_CHANGE_PATTERN.search(
                    (x.get("report_nm") or "").strip())]

    # ---------------- 원문 다운로드 ----------------

    def get_document_xml(self, rcept_no: str) -> Optional[str]:
        """
        공시 원문 XML (zip 압축 풀어서 텍스트 반환).
        ※ DART 공시는 UTF-8/EUC-KR(CP949) 혼재. 자동 감지.
        """
        content = self._request("/document.xml", {"rcept_no": rcept_no}, expect="bytes")
        if not content:
            return None
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".xml"):
                    return _decode_xml_bytes(zf.read(name))
        return None

    # ---------------- 정기보고서 배당사항 (백필용) ----------------

    def get_dividend_report(
        self,
        corp_code: str,
        bsns_year: int,
        reprt_code: str = "11011",   # 11011=사업, 11012=반기, 11013=1분기, 11014=3분기
    ) -> list[dict]:
        """
        정기보고서 배당에 관한 사항.
        반환: 보고서 내 항목별 행 (주당현금배당금, 시가배당률 등)
        """
        params = {
            "corp_code": corp_code,
            "bsns_year": str(bsns_year),
            "reprt_code": reprt_code,
        }
        data = self._request("/alotMatter.json", params)
        if not data:
            return []
        return data.get("list", [])

    # ---------------- 정적 헬퍼 ----------------

    @staticmethod
    def make_raw_text_url(rcept_no: str) -> str:
        """공시 원문 페이지 URL (외부 브라우저용)"""
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

    @staticmethod
    def classify_charter_group(xml_text: str) -> Optional[str]:
        """
        정관 본문 텍스트에서 배당기준일 조항을 보고 A/B 분류.
        - A: 이사회가 결의하는 날 (변경된 정관)
        - B: 결산기 말일 (전통적인 정관)
        - None: 판별 불가
        """
        if not xml_text:
            return None
        # XML 태그 제거 후 평문에서 매칭
        text = re.sub(r"<[^>]+>", " ", xml_text)
        if CHARTER_NEW_BASIS_PATTERN.search(text):
            return "A"
        if CHARTER_OLD_BASIS_PATTERN.search(text):
            return "B"
        return None

    @staticmethod
    def is_subsidiary_disclosure(xml_text: str) -> bool:
        """
        본문이 자회사(비상장) 배당 결정인지 판별. 강화된 다중 패턴 검사.
        report_nm 필터('자회사' 키워드)를 우회한 공시도 잡기 위함.
        """
        if not xml_text:
            return False
        text = re.sub(r"<[^>]+>", " ", xml_text)
        text = re.sub(r"\s+", " ", text)

        # P1. "주요자회사명" — 100% 자회사 공시
        if re.search(r"주요\s*자회사명", text):
            return True

        # P2. "자회사의 주요경영사항" 본문 어디든 등장 시
        if "자회사의 주요경영사항" in text:
            return True

        # P3. 자회사명 뒤에 [비상장] 마커 (예: "㈜연우 [비상장]")
        if re.search(r"\[\s*비상장\s*\]", text):
            return True

        # P4. "지주회사의 자회사" 또는 "지주회사로서 자회사" 패턴
        if re.search(r"지주회사\s*[의로]?[서]?\s*[가-힣]*자회사", text):
            return True

        # P5. 100% 자회사 또는 종속회사 + 비상장 + 본 공시 = 모회사 신고
        if re.search(r"(100\s*%|100\s*퍼센트)\s*자회사", text) and "비상장" in text:
            return True

        # P6. (안전장치) 자회사 + 비상장 + 1주당 동시 등장 + 본문 후반에 지주회사 시그널
        if "자회사" in text and "비상장" in text and "1주당" in text:
            tail = text[-2000:]
            if any(kw in tail for kw in ["지주회사", "주요자회사", "비상장 회사", "지주사"]):
                return True

        return False

    @staticmethod
    def parse_dividend_decision(xml_text: str) -> dict:
        """
        '현금ㆍ현물배당결정' 공시 본문에서 핵심 필드 추출.
        반환 키:
          amount, yield_pct,
          record_date, pay_date, board_resolution_date,
          dividend_class ('결산배당'/'분기배당'/'반기배당'/'중간배당' 등),
          period (Q1/Q2/Q3/Q4/H1/ANNUAL — dividend_class+record_date로 추정)

        보통주 기준만 추출 (종류주식은 별도 처리 필요).
        DART 양식 예: "1주당 배당금(원) 보통주식 566 종류주식 567"
        """
        if not xml_text:
            return {}
        text = re.sub(r"<[^>]+>", " ", xml_text)
        text = re.sub(r"\s+", " ", text)

        result: dict = {}

        # 1주당 배당금 — "1주당 배당금(원) 보통주식 566" 패턴
        # 보통주가 "-"이면 우선주만 → amount 없음으로 처리.
        # "보통주식 - 종류주식 35" 같은 케이스는 보통주식 다음에 숫자 안 나오므로 매칭 실패.
        m = re.search(
            r"1주당\s*(?:현금|현물)?\s*배당금\s*\(?\s*원\s*\)?\s*"
            r"(?:보통주식\s*)?([\d,]+(?:\.\d+)?)",
            text)
        if m:
            try:
                amt = float(m.group(1).replace(",", ""))
                if amt > 0:
                    result["amount"] = amt
            except ValueError:
                pass

        # 시가배당률 — "시가배당율(%) 보통주식 0.5"
        m = re.search(
            r"시가배당[률율]\s*\(?\s*%\s*\)?\s*(?:보통주식)?\s*([\d.]+)",
            text)
        if m:
            try:
                result["yield_pct"] = float(m.group(1))
            except ValueError:
                pass

        # 배당기준일
        m = re.search(
            r"배당기준일\s*[:\-]?\s*(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})",
            text)
        if m:
            result["record_date"] = (
                f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}")

        # 배당금지급(예정)일자 — "-"이면 미정 (값 없음)
        m = re.search(
            r"배당금\s*지급(?:\s*예정)?\s*일자?\s*[:\-]?\s*(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})",
            text)
        if m:
            result["pay_date"] = (
                f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}")

        # 이사회결의일(결정일) — 공백 없을 수 있음
        m = re.search(
            r"이사회\s*결의일(?:\s*\(\s*결정일\s*\))?\s*[:\-]?\s*"
            r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})",
            text)
        if m:
            result["board_resolution_date"] = (
                f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}")

        # 배당구분 — "결산배당", "분기배당", "반기배당", "중간배당"
        m = re.search(r"배당구분\s*([결산분기반중간기말]+배당)", text)
        if m:
            result["dividend_class"] = m.group(1)

        # period 추정: dividend_class + record_date 월
        result["period"] = _infer_period(result.get("dividend_class"),
                                          result.get("record_date"))
        return result


def _infer_period(dividend_class: Optional[str],
                  record_date_str: Optional[str]) -> Optional[str]:
    """
    배당구분과 배당기준일 월을 보고 period 결정.
    - 결산배당/기말배당 → ANNUAL
    - 반기배당/중간배당 → H1
    - 분기배당:
        3월 → Q1, 6월 → Q2, 9월 → Q3, 12월 → ANNUAL (결산과 겹침)
    """
    if not dividend_class:
        return None
    if "결산" in dividend_class or "기말" in dividend_class:
        return "ANNUAL"
    if "반기" in dividend_class or "중간" in dividend_class:
        return "H1"
    if "분기" in dividend_class and record_date_str:
        try:
            month = int(record_date_str.split("-")[1])
        except (ValueError, IndexError):
            return None
        if month in (1, 2, 3):  return "Q1"
        if month in (4, 5, 6):  return "Q2"
        if month in (7, 8, 9):  return "Q3"
        if month in (10, 11, 12): return "Q4"
    return None


if __name__ == "__main__":
    # 모듈 동작 확인 (API 키 있을 때만)
    if not _current_api_key():
        print("DART_API_KEY 미설정 → 실제 호출 불가. 모듈 import는 정상.")
        sys.exit(0)

    client = DartClient()
    print("[1] 회사 코드 매핑 다운로드 중...")
    corp_map = client.get_corp_code_map()
    print(f"  → {len(corp_map):,}개 종목 매핑")
    samsung = corp_map.get("005930")
    print(f"  → 005930(삼성전자) corp_code: {samsung}")

    if samsung:
        print("\n[2] 삼성전자 최근 1년 현금배당결정 공시 검색...")
        from datetime import date, timedelta
        today = date.today()
        bgn = (today - timedelta(days=365)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        items = client.get_dividend_decisions(corp_code=samsung, bgn_de=bgn, end_de=end)
        for x in items[:5]:
            print(f"  {x.get('rcept_dt')} | {x.get('report_nm')} | rcp={x.get('rcept_no')}")
