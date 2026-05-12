"""
LS증권 OpenAPI 클라이언트 — 30초봉(t1302) 수집용

- OAuth2 토큰 발급 + 23h 메모리 캐시 (TTL 24h, 마진 1h)
- TPS 1 — 호출 간 1.05초 대기 (전역 lock)
- get_30sec_bars(code, target_date): 단일 종목 단일 일자 30초봉 list
- 페이징: 응답 cts_time이 비어있지 않으면 연속 호출

LENS realtime/src/feed/ls_rest.rs 패턴 참고.
"""

from __future__ import annotations

import signal
import sys
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings


KST       = ZoneInfo("Asia/Seoul")
BASE_URL  = settings.LS_BASE_URL
APP_KEY   = settings.LS_APP_KEY
APP_SECRET = settings.LS_APP_SECRET
REQ_DELAY = 1.05         # TPS 1 + 마진
MAX_RETRY = 3
RETRY_WAIT = 10.0        # LS 부하 케이스 retry 간격 (이전 5초는 너무 짧음)
TOKEN_TTL = 23 * 3600    # 23h (다음날 07:00 KST 만료라 마진 1h)
TOKEN_CALL_LIMIT = 5000  # 한 token 5000회 호출 후 자동 갱신 (LS token-level 한도 회피)


# ── 에러 분류 ─────────────────────────────────────────
class LsApiError(Exception):
    """LS API 호출 실패. category: no_data | http_5xx | tps | other"""
    def __init__(self, message: str, category: str = "other"):
        super().__init__(message)
        self.category = category


# ── OS-level 강제 timeout (requests의 read timeout이 CLOSE-WAIT에 안 먹는 케이스 방어) ──
class _HardTimeout(Exception):
    pass


def _alarm_handler(signum, frame):
    raise _HardTimeout()


@contextmanager
def hard_timeout(seconds: int):
    """signal.alarm으로 강제 timeout. main thread에서만 동작."""
    old = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _classify_error(rsp_msg: str, status_code: int = 0) -> str:
    msg = (rsp_msg or "").lower()
    if status_code == 429 or "초과" in (rsp_msg or "") or "rate" in msg:
        return "tps"
    if status_code >= 500:
        return "http_5xx"
    if "없" in (rsp_msg or "") or "no data" in msg:
        return "no_data"
    return "other"


# ── 클라이언트 ────────────────────────────────────────
class LsApiClient:
    """LS증권 OpenAPI 클라이언트 (thread-safe)"""

    # 전역 rate limiter (LENS realtime과 별도 — 양쪽 프로세스 각자 보유)
    _rate_lock      = threading.Lock()
    _rate_last_call = 0.0

    # 토큰 캐시 (프로세스 단위)
    _token_lock     = threading.Lock()
    _token_value    = None     # str
    _token_expires  = 0.0      # epoch seconds
    _token_call_cnt = 0        # 토큰 사용 호출 카운트 (TOKEN_CALL_LIMIT 도달 시 갱신)

    def __init__(self):
        # session은 매 호출마다 _new_session()으로 새로 생성 (CLOSE_WAIT 누적 방지)
        self.session = self._new_session()

    @staticmethod
    def _new_session():
        s = requests.Session()
        s.verify = False
        adapter = requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=0)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        return s

    def _refresh_session(self):
        """이전 session 강제 close + 새 session 생성. CLOSE_WAIT 누적 방지."""
        try:
            self.session.close()
        except Exception:
            pass
        self.session = self._new_session()

    # ── 토큰 ──────────────────────────────────────────
    def _fetch_token(self) -> str:
        url = f"{BASE_URL}/oauth2/token"
        payload = {
            "grant_type": "client_credentials",
            "appkey":     APP_KEY,
            "appsecretkey": APP_SECRET,
            "scope":      "oob",
        }
        headers = {"content-type": "application/x-www-form-urlencoded"}
        with hard_timeout(15):
            r = self.session.post(url, data=payload, headers=headers, timeout=(10, 30))
        r.raise_for_status()
        data = r.json()
        if "access_token" not in data:
            raise LsApiError(f"토큰 발급 실패: {data}", category="other")
        return data["access_token"]

    def _get_token(self) -> str:
        with LsApiClient._token_lock:
            now = time.time()
            # TOKEN_CALL_LIMIT 도달 또는 TTL 만료 시 새 token 발급
            need_refresh = (
                LsApiClient._token_value is None
                or now >= LsApiClient._token_expires
                or LsApiClient._token_call_cnt >= TOKEN_CALL_LIMIT
            )
            if need_refresh:
                tok = self._fetch_token()
                LsApiClient._token_value    = tok
                LsApiClient._token_expires  = now + TOKEN_TTL
                LsApiClient._token_call_cnt = 0
                print(f"[ls_api] token refreshed at {time.strftime('%H:%M:%S')} "
                      f"(prev count={LsApiClient._token_call_cnt})", flush=True)
            LsApiClient._token_call_cnt += 1
            return LsApiClient._token_value

    # ── throttle ──────────────────────────────────────
    def _throttle(self):
        with LsApiClient._rate_lock:
            elapsed = time.time() - LsApiClient._rate_last_call
            if elapsed < REQ_DELAY:
                time.sleep(REQ_DELAY - elapsed)
            LsApiClient._rate_last_call = time.time()

    # ── t1302 호출 (단일) ─────────────────────────────
    def _post_t1302(self, shcode: str, gubun: str, time_str: str, cnt: int,
                    exchgubun: str = "K", tr_cont: str = "N",
                    tr_cont_key: str = "") -> dict:
        url = f"{BASE_URL}/stock/market-data"
        token = self._get_token()
        headers = {
            "content-type":  "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "tr_cd":         "t1302",
            "tr_cont":       tr_cont,
            "tr_cont_key":   tr_cont_key,
            "mac_address":   "",
            "Connection":    "close",
        }
        body = {
            "t1302InBlock": {
                "shcode":    shcode,
                "gubun":     gubun,
                "time":      time_str,
                "cnt":       cnt,
                "exchgubun": exchgubun,
            }
        }

        for attempt in range(1, MAX_RETRY + 1):
            self._throttle()
            self._refresh_session()  # 매 호출마다 새 TCP — CLOSE_WAIT 누적 방지
            try:
                with hard_timeout(25):  # OS-level kill switch (LS 부하 케이스 16초 응답 커버)
                    r = self.session.post(url, json=body, headers=headers, timeout=(10, 30))
                if r.status_code >= 500:
                    if attempt < MAX_RETRY:
                        time.sleep(RETRY_WAIT * attempt)
                        continue
                    raise LsApiError(f"HTTP {r.status_code}", category="http_5xx")
                if r.status_code == 429:
                    time.sleep(RETRY_WAIT * attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                rsp_cd  = data.get("rsp_cd", "")
                rsp_msg = data.get("rsp_msg", "")
                if rsp_cd != "00000":
                    cat = _classify_error(rsp_msg, r.status_code)
                    if cat == "no_data":
                        return {"_no_data": True, "rsp_msg": rsp_msg}
                    raise LsApiError(f"rsp_cd={rsp_cd} msg={rsp_msg}", category=cat)
                return data
            except requests.Timeout:
                if attempt < MAX_RETRY:
                    time.sleep(RETRY_WAIT)
                    continue
                raise LsApiError("timeout", category="other")
            except _HardTimeout:
                if attempt < MAX_RETRY:
                    time.sleep(RETRY_WAIT)
                    continue
                raise LsApiError("hard timeout 15s (CLOSE-WAIT 회피)", category="other")
            except requests.RequestException as e:
                if attempt < MAX_RETRY:
                    time.sleep(RETRY_WAIT)
                    continue
                raise LsApiError(str(e), category="other")
        raise LsApiError("max retries exceeded", category="other")

    # ── t8452: 통합 주식차트 N분 (백필/일배치 메인 TR) ────────────────
    def _post_t8452(self, shcode: str, ncnt: int, qrycnt: int, edate: str,
                    sdate: str = "", cts_date: str = "", cts_time: str = "",
                    exchgubun: str = "K", tr_cont: str = "N",
                    tr_cont_key: str = "") -> dict:
        url = f"{BASE_URL}/stock/chart"
        token = self._get_token()
        headers = {
            "content-type":  "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "tr_cd":         "t8452",
            "tr_cont":       tr_cont,
            "tr_cont_key":   tr_cont_key,
            "mac_address":   "",
            "Connection":    "close",
        }
        body = {
            "t8452InBlock": {
                "shcode":    shcode,
                "ncnt":      ncnt,
                "qrycnt":    qrycnt,
                "nday":      "0",
                "sdate":     sdate,
                "stime":     "",
                "edate":     edate,
                "etime":     "",
                "cts_date":  cts_date,
                "cts_time":  cts_time,
                "comp_yn":   "N",
                "exchgubun": exchgubun,
            }
        }
        for attempt in range(1, MAX_RETRY + 1):
            self._throttle()
            self._refresh_session()
            try:
                with hard_timeout(25):
                    r = self.session.post(url, json=body, headers=headers, timeout=(10, 30))
                if r.status_code >= 500:
                    if attempt < MAX_RETRY:
                        time.sleep(RETRY_WAIT * attempt)
                        continue
                    raise LsApiError(f"HTTP {r.status_code}", category="http_5xx")
                if r.status_code == 429:
                    time.sleep(RETRY_WAIT * attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                rsp_cd  = data.get("rsp_cd", "")
                rsp_msg = data.get("rsp_msg", "")
                if rsp_cd != "00000":
                    cat = _classify_error(rsp_msg, r.status_code)
                    if cat == "no_data":
                        return {"_no_data": True, "rsp_msg": rsp_msg}
                    raise LsApiError(f"rsp_cd={rsp_cd} msg={rsp_msg}", category=cat)
                return data
            except _HardTimeout:
                if attempt < MAX_RETRY:
                    time.sleep(RETRY_WAIT)
                    continue
                raise LsApiError("hard timeout 25s", category="other")
            except requests.RequestException as e:
                if attempt < MAX_RETRY:
                    time.sleep(RETRY_WAIT)
                    continue
                raise LsApiError(str(e), category="other")
        raise LsApiError("max retries exceeded", category="other")

    def get_intraday_bars(self, code: str, target_date: date) -> tuple[list[dict], int]:
        """
        target_date 1일치 분봉 + interval_seconds 반환.
        - target_date >= START_30SEC: 30초봉 (ncnt=0, interval_seconds=30)
        - 그 이전: 1분봉 (ncnt=1, interval_seconds=60)

        반환: (bars, interval_seconds)
        """
        ncnt = select_ncnt(target_date)
        interval = 30 if ncnt == 0 else 60
        ymd = target_date.strftime("%Y%m%d")
        all_bars: list[dict] = []
        cts_date, cts_time = "", ""
        tr_cont, tr_cont_key = "N", ""
        seen_keys: set[tuple] = set()

        while True:
            data = self._post_t8452(
                shcode=code, ncnt=ncnt, qrycnt=500, edate=ymd,
                cts_date=cts_date, cts_time=cts_time,
                exchgubun="K", tr_cont=tr_cont, tr_cont_key=tr_cont_key,
            )
            if data.get("_no_data"):
                break
            bars = data.get("t8452OutBlock1", []) or []
            if not bars:
                break
            all_bars.extend(bars)
            out = data.get("t8452OutBlock", {}) or {}
            nd = (out.get("cts_date", "") or "").strip()
            nt = (out.get("cts_time", "") or "").strip()

            dates_in = {b.get("date") for b in bars}
            if ymd not in dates_in:
                break
            if not nd or nd < ymd:
                break
            key = (nd, nt)
            if key in seen_keys:
                break
            seen_keys.add(key)
            cts_date, cts_time = nd, nt
            tr_cont, tr_cont_key = "Y", f"{nd}{nt}"

        target_bars = [b for b in all_bars if b.get("date") == ymd and b.get("time")]
        dedup = {b["time"]: b for b in target_bars}
        return sorted(dedup.values(), key=lambda r: r["time"]), interval

    @staticmethod
    def t8452_to_db_row(stock_code: str, bar: dict, interval_seconds: int,
                        exchange: str = "K") -> Optional[dict]:
        """t8452 응답 봉 1개 → ohlcv_intraday INSERT row."""
        ymd = bar.get("date", "")
        hms = bar.get("time", "")
        if len(ymd) != 8 or not ymd.isdigit() or len(hms) < 6 or not hms[:6].isdigit():
            return None
        h, m, s = int(hms[0:2]), int(hms[2:4]), int(hms[4:6])
        ts = datetime(int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]), h, m, s, tzinfo=KST)
        close = bar.get("close")
        vol   = bar.get("jdiff_vol")
        val   = bar.get("value")
        if close is None or vol is None:
            return None
        try:
            tv = int(val) * 1_000_000 if val is not None else None
        except (TypeError, ValueError):
            tv = None
        return {
            "stock_code":       stock_code,
            "time":             ts,
            "exchange":         exchange,
            "interval_seconds": interval_seconds,
            "open":             bar.get("open"),
            "high":             bar.get("high"),
            "low":              bar.get("low"),
            "close":            close,
            "volume":           vol,
            "trading_value":    tv,
        }


# ── 30초봉 가용 시작점 (실측 — 모듈 레벨 상수) ────────────────────
START_30SEC = date(2026, 4, 27)


def select_ncnt(target_date: date) -> int:
    """target_date에 따른 ncnt 분기. 30초봉 가용 이후 = 0, 그 이전 = 1."""
    return 0 if target_date >= START_30SEC else 1


# ── (보존) t8412: 주식차트(N분) — 검증 자료, 운영 미사용 ──────────────
class _DeprecatedT8412:
    """이하 t8412 메서드는 검증 자료로 보존 — 운영은 t8452 사용."""

    def _post_t8412(self, shcode: str, ncnt: int, qrycnt: int, edate: str,
                    cts_date: str = "", cts_time: str = "",
                    tr_cont: str = "N", tr_cont_key: str = "") -> dict:
        url = f"{BASE_URL}/stock/chart"
        token = self._get_token()
        headers = {
            "content-type":  "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "tr_cd":         "t8412",
            "tr_cont":       tr_cont,
            "tr_cont_key":   tr_cont_key,
            "mac_address":   "",
            "Connection":    "close",
        }
        body = {
            "t8412InBlock": {
                "shcode":   shcode,
                "ncnt":     ncnt,
                "qrycnt":   qrycnt,
                "nday":     "0",
                "sdate":    "",
                "stime":    "",
                "edate":    edate,
                "etime":    "",
                "cts_date": cts_date,
                "cts_time": cts_time,
                "comp_yn":  "N",   # 압축은 LS 서버가 JSON으로 잘못 인코딩 (사용 불가)
            }
        }
        for attempt in range(1, MAX_RETRY + 1):
            self._throttle()
            self._refresh_session()  # 매 호출마다 새 TCP — CLOSE_WAIT 누적 방지
            try:
                with hard_timeout(25):  # OS-level kill switch (LS 부하 케이스 16초 응답 커버)
                    r = self.session.post(url, json=body, headers=headers, timeout=(10, 30))
                if r.status_code >= 500:
                    if attempt < MAX_RETRY:
                        time.sleep(RETRY_WAIT * attempt)
                        continue
                    raise LsApiError(f"HTTP {r.status_code}", category="http_5xx")
                if r.status_code == 429:
                    time.sleep(RETRY_WAIT * attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                rsp_cd  = data.get("rsp_cd", "")
                rsp_msg = data.get("rsp_msg", "")
                if rsp_cd != "00000":
                    cat = _classify_error(rsp_msg, r.status_code)
                    if cat == "no_data":
                        return {"_no_data": True, "rsp_msg": rsp_msg}
                    raise LsApiError(f"rsp_cd={rsp_cd} msg={rsp_msg}", category=cat)
                return data
            except requests.Timeout:
                if attempt < MAX_RETRY:
                    time.sleep(RETRY_WAIT)
                    continue
                raise LsApiError("timeout", category="other")
            except _HardTimeout:
                if attempt < MAX_RETRY:
                    time.sleep(RETRY_WAIT)
                    continue
                raise LsApiError("hard timeout 15s (CLOSE-WAIT 회피)", category="other")
            except requests.RequestException as e:
                if attempt < MAX_RETRY:
                    time.sleep(RETRY_WAIT)
                    continue
                raise LsApiError(str(e), category="other")
        raise LsApiError("max retries exceeded", category="other")

    def get_30sec_bars_t8412(self, code: str, target_date: date,
                             qrycnt: int = 500) -> list[dict]:
        """
        t8412 (ncnt=0)로 target_date 1일치 30초봉 반환.
        edate=target_date 로 시작 → 페이징 거꾸로 거슬러 → target_date 봉만 필터.
        반환: [{"date":"YYYYMMDD","time":"HHMMSS","open","high","low","close",
                "jdiff_vol"(거래량), "value"(거래대금 백만원), ...}, ...]
        """
        ymd = target_date.strftime("%Y%m%d")
        all_bars: list[dict] = []
        cts_date, cts_time = "", ""
        tr_cont, tr_cont_key = "N", ""
        seen_keys: set[tuple] = set()

        while True:
            data = self._post_t8412(
                shcode=code, ncnt=0, qrycnt=qrycnt, edate=ymd,
                cts_date=cts_date, cts_time=cts_time,
                tr_cont=tr_cont, tr_cont_key=tr_cont_key,
            )
            if data.get("_no_data"):
                break
            bars = data.get("t8412OutBlock1", []) or []
            if not bars:
                break
            all_bars.extend(bars)
            out = data.get("t8412OutBlock", {}) or {}
            nd = (out.get("cts_date", "") or "").strip()
            nt = (out.get("cts_time", "") or "").strip()

            dates_in = {b.get("date") for b in bars}
            # target 거래일 봉을 모두 받았다고 판단되면 종료
            #  - 응답 안에 target_date 봉이 없거나 (이전 거래일 영역으로 넘어감)
            #  - cts_date가 target_date보다 이른 날짜이거나 비어있으면 종료
            if ymd not in dates_in:
                break
            if not nd or nd < ymd:
                break
            key = (nd, nt)
            if key in seen_keys:
                break
            seen_keys.add(key)
            cts_date, cts_time = nd, nt
            tr_cont, tr_cont_key = "Y", f"{nd}{nt}"

        # target 거래일만 필터 + 시간순 정렬
        target_bars = [b for b in all_bars if b.get("date") == ymd and b.get("time")]
        dedup = {b["time"]: b for b in target_bars}
        return sorted(dedup.values(), key=lambda r: r["time"])

    @staticmethod
    def t8412_to_db_row(stock_code: str, bar: dict) -> Optional[dict]:
        """
        t8412 응답 봉 → ohlcv_30sec INSERT row.
        - DB.volume        = bar.jdiff_vol (봉 단위)
        - DB.trading_value = bar.value × 1_000_000 (백만원 → 원)
        - DB.time          = bar.date + bar.time (KST)
        매도/매수 분리 거래량은 t8412에 없음 → mdvolume/msvolume NULL
        """
        ymd  = bar.get("date", "")
        hms  = bar.get("time", "")
        if len(ymd) != 8 or not ymd.isdigit() or len(hms) < 6 or not hms[:6].isdigit():
            return None
        h, m, s = int(hms[0:2]), int(hms[2:4]), int(hms[4:6])
        ts = datetime(int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]), h, m, s, tzinfo=KST)
        close = bar.get("close")
        vol   = bar.get("jdiff_vol")
        val   = bar.get("value")
        if close is None or vol is None:
            return None
        try:
            tv = int(val) * 1_000_000 if val is not None else None
        except (TypeError, ValueError):
            tv = None
        return {
            "stock_code":    stock_code,
            "time":          ts,
            "open":          bar.get("open"),
            "high":          bar.get("high"),
            "low":           bar.get("low"),
            "close":         close,
            "volume":        vol,
            "trading_value": tv,
            "mdvolume":      None,
            "msvolume":      None,
        }

    # ── (보존) t1302 30초봉 — 검증 자료, 운영 미사용 ──────────────
    def get_30sec_bars(self, code: str, target_date: date,
                       cnt: int = 900) -> list[dict]:
        """
        target_date 의 30초봉 전체 list 반환.
        cts_time 페이징: 응답에서 더 이상 cts_time 없거나 빈 문자열이면 종료.
        ymd 필터: chetime은 HHMMSS만 주므로 호출 시점 기준 오늘 또는 인자 날짜로 가정 — 실제로는 시점 검증을 호출자가 보강.

        반환: [{"chetime", "open", "high", "low", "close", "volume", "cvolume",
                 "mdvolume", "msvolume", "chdegree", "totofferrem", "totbidrem", ...}, ...]
        시간순 정렬 (chetime 오름차순) 보장.
        """
        all_rows: list[dict] = []
        time_str = ""           # 첫 호출은 빈 문자열
        tr_cont = "N"
        tr_cont_key = ""
        seen_cts: set[str] = set()

        while True:
            data = self._post_t1302(
                shcode=code, gubun="0", time_str=time_str, cnt=cnt,
                exchgubun="K", tr_cont=tr_cont, tr_cont_key=tr_cont_key,
            )
            if data.get("_no_data"):
                break

            rows = data.get("t1302OutBlock1", []) or []
            all_rows.extend(rows)

            cts = (data.get("t1302OutBlock") or {}).get("cts_time", "").strip()
            if not cts or cts in seen_cts:
                break
            seen_cts.add(cts)
            time_str    = cts
            tr_cont     = "Y"
            tr_cont_key = cts

        # 중복 제거 + 시간순 정렬
        dedup = {r.get("chetime"): r for r in all_rows if r.get("chetime")}
        return sorted(dedup.values(), key=lambda r: r["chetime"])

    @staticmethod
    def chetime_to_ts(target_date: date, chetime: str) -> Optional[datetime]:
        """HHMMSS → KST datetime"""
        if not chetime or len(chetime) != 6 or not chetime.isdigit():
            return None
        h, m, s = int(chetime[0:2]), int(chetime[2:4]), int(chetime[4:6])
        return datetime(target_date.year, target_date.month, target_date.day, h, m, s, tzinfo=KST)

    @staticmethod
    def to_db_row(stock_code: str, target_date: date, bar: dict) -> Optional[dict]:
        """
        t1302 응답 봉 1개 → ohlcv_30sec INSERT row.

        매핑:
        - DB.volume = t1302.cvolume (봉 단위 거래량 — 첫 실측에서 의미 확정)
        - DB.trading_value = close × cvolume 추정
        - chetime → KST timestamp
        - close/open/high/low: 그대로
        - mdvolume/msvolume: 그대로 (봉 단위)

        반환 None: chetime 파싱 실패 또는 필수 필드 누락
        """
        ts = LsApiClient.chetime_to_ts(target_date, bar.get("chetime", ""))
        if ts is None:
            return None
        cvol  = bar.get("cvolume")
        close = bar.get("close")
        if cvol is None or close is None:
            return None
        try:
            tv = int(round(float(close) * int(cvol)))
        except (TypeError, ValueError):
            tv = None
        return {
            "stock_code":    stock_code,
            "time":          ts,
            "open":          bar.get("open"),
            "high":          bar.get("high"),
            "low":           bar.get("low"),
            "close":         close,
            "volume":        cvol,
            "trading_value": tv,
            "mdvolume":      bar.get("mdvolume"),
            "msvolume":      bar.get("msvolume"),
        }
