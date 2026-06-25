"""
Infomax API 수집기
- /api/stock/hist     → ohlcv_daily, market_cap_daily
- /api/stock/investor → investor_trading
- /api/stock/foreign  → foreign_ownership
"""

import re
import time
import threading
import sys
from pathlib import Path
from datetime import date, datetime
from typing import Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings

BASE_URL   = settings.INFOMAX_BASE_URL
TOKEN      = settings.INFOMAX_API_KEY
REQ_DELAY  = 1.05   # 초 (60회/분 Lite 플랜 기준)
MAX_RETRY  = 3
RETRY_WAIT = 5.0


class InfomaxDailyLimitError(Exception):
    """인포맥스 일별 API 호출 한도 초과."""

# 투자자 API 코드 → DB investor_type 매핑
# ※ API는 '연기금' 대신 '기금공제'로 반환함 (실측 확인)
INVESTOR_MAP = {
    "외국인": "FOREIGN",
    "기관계": "INSTITUTION",
    "기금공제": "PENSION",   # 연기금 = 기금공제
    "개인":    "RETAIL",
}


def pick_nearest_deferred(rows: list[dict]) -> list[dict]:
    """NEXT(/api/future/2active) 응답은 날짜마다 근월 이후 상장된 *모든* 원월물을
    반환한다(가까운→먼 순). 진짜 차근월 = 날짜별 만기 최소 1개만 골라야 한다.
    (이전 dedup-last 로직은 최원월물을 남겨 OHLC=0 미거래 계약을 적재하던 버그.)

    만기는 kr_name 끝의 YYYYMM(예: "코스피200 F 202406")으로 판정. 못 읽으면 뒤로 보냄.
    """
    def _expiry(r: dict) -> str:
        m = re.search(r"(\d{6})\s*$", str(r.get("kr_name", "") or ""))
        return m.group(1) if m else "999999"

    best: dict = {}
    for r in rows:
        d = r.get("date")
        if d is None:
            continue
        k = _expiry(r)
        if d not in best or k < best[d][0]:
            best[d] = (k, r)
    return [r for _, r in best.values()]


class InfomaxClient:
    """Infomax REST API 클라이언트 (thread-safe)"""

    # 모든 인스턴스·스레드가 공유하는 rate limiter
    _rate_lock      = threading.Lock()
    _rate_last_call = 0.0

    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False
        self.headers = {"Authorization": f"bearer {TOKEN}"}

    def _throttle(self):
        """전역 공유 rate limiter — 멀티스레드 환경에서도 분당 60회 준수"""
        with InfomaxClient._rate_lock:
            elapsed = time.time() - InfomaxClient._rate_last_call
            if elapsed < REQ_DELAY:
                time.sleep(REQ_DELAY - elapsed)
            InfomaxClient._rate_last_call = time.time()

    def _get(self, endpoint: str, params: dict) -> Optional[dict]:
        url = f"{BASE_URL}{endpoint}"
        for attempt in range(1, MAX_RETRY + 1):
            self._throttle()
            try:
                r = self.session.get(url, params=params,
                                     headers=self.headers, timeout=30)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("success"):
                        return data
                    msg = data.get("message", {})
                    if isinstance(msg, dict) and "사용량 제한" in msg.get("errmsg", ""):
                        raise InfomaxDailyLimitError(msg.get("errmsg", "일별 사용량 제한 초과"))
                    # success=False 면 재시도 불필요 (파라미터 문제)
                    return None
                # 429 Too Many Requests → 대기 후 재시도
                if r.status_code == 429:
                    time.sleep(RETRY_WAIT * attempt)
                    continue
            except requests.Timeout:
                if attempt < MAX_RETRY:
                    time.sleep(RETRY_WAIT)
            except requests.RequestException:
                if attempt < MAX_RETRY:
                    time.sleep(RETRY_WAIT)
        return None

    # ── OHLCV (/api/stock/hist) ──────────────────────────────────────────
    def get_hist(self, code: str,
                 start: date, end: date) -> list[dict]:
        """
        일봉 OHLCV + listed_shares 조회
        반환: [{"date", "stock_code", "open_price", "high_price", "low_price",
                "close_price", "volume", "trading_value", "listed_shares"}, ...]
        """
        params = {
            "code":      code,
            "startDate": start.strftime("%Y%m%d"),
            "endDate":   end.strftime("%Y%m%d"),
        }
        data = self._get("/api/stock/hist", params)
        if not data:
            return []

        rows = []
        for r in data.get("results", []):
            rows.append({
                "date":          self._parse_date(r.get("date")),
                "stock_code":    r.get("code", code),
                "open_price":    r.get("open_price"),
                "high_price":    r.get("high_price"),
                "low_price":     r.get("low_price"),
                "close_price":   r.get("close_price"),
                "volume":        r.get("trading_volume"),
                "trading_value": r.get("trading_value"),
                "listed_shares": r.get("listed_shares"),
            })
        return rows

    # ── 투자자별 수급 (/api/stock/investor) ────────────────────────────────
    # API는 bid_value / ask_value를 천원(千원) 단위로 반환
    # → 원(원화) 단위로 변환하기 위해 × 1,000 적용
    _INVESTOR_VALUE_UNIT = 1_000

    def get_investor(self, code: str,
                     start: date, end: date) -> list[dict]:
        """
        4개 투자자 유형 수급 한 번에 조회 (investor 미입력 = 전체 반환)
        반환: [{"date", "stock_code", "investor_type",
                "net_buy_value"(원), "net_buy_volume"(주)}, ...]

        단위 검증:
          bid_value / bid_volume (원 단위 가정) → 100원 미만이면 천원 단위로 판단
          정상 한국 주식 범위: 100원 ~ 10,000,000원/주
        """
        params = {
            "code":      code,
            "startDate": start.strftime("%Y%m%d"),
            "endDate":   end.strftime("%Y%m%d"),
        }
        data = self._get("/api/stock/investor", params)
        if not data:
            return []

        results = data.get("results", [])

        # API는 항상 천원(千원) 단위로 반환 → 항상 ×1,000 적용
        # 주의: bid_value/bid_volume으로 단위 자동감지를 시도했으나
        # 주가 ≥ 100,000원 종목에서 역산단가가 100 이상이 되어 오판하는 버그가 있었음.
        # (예: 삼성전자 199,400원 → 천원 단위 역산 = 199.4 ≥ 100 → 원으로 오인식)
        unit = self._INVESTOR_VALUE_UNIT  # 항상 1,000

        rows = []
        for r in results:
            api_investor = r.get("investor", "")
            db_type = INVESTOR_MAP.get(api_investor)
            if db_type is None:
                continue    # 매핑 없는 투자자 유형 스킵

            bid_val = r.get("bid_value", 0) or 0
            ask_val = r.get("ask_value", 0) or 0
            bid_vol = r.get("bid_volume", 0) or 0
            ask_vol = r.get("ask_volume", 0) or 0

            rows.append({
                "date":           self._parse_date(r.get("date")),
                "stock_code":     r.get("code", code),
                "investor_type":  db_type,
                "net_buy_value":  (bid_val - ask_val) * unit,   # 원 단위
                "net_buy_volume": bid_vol - ask_vol,             # 주 단위
            })
        return rows

    # ── 현재 상장 종목 목록 (/api/stock/code) ──────────────────────────────
    def get_stock_codes(self) -> list[dict]:
        """
        현재 상장 종목 목록 전체 조회 (API 1,000개 상한 우회)

        전략:
          - 1,000개 미만 구간: 단일 호출
          - 1,000개 이상 구간: code 파라미터로 2자리 prefix(00~49) 분리
            → startswith() 필터링으로 해당 prefix 종목만 추출
          처리 대상:
          - KOSPI:  market=1(주식) + market=2(기타)  → 각 단일 호출
          - KOSDAQ: market=7 + type=ST               → prefix 분리
          - ETF:    type=EF                           → prefix 분리
          - 기타:   EN,MF,RT,IF,DR,SW,SR,EW,BC,FS   → 각 단일 호출

        반환: [{"code", "name", "market", "listing_date", "standard_code"}, ...]
        """
        # API 응답 market 필드(한글) → DB market 값 (숫자코드도 대응)
        MARKET_MAP = {
            "거래소(코스피)": "KOSPI", "거래소기타": "KOSPI",
            "코스닥": "KOSDAQ", "코스닥기타": "KOSDAQ",
            "1": "KOSPI", "2": "KOSPI", "5": "KOSPI",
            "7": "KOSDAQ", "8": "KOSDAQ",
        }
        # 일반 주식 equity_type (한글/영문 모두 대응)
        STOCK_TYPES = {"ST", "주식"}

        def _parse(r: dict) -> Optional[dict]:
            code = r.get("code")
            if not code:
                return None
            market = MARKET_MAP.get(str(r.get("market", "")))
            eq_type = r.get("equity_type", "")
            if eq_type and eq_type not in STOCK_TYPES:
                market = "ETF"
            return {
                "code":          str(code).strip(),
                "name":          r.get("kr_name", ""),
                "market":        market,
                "listing_date":  self._parse_date(r.get("listed_date")),
                "standard_code": r.get("isin"),
            }

        def _fetch(params: dict) -> dict[str, dict]:
            """단일 호출. 결과를 {code: row} dict로 반환."""
            data = self._get("/api/stock/code", params)
            if not data:
                return {}
            out = {}
            for r in data.get("results", []):
                row = _parse(r)
                if row:
                    out[row["code"]] = row
            return out

        def _fetch_split(params: dict) -> dict[str, dict]:
            """
            1,000개 이상 시 2자리 prefix(00~49)로 분리 수집.
            각 prefix별 호출 후 startswith()로 정밀 필터링.
            """
            data = self._get("/api/stock/code", params)
            if not data:
                return {}
            results = data.get("results", [])
            if len(results) < 1000:
                out = {}
                for r in results:
                    row = _parse(r)
                    if row:
                        out[row["code"]] = row
                return out
            # 1,000개 이상 → 2자리 prefix 분리 (한국 주식 코드 현재 범위: 00~49)
            out = {}
            for i in range(50):
                prefix = f"{i:02d}"
                sub = self._get("/api/stock/code", {**params, "code": prefix})
                if not sub:
                    continue
                for r in sub.get("results", []):
                    if not str(r.get("code", "")).startswith(prefix):
                        continue
                    row = _parse(r)
                    if row:
                        out[row["code"]] = row
            return out

        all_stocks: dict[str, dict] = {}
        all_stocks.update(_fetch({"market": "1", "type": "ST"}))        # KOSPI 주식
        all_stocks.update(_fetch_split({"market": "7", "type": "ST"}))  # KOSDAQ 주식
        all_stocks.update(_fetch_split({"type": "EF"}))                 # ETF

        return list(all_stocks.values())

    # ── 상장폐지 종목 목록 (/api/stock/expired) ─────────────────────────────
    def get_expired_codes(self, start_date: Optional[date] = None,
                          end_date: Optional[date] = None) -> list[dict]:
        """
        상장폐지 종목 목록 조회
        start_date: 폐지일 기준 시작일 (None = API 기본값 today-365)
        end_date:   폐지일 기준 종료일 (None = API 기본값 today)
        반환: [{"code", "name", "delisting_date"}, ...]

        API 응답 필드: isin, code, market_type, equity_type, kr_name, listed_date, delisted_date
        """
        params: dict = {}
        if start_date:
            params["startDate"] = start_date.strftime("%Y%m%d")
        if end_date:
            params["endDate"] = end_date.strftime("%Y%m%d")

        data = self._get("/api/stock/expired", params)
        if not data:
            return []

        rows = []
        for r in data.get("results", []):
            code = r.get("code")
            if not code:
                continue
            rows.append({
                "code":           str(code).strip(),
                "name":           r.get("kr_name", ""),
                "delisting_date": self._parse_date(r.get("delisted_date")),
            })
        return rows

    # ── 외국인 지분율 (/api/stock/foreign) ──────────────────────────────────
    def get_foreign(self, code: str,
                    start: date, end: date) -> list[dict]:
        """
        외국인 지분율 조회
        반환: [{"date", "stock_code", "frn_ownership_ratio",
                "frn_ownership_vol", "frn_limit_ratio", "listed_shares"}, ...]
        """
        params = {
            "code":      code,
            "startDate": start.strftime("%Y%m%d"),
            "endDate":   end.strftime("%Y%m%d"),
        }
        data = self._get("/api/stock/foreign", params)
        if not data:
            return []

        rows = []
        for r in data.get("results", []):
            rows.append({
                "date":                self._parse_date(r.get("date")),
                "stock_code":          r.get("code", code),
                "frn_ownership_ratio": r.get("frn_ownership_ratio"),
                "frn_ownership_vol":   r.get("frn_ownership_vol"),
                "frn_limit_ratio":     r.get("frn_limit_ratio"),
            })
        return rows

    # ── 지수 코드 list (/api/index/code) ──────────────────────────────────
    def get_index_codes(self, type_: str = "") -> list[dict]:
        """지수 코드 list. type='' 전체 / K/Q/X/T/N 별."""
        data = self._get("/api/index/code", {"type": type_})
        if not data:
            return []
        return data.get("results", []) or []

    # ── 지수 일별 OHLCV (/api/index/hist) ─────────────────────────────────
    def get_index_hist(self, code: str, start: date, end: date) -> list[dict]:
        """지수 일별 OHLCV. 1000행 한도이므로 호출자가 chunks 분할 권장."""
        params = {"code": code,
                  "startDate": start.strftime("%Y%m%d"),
                  "endDate": end.strftime("%Y%m%d")}
        data = self._get("/api/index/hist", params)
        if not data:
            return []
        return data.get("results", []) or []

    # ── 선물 종목 list (/api/future/code) ─────────────────────────────────
    def get_future_codes(self, underlying_type: str = "") -> list[dict]:
        """선물 종목 list. underlying_type='' 전체 / F/C/G/L 별."""
        data = self._get("/api/future/code", {"underlying_type": underlying_type})
        if not data:
            return []
        return data.get("results", []) or []

    # ── 선물 연결 시계열 (/api/future/active|2active) ─────────────────────
    def get_future_active(self, underlying_code: str, start: date, end: date,
                          contract_class: str = "NEAR") -> list[dict]:
        """
        선물 근월/원월 연결 시계열.
        contract_class: NEAR (근월, /api/future/active) | NEXT (차근월, /api/future/2active)
        1000행 한도 (chunks 분할 권장).
        """
        endpoint = "/api/future/active" if contract_class == "NEAR" else "/api/future/2active"
        params = {"underlying": underlying_code,
                  "startDate": start.strftime("%Y%m%d"),
                  "endDate":   end.strftime("%Y%m%d")}
        data = self._get(endpoint, params)
        if not data:
            return []
        return data.get("results", []) or []

    # ── ETF 마스터 (/api/etp) ─────────────────────────────────────────────
    def get_etf_master(self, code: str, target_date: Optional[date] = None) -> Optional[dict]:
        """
        ETF 추가정보 (creation_unit, listed_shares, kr_company, underlying_index 등).
        target_date 미입력 시 today.
        반환: 단일 dict 또는 None
        """
        params = {"code": code}
        if target_date:
            params["date"] = target_date.strftime("%Y%m%d")
        data = self._get("/api/etp", params)
        if not data:
            return None
        rows = data.get("results", []) or []
        if not rows:
            return None
        r = rows[0]
        return {
            "date":              self._parse_date(r.get("date")),
            "etf_code":          r.get("code", code),
            "kr_name":           r.get("kr_name"),
            "kr_company":        r.get("kr_company"),
            "company_code":      r.get("company_code"),
            "net_asset":         r.get("net_asset"),
            "listed_shares":     r.get("listed_shares"),
            "creationunit":      r.get("creationunit"),
            "tracking_multiple": r.get("tracking_multiple"),
            "replication":       r.get("replication"),
            "underlying_index":  r.get("underlying_index"),
            "index_agency":      r.get("index_agency"),
            "total_fee":         r.get("total_fee"),
        }

    # ── ETF 구성종목 PDF (/api/etf/port) ──────────────────────────────────
    def get_etf_portfolio(self, code: str, target_date: date) -> list[dict]:
        """
        ETF 구성종목 PDF 조회 (KOSPI200/KOSDAQ150 추적 ETF용).
        target_date 미공시일/휴장일이면 빈 list 반환.

        반환: [{"date", "etf_code", "constituents", "port_code", "port_name",
                "port_volume", "port_value"}, ...]
        - port_code 가 6자리 숫자가 아닌 행(원화현금 등)도 함께 반환 — 호출자가 필터.
        """
        params = {
            "code": code,
            "date": target_date.strftime("%Y%m%d"),
        }
        data = self._get("/api/etf/port", params)
        if not data:
            return []

        rows = []
        for r in data.get("results", []):
            rows.append({
                "date":         self._parse_date(r.get("date")),
                "etf_code":     r.get("code", code),
                "constituents": r.get("constituents"),
                "port_code":    r.get("port_code"),
                "port_name":    r.get("port_name"),
                "port_volume":  r.get("port_volume"),
                "port_value":   r.get("port_value"),
            })
        return rows

    @staticmethod
    def _parse_date(val) -> Optional[date]:
        if val is None:
            return None
        try:
            return datetime.strptime(str(val), "%Y%m%d").date()
        except (ValueError, TypeError):
            return None
