"""
Infomax API 수집기
- /api/stock/hist    → ohlcv_daily, market_cap_daily
- /api/stock/investor → investor_trading
"""

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

# 투자자 API 코드 → DB investor_type 매핑
# ※ API는 '연기금' 대신 '기금공제'로 반환함 (실측 확인)
INVESTOR_MAP = {
    "외국인": "FOREIGN",
    "기관계": "INSTITUTION",
    "기금공제": "PENSION",   # 연기금 = 기금공제
    "개인":    "RETAIL",
}


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
    def get_investor(self, code: str,
                     start: date, end: date) -> list[dict]:
        """
        4개 투자자 유형 수급 한 번에 조회 (investor 미입력 = 전체 반환)
        반환: [{"date", "stock_code", "investor_type",
                "net_buy_value", "net_buy_volume"}, ...]
        """
        params = {
            "code":      code,
            "startDate": start.strftime("%Y%m%d"),
            "endDate":   end.strftime("%Y%m%d"),
        }
        data = self._get("/api/stock/investor", params)
        if not data:
            return []

        rows = []
        for r in data.get("results", []):
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
                "net_buy_value":  bid_val - ask_val,
                "net_buy_volume": bid_vol - ask_vol,
            })
        return rows

    # ── 현재 상장 종목 목록 (/api/stock/code) ──────────────────────────────
    def get_stock_codes(self) -> list[dict]:
        """
        현재 상장 종목 목록 전체 조회
        반환: [{"code", "name", "market", "listing_date", "standard_code"}, ...]

        API 응답 필드: code, kr_name, market, equity_type, isin, listed_date
        """
        data = self._get("/api/stock/code", {})
        if not data:
            return []

        # market 숫자코드 → DB 문자열 변환
        # API: 1=거래소(KOSPI), 2=거래소기타, 5=KRX, 7=코스닥, 8=코스닥기타
        MARKET_MAP = {"1": "KOSPI", "2": "KOSPI", "5": "KOSPI",
                      "7": "KOSDAQ", "8": "KOSDAQ"}

        rows = []
        for r in data.get("results", []):
            code = r.get("code")
            if not code:
                continue
            mkt_raw = str(r.get("market", ""))
            market  = MARKET_MAP.get(mkt_raw, mkt_raw.upper() if mkt_raw else None)
            # equity_type EF/EN/MF/RT → ETF로 통합
            eq_type = r.get("equity_type", "")
            if eq_type and eq_type not in ("ST",):
                market = "ETF"
            rows.append({
                "code":          str(code).strip(),
                "name":          r.get("kr_name", ""),
                "market":        market,
                "listing_date":  self._parse_date(r.get("listed_date")),
                "standard_code": r.get("isin"),
            })
        return rows

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

    @staticmethod
    def _parse_date(val) -> Optional[date]:
        if val is None:
            return None
        try:
            return datetime.strptime(str(val), "%Y%m%d").date()
        except (ValueError, TypeError):
            return None
