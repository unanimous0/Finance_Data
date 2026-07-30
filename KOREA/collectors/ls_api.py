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

# ── 네트워크/DNS 계층 실패 전용 재시도 (MAX_RETRY/RETRY_WAIT와 별도 예산) ──
# 배경: 2026-07-29 02:02 KST Tailscale MagicDNS(100.100.100.100) 플래핑으로
#   openapi.ls-sec.co.kr 이름 해석이 ~1.5분간 실패 → daily_update가 죽어
#   7/28 데이터가 통째로 결손됐다(익일 자동 복구됨). 기존 RequestException
#   재시도는 3회 × 10s ≈ 30s라 분 단위 DNS 장애를 못 넘긴다.
# ConnectionError는 "요청이 아직 나가지도 못한" 상태 = LS 서버는 무죄이므로
#   길게 기다려 재시도하는 게 맞다. 5xx/429/timeout 정책은 건드리지 않는다.
CONN_ERR_MAX_RETRY = 5
CONN_ERR_WAITS = (15.0, 30.0, 60.0, 120.0, 180.0)   # 호출당 누적 최대 ~6.4분
# 장애가 blip이 아니라 지속형일 때의 안전장치: 긴 대기는 프로세스 전체에서
# 이 예산(초)만 쓴다. 소진되면 즉시 기존 짧은 retry 정책으로 fail-fast.
# (workers=4 × 3,900종목이 각자 6.4분씩 기다리면 배치가 며칠이 된다)
CONN_ERR_TOTAL_BUDGET_SEC = 900.0                   # 15분
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
    """signal.alarm으로 강제 timeout.
    signal handler는 메인 스레드에서만 설치 가능 → worker thread(APScheduler 등)에서는 no-op.
    그 경우 requests 자체 timeout만 의존."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
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

    # 연결 실패(DNS/네트워크) 긴 재시도의 전역 대기 예산 (프로세스 단위)
    _conn_budget_lock = threading.Lock()
    _conn_wait_left   = CONN_ERR_TOTAL_BUDGET_SEC

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

    def _claim_conn_wait(self, seconds: float) -> bool:
        """긴 연결-재시도 대기를 전역 예산에서 차감. 남았으면 True.
        worker 스레드 여럿이 동시에 호출하므로 클래스 lock으로 보호."""
        with LsApiClient._conn_budget_lock:
            if LsApiClient._conn_wait_left < seconds:
                return False
            LsApiClient._conn_wait_left -= seconds
            return True

    def _post_resilient(self, url: str, *, hard_sec: int, timeout,
                        headers: dict, json=None, data=None):
        """session.post + **ConnectionError만** 긴 백오프로 재시도.

        DNS/네트워크 계층 실패는 요청이 나가지도 못한 상태라 LS 서버 부하와
        무관하다 → 짧은 retry로 포기하지 말고 분 단위로 기다린다.
        (2026-07-29 DNS 플래핑 사고 대응. 상수 주석 참조)

        - sleep은 hard_timeout 컨텍스트 **밖**에서 수행 (안에서 자면 즉사)
        - _HardTimeout / 그 외 RequestException은 그대로 상위로 전파 →
          기존 5xx/429/timeout 재시도 정책 그대로 유지
        - 전역 예산 소진 시 즉시 raise → 지속형 장애에서 배치가 늘어지지 않음
        """
        last_err = None
        for i in range(CONN_ERR_MAX_RETRY + 1):
            try:
                with hard_timeout(hard_sec):
                    return self.session.post(url, json=json, data=data,
                                             headers=headers, timeout=timeout)
            except requests.ConnectionError as e:
                last_err = e
                if i >= CONN_ERR_MAX_RETRY:
                    break
                wait = CONN_ERR_WAITS[min(i, len(CONN_ERR_WAITS) - 1)]
                if not self._claim_conn_wait(wait):
                    print(f"    [연결 실패] 전역 대기 예산 소진 — 긴 재시도 중단 "
                          f"(지속형 장애 의심)", flush=True)
                    break
                print(f"    [연결 실패] {type(e).__name__} — {wait:.0f}s 후 재시도 "
                      f"({i + 1}/{CONN_ERR_MAX_RETRY})", flush=True)
                time.sleep(wait)
                self._refresh_session()   # 끊긴 소켓/스테일 커넥션 정리
        raise last_err

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
        r = self._post_resilient(url, hard_sec=15, timeout=(10, 30),
                                 headers=headers, data=payload)
        r.raise_for_status()
        data = r.json()
        if "access_token" not in data:
            raise LsApiError(f"토큰 발급 실패: {data}", category="other")
        return data["access_token"]

    def _invalidate_token(self):
        """401 받았을 때 호출 — 다음 _get_token이 새로 발급."""
        with LsApiClient._token_lock:
            LsApiClient._token_value    = None
            LsApiClient._token_expires  = 0.0
            LsApiClient._token_call_cnt = 0

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
                r = self._post_resilient(url, hard_sec=10, timeout=(5, 15),
                                         headers=headers, json=body)  # OS-level kill switch (5xx 빠른 fail → retry로 다음 호출로)
                if r.status_code >= 500:
                    # 5xx 본문 로깅 + IGW00121(token invalid) 자동 처리
                    body_snippet = (r.text or "")[:200].replace("\n", " ")
                    print(f"    [5xx debug] status={r.status_code} body={body_snippet!r}", flush=True)
                    if "IGW00121" in body_snippet:
                        # token invalid (LENS와 LS 계정 공유로 인한 무효화 등) → invalidate + 재발급 + retry
                        self._invalidate_token()
                        if attempt < MAX_RETRY:
                            token = self._get_token()
                            headers["authorization"] = f"Bearer {token}"
                            continue
                        raise LsApiError("IGW00121 token invalid (refresh exhausted)", category="other")
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
                r = self._post_resilient(url, hard_sec=10, timeout=(5, 15),
                                         headers=headers, json=body)
                if r.status_code == 401:
                    # 다른 프로세스가 토큰 발급해서 무효화된 케이스 — 재발급 후 retry
                    self._invalidate_token()
                    if attempt < MAX_RETRY:
                        token = self._get_token()
                        headers["authorization"] = f"Bearer {token}"
                        continue
                    raise LsApiError("HTTP 401 (token refresh attempts exhausted)", category="other")
                if r.status_code >= 500:
                    # 5xx 본문 로깅 + IGW00121(token invalid) 자동 처리
                    body_snippet = (r.text or "")[:200].replace("\n", " ")
                    print(f"    [5xx debug] status={r.status_code} body={body_snippet!r}", flush=True)
                    if "IGW00121" in body_snippet:
                        # token invalid (LENS와 LS 계정 공유로 인한 무효화 등) → invalidate + 재발급 + retry
                        self._invalidate_token()
                        if attempt < MAX_RETRY:
                            token = self._get_token()
                            headers["authorization"] = f"Bearer {token}"
                            continue
                        raise LsApiError("IGW00121 token invalid (refresh exhausted)", category="other")
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

    # ── 공통 helper: TR 호출 (401 자동 refresh + retry) ────────────────────
    def _post_generic(self, tr_cd: str, url: str, in_block_name: str,
                      in_block: dict) -> dict:
        """t8418/t8465/t8406/t8401 등 새 TR용 일반화 POST.
        401 시 자동 token refresh + retry. 5xx/429/timeout 동일 정책."""
        token = self._get_token()
        headers = {
            "content-type":  "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "tr_cd":         tr_cd,
            "tr_cont":       "N",
            "tr_cont_key":   "",
            "mac_address":   "",
            "Connection":    "close",
        }
        body = {in_block_name: in_block}
        for attempt in range(1, MAX_RETRY + 1):
            self._throttle()
            self._refresh_session()
            try:
                r = self._post_resilient(url, hard_sec=10, timeout=(5, 15),
                                         headers=headers, json=body)
                if r.status_code == 401:
                    self._invalidate_token()
                    if attempt < MAX_RETRY:
                        token = self._get_token()
                        headers["authorization"] = f"Bearer {token}"
                        continue
                    raise LsApiError("HTTP 401 (token refresh exhausted)", category="other")
                if r.status_code >= 500:
                    # 5xx 본문 로깅 + IGW00121(token invalid) 자동 처리
                    body_snippet = (r.text or "")[:200].replace("\n", " ")
                    print(f"    [5xx debug] status={r.status_code} body={body_snippet!r}", flush=True)
                    if "IGW00121" in body_snippet:
                        # token invalid (LENS와 LS 계정 공유로 인한 무효화 등) → invalidate + 재발급 + retry
                        self._invalidate_token()
                        if attempt < MAX_RETRY:
                            token = self._get_token()
                            headers["authorization"] = f"Bearer {token}"
                            continue
                        raise LsApiError("IGW00121 token invalid (refresh exhausted)", category="other")
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

    # ── t8418: 업종/지수 N분차트 ─────────────────────────────────────────
    def get_index_intraday_bars(self, shcode: str, target_date: date,
                                ncnt: int = 0) -> list[dict]:
        """target_date 1일치 지수 N분봉. ncnt=0(30초) | 1(1분).
        반환: [{"date","time","open","high","low","close","jdiff_vol","value"}, ...]
        지수는 종목과 달리 페이징 거의 불필요 — 단일 호출 + 응답 cts_date 페이징 fallback."""
        url = f"{BASE_URL}/indtp/chart"
        ymd = target_date.strftime("%Y%m%d")
        all_bars: list[dict] = []
        cts_date, cts_time = "", ""
        seen_keys: set[tuple] = set()

        while True:
            in_block = {
                "shcode": shcode, "ncnt": ncnt, "qrycnt": 500, "nday": "0",
                "sdate": "", "stime": "", "edate": ymd, "etime": "",
                "cts_date": cts_date, "cts_time": cts_time, "comp_yn": "N"
            }
            data = self._post_generic("t8418", url, "t8418InBlock", in_block)
            if data.get("_no_data"):
                break
            bars = data.get("t8418OutBlock1", []) or []
            if not bars:
                break
            all_bars.extend(bars)
            out = data.get("t8418OutBlock", {}) or {}
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

        target_bars = [b for b in all_bars if b.get("date") == ymd and b.get("time")]
        dedup = {b["time"]: b for b in target_bars}
        return sorted(dedup.values(), key=lambda r: r["time"])

    # ── t8465: 선물옵션 N분차트 (t8415 신 TR, 5/28 deprecate 대비) ─────
    def get_futures_intraday_bars(self, shcode: str, target_date: date,
                                  ncnt: int = 0) -> list[dict]:
        """target_date 1일치 선물 N분봉. ncnt=0(30초) | 1(1분).
        반환: [{"date","time","open","high","low","close","jdiff_vol","value","openyak"}, ...]"""
        url = f"{BASE_URL}/futureoption/chart"
        ymd = target_date.strftime("%Y%m%d")
        all_bars: list[dict] = []
        cts_date, cts_time = "", ""
        seen_keys: set[tuple] = set()

        while True:
            in_block = {
                "shcode": shcode, "ncnt": ncnt, "qrycnt": 500, "nday": "0",
                "sdate": "", "stime": "", "edate": ymd, "etime": "",
                "cts_date": cts_date, "cts_time": cts_time, "comp_yn": "N"
            }
            data = self._post_generic("t8465", url, "t8465InBlock", in_block)
            if data.get("_no_data"):
                break
            bars = data.get("t8465OutBlock1", []) or []
            if not bars:
                break
            all_bars.extend(bars)
            out = data.get("t8465OutBlock", {}) or {}
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

        target_bars = [b for b in all_bars if b.get("date") == ymd and b.get("time")]
        dedup = {b["time"]: b for b in target_bars}
        return sorted(dedup.values(), key=lambda r: r["time"])

    # ── t8451: 주식 일/주/월/년 차트 (sujung=Y/N 수정주가 지원) ──────────
    def get_daily_bars(self, shcode: str, sdate: date = None, edate: date = None,
                       sujung: str = "Y", exchgubun: str = "K") -> list[dict]:
        """주식 일봉 (sujung=Y → 수정주가, N → raw). 4년치 한 호출에 받기 가능 (qrycnt 큰값).
        반환: [{"date","open","high","low","close","jdiff_vol","value",...}, ...] (date 오름차순)
        sdate/edate 모두 date 객체. None이면 max 범위. cts_date 페이징으로 무제한."""
        url = f"{BASE_URL}/stock/chart"
        sdate_s = sdate.strftime("%Y%m%d") if sdate else ""
        edate_s = edate.strftime("%Y%m%d") if edate else "99999999"
        all_bars: list[dict] = []
        cts_date = ""
        seen_cts: set[str] = set()

        while True:
            in_block = {
                "shcode": shcode, "gubun": "2", "qrycnt": 2000,
                "sdate": sdate_s, "edate": edate_s,
                "cts_date": cts_date, "comp_yn": "N",
                "sujung": sujung, "exchgubun": exchgubun,
            }
            data = self._post_generic("t8451", url, "t8451InBlock", in_block)
            if data.get("_no_data"):
                break
            bars = data.get("t8451OutBlock1", []) or []
            if not bars:
                break
            all_bars.extend(bars)
            out = data.get("t8451OutBlock", {}) or {}
            nd = (out.get("cts_date", "") or "").strip()
            if not nd or nd in seen_cts:
                break
            seen_cts.add(nd)
            cts_date = nd
            if sdate_s and nd <= sdate_s:
                break

        # dedup by date, sort ascending
        dedup = {b["date"]: b for b in all_bars if b.get("date")}
        # LS t8451이 sdate를 자주 무시하고 edate 기준 과거로 ~500 bar 반환 →
        # 클라이언트 측에서 sdate ~ edate 범위로 명시적 filter (1일 query에 500 bar 적재 방지)
        if sdate_s:
            dedup = {d: b for d, b in dedup.items() if d >= sdate_s}
        if edate_s and edate_s != "99999999":
            dedup = {d: b for d, b in dedup.items() if d <= edate_s}
        return sorted(dedup.values(), key=lambda r: r["date"])

    # ── t8406: 주식선물 분차트 (당일만 — historical 불가) ───────────────
    def get_stockfut_today_bars(self, focode: str, bgubun: int = 0) -> list[dict]:
        """주식선물 분봉 (오늘 1일치만). bgubun=0(30초) | 1(1분) | 2/30/60(N분).
        반환: [{"chetime","price","open","high","low","close","cvolume","volume","value","openyak"}, ...]
        chetime = HHMMSS, OHLC는 분 봉 단위 (cgubun='M').
        cnt=900로 1일 전체 충분히 수신 (장 9:00~15:30 = 780개)."""
        url = f"{BASE_URL}/futureoption/market-data"
        in_block = {"focode": focode, "cgubun": "M", "bgubun": bgubun, "cnt": 900}
        data = self._post_generic("t8406", url, "t8406InBlock", in_block)
        if data.get("_no_data"):
            return []
        rows = data.get("t8406OutBlock1", []) or []
        # 시간순 정렬 (응답은 최신→과거 순)
        return sorted(rows, key=lambda r: r.get("chetime", ""))

    # ── t8401: 주식선물 마스터 ───────────────────────────────────────────
    def get_stockfut_master(self) -> list[dict]:
        """t8401 — 주식선물 전체 마스터.
        반환: [{"hname","shcode","expcode","basecode"}, ...]
        shcode = LS 주식선물 코드 (8자), basecode = 기초자산 종목코드 (A + 6자)."""
        url = f"{BASE_URL}/futureoption/market-data"
        data = self._post_generic("t8401", url, "t8401InBlock", {"dummy": ""})
        if data.get("_no_data"):
            return []
        return data.get("t8401OutBlock", []) or []

    # ── 변환 helper ────────────────────────────────────────────────────────
    @staticmethod
    def index_bar_to_db_row(index_code: str, bar: dict, interval_seconds: int) -> Optional[dict]:
        """t8418 응답 bar → index_ohlcv_intraday INSERT row."""
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
            "index_code":       index_code,
            "time":             ts,
            "interval_seconds": interval_seconds,
            "open":             bar.get("open"),
            "high":             bar.get("high"),
            "low":              bar.get("low"),
            "close":            close,
            "volume":           vol,
            "trading_value":    tv,
        }

    @staticmethod
    def futures_bar_to_db_row(futures_code: str, bar: dict, interval_seconds: int) -> Optional[dict]:
        """t8465/t8406 응답 bar → futures_ohlcv_intraday INSERT row.
        t8465 입력: date+time/OHLC/jdiff_vol/value/openyak
        t8406 입력: chetime + price/open/high/low + cvolume/volume/value/openyak — 변환 처리"""
        # 1) t8465 형식 (date + time)
        if "date" in bar and "time" in bar:
            ymd = bar.get("date", "")
            hms = bar.get("time", "")
            if len(ymd) != 8 or not ymd.isdigit() or len(hms) < 6 or not hms[:6].isdigit():
                return None
            h, m, s = int(hms[0:2]), int(hms[2:4]), int(hms[4:6])
            ts = datetime(int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]), h, m, s, tzinfo=KST)
            close = bar.get("close")
            vol   = bar.get("jdiff_vol")
            val   = bar.get("value")
            openyak = bar.get("openyak")
            if close is None or vol is None:
                return None
        else:
            # 2) t8406 형식 (chetime, target date 호출자가 지정)
            return None  # 별도 helper로 변환

        try:
            tv = int(val) * 1_000_000 if val is not None else None
        except (TypeError, ValueError):
            tv = None
        return {
            "futures_code":     futures_code,
            "time":             ts,
            "interval_seconds": interval_seconds,
            "open":             bar.get("open"),
            "high":             bar.get("high"),
            "low":              bar.get("low"),
            "close":            close,
            "volume":           vol,
            "trading_value":    tv,
            "open_interest":    openyak,
        }

    @staticmethod
    def stockfut_t8406_to_db_row(focode: str, bar: dict, target_date: date,
                                 interval_seconds: int) -> Optional[dict]:
        """t8406 응답 bar → futures_ohlcv_intraday INSERT row.
        bar의 chetime은 HHMMSS (날짜 정보 없음) → target_date와 결합.
        OHLC는 cgubun='M' bgubun=0 일 때 봉 단위 OHLC 제공."""
        chetime = bar.get("chetime", "")
        if len(chetime) < 6 or not chetime[:6].isdigit():
            return None
        h, m, s = int(chetime[0:2]), int(chetime[2:4]), int(chetime[4:6])
        ts = datetime(target_date.year, target_date.month, target_date.day, h, m, s, tzinfo=KST)
        # t8406은 첫 봉이 하루 누적 vol/value 보임 (현재가 봉) → 봉 내 vol = cvolume
        cvol = bar.get("cvolume", 0)
        # t8406은 OHLC가 이미 봉 단위
        close = bar.get("close")
        if close in (None, 0):
            close = bar.get("price")
        try:
            cvol_int = int(cvol) if cvol is not None else 0
        except (TypeError, ValueError):
            cvol_int = 0
        # trading_value: t8406 value는 누적 — 우선 None (LP 봉 단위 검증 어려움)
        return {
            "futures_code":     focode,
            "time":             ts,
            "interval_seconds": interval_seconds,
            "open":             bar.get("open"),
            "high":             bar.get("high"),
            "low":              bar.get("low"),
            "close":            close,
            "volume":           cvol_int,
            "trading_value":    None,  # t8406은 누적이라 봉 단위 변환 불가
            "open_interest":    bar.get("openyak"),
        }

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


# ── 만기 식별: 근월 + 다음월물 ────────────────────────────────────────
def _parse_expiry_yyyymm(hname: str) -> Optional[date]:
    """hname의 마지막 숫자 토큰 (YYMM 또는 YYYYMM) → 만기일 (해당 월 두번째 목요일).
    예: 'TKG휴켐스 F 202605' → 2026-05-14 (5월 둘째 목)
        'F 2606' → 2026-06-11 (6월 둘째 목)"""
    from calendar import monthcalendar
    tokens = [t for t in hname.split() if t.isdigit()]
    if not tokens:
        return None
    exp = tokens[-1]
    if len(exp) == 6:
        try:
            yyyy, mm = int(exp[:4]), int(exp[4:])
        except ValueError:
            return None
    elif len(exp) == 4:
        try:
            yyyy, mm = 2000 + int(exp[:2]), int(exp[2:])
        except ValueError:
            return None
    else:
        return None
    try:
        # monthcalendar: 각 주가 [Mon..Sun] 7개. 목요일 idx=3
        thu_days = [w[3] for w in monthcalendar(yyyy, mm) if w[3] != 0]
        if len(thu_days) < 2:
            return None
        return date(yyyy, mm, thu_days[1])
    except Exception:
        return None


def select_near_next_two(master: list[dict], today: date,
                         group_key=None) -> list[dict]:
    """master에서 'F'(단일선물)만 + 만기 ≥ today, group별 근월+다음월물 2개씩.
    - group_key=None: 전체 1개 그룹
    - group_key=callable(m: dict)->str: 그룹별 분리 (예: 종목별)"""
    from collections import defaultdict
    parsed: list[tuple[str, date, dict]] = []
    for m in master:
        hname = m.get("hname", "")
        if "SP" in hname.split():   # 스프레드 제외 (토큰 매칭 — 'HPSP' 등 이름 내 'SP' 부분문자열 오탐 방지)
            continue
        # 옵션(콜/풋)은 hname이 'C 2605 ...', 'P 2605 ...' (가격 포함) — 첫 토큰 정확히 'C'/'P'
        first_tok = hname.strip().split()[0] if hname.strip() else ""
        if first_tok in ("C", "P"):
            continue
        exp = _parse_expiry_yyyymm(hname)
        if not exp or exp < today:
            continue
        key = group_key(m) if group_key else "_"
        parsed.append((key, exp, m))

    grouped: dict[str, list] = defaultdict(list)
    for k, exp, m in parsed:
        grouped[k].append((exp, m))
    out: list[dict] = []
    for k in grouped:
        grouped[k].sort(key=lambda x: x[0])
        out.extend(m for _, m in grouped[k][:2])
    return out


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
                r = self._post_resilient(url, hard_sec=10, timeout=(5, 15),
                                         headers=headers, json=body)  # OS-level kill switch (5xx 빠른 fail → retry로 다음 호출로)
                if r.status_code >= 500:
                    # 5xx 본문 로깅 + IGW00121(token invalid) 자동 처리
                    body_snippet = (r.text or "")[:200].replace("\n", " ")
                    print(f"    [5xx debug] status={r.status_code} body={body_snippet!r}", flush=True)
                    if "IGW00121" in body_snippet:
                        # token invalid (LENS와 LS 계정 공유로 인한 무효화 등) → invalidate + 재발급 + retry
                        self._invalidate_token()
                        if attempt < MAX_RETRY:
                            token = self._get_token()
                            headers["authorization"] = f"Bearer {token}"
                            continue
                        raise LsApiError("IGW00121 token invalid (refresh exhausted)", category="other")
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
