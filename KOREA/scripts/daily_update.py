"""
일별 데이터 업데이트 스크립트
대상: ohlcv_daily, market_cap_daily, investor_trading, foreign_ownership
실행: 매일 16:30 (schedulers/daily_scheduler.py 또는 단독 실행)

사용법:
    python scripts/daily_update.py           # 자동 날짜 감지
    python scripts/daily_update.py 20260220  # 특정 날짜 지정
"""

import sys
import time
import traceback
from pathlib import Path
from datetime import date, datetime, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings
from collectors.infomax import InfomaxClient
from validators.quality_checks import run_quality_checks

KST = ZoneInfo("Asia/Seoul")
REPORTS_DIR = project_root / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

# 병렬 처리 설정
# 공유 rate limiter(60회/분)를 N개 스레드가 나눠 쓰므로 rate limit 초과 없음.
# 네트워크 latency(~0.1초)와 throttle 대기를 오버랩해 처리율 향상.
MAX_WORKERS = 4

# 특이사항 임계값
THRESHOLD_PRICE_EVENT   = 0.30    # 가격 변동 30% 초과 = 한국 가격제한 초과 → 이벤트 확정 (수정계수 필요)
TOP_PRICE_CHANGE_COUNT  = 30      # 급등/급락 각각 상위 N개만 보고서에 표시
THRESHOLD_HALT_SUSPECT  = 3   # 거래량 0 연속 3~4일 → 거래정지 의심
THRESHOLD_HALT_CONFIRM  = 5   # 거래량 0 연속 5일 이상 → 거래정지 (스팩도 동일)
THRESHOLD_LARGE_NET_BUY = 5e10    # 순매수 500억 이상 (거액 유입/이탈)


# ── DB 연결 ───────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


# ── 휴장일 판정 (krx_holidays 테이블 SSoT) ──────────────────────────────
def is_market_closed(conn, d: date) -> bool:
    """주말 또는 krx_holidays 테이블에 등록된 날짜이면 True."""
    if d.weekday() >= 5:
        return True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM krx_holidays WHERE date = %s", (d,))
        return cur.fetchone() is not None


def last_business_day_on_or_before(conn, d: date) -> date:
    """d에서 거꾸로 가며 첫 영업일 반환."""
    while is_market_closed(conn, d):
        d -= timedelta(days=1)
    return d


# ── 날짜 결정 ─────────────────────────────────────────────────────────────
def get_update_range(conn) -> tuple[date, date]:
    """
    업데이트 범위 결정
    start: DB의 마지막 날짜 + 1일
    end:   어제 기준 마지막 영업일 (휴장일은 자동 skip)
    """
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(time) FROM ohlcv_daily")
        last_date = cur.fetchone()[0]

    if last_date is None:
        raise ValueError("ohlcv_daily에 데이터가 없습니다.")

    start = last_date + timedelta(days=1)
    yesterday = datetime.now(KST).date() - timedelta(days=1)
    end = last_business_day_on_or_before(conn, yesterday)
    return start, end


# ── 종목 목록 ─────────────────────────────────────────────────────────────
def get_stocks(conn, include_etf: bool = True) -> list[tuple[str, str]]:
    """(stock_code, stock_name) 리스트 반환"""
    with conn.cursor() as cur:
        if include_etf:
            cur.execute("""
                SELECT stock_code, stock_name
                FROM stocks
                WHERE is_active = TRUE
                ORDER BY stock_code
            """)
        else:
            cur.execute("""
                SELECT stock_code, stock_name
                FROM stocks
                WHERE is_active = TRUE
                  AND market IN ('KOSPI', 'KOSDAQ')
                ORDER BY stock_code
            """)
        return cur.fetchall()


def get_missing_ohlcv_stocks(conn, target_date: date) -> list[tuple[str, str]]:
    """target_date에 ohlcv_daily 데이터가 없는 활성 종목 목록"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.stock_code, s.stock_name
            FROM stocks s
            WHERE s.is_active = TRUE
              AND s.stock_code NOT IN (
                  SELECT stock_code FROM ohlcv_daily WHERE time = %s
              )
            ORDER BY s.stock_code
        """, (target_date,))
        return cur.fetchall()


def get_missing_investor_stocks(conn, target_date: date) -> list[tuple[str, str]]:
    """target_date에 investor_trading 데이터가 없는 KOSPI/KOSDAQ 종목 목록"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.stock_code, s.stock_name
            FROM stocks s
            WHERE s.is_active = TRUE
              AND s.market IN ('KOSPI', 'KOSDAQ')
              AND s.stock_code NOT IN (
                  SELECT DISTINCT stock_code FROM investor_trading WHERE time = %s
              )
            ORDER BY s.stock_code
        """, (target_date,))
        return cur.fetchall()


def get_foreign_stocks(conn) -> list[tuple[str, str]]:
    """외국인 지분율 수집 대상: ETF/SPAC 제외 활성 종목"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT stock_code, stock_name
            FROM stocks
            WHERE is_active = TRUE
              AND market IN ('KOSPI', 'KOSDAQ')
              AND stock_name NOT LIKE '%스팩%'
            ORDER BY stock_code
        """)
        return cur.fetchall()


def get_missing_foreign_stocks(conn, target_date: date) -> list[tuple[str, str]]:
    """target_date에 foreign_ownership 데이터가 없는 ETF/SPAC 제외 종목"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.stock_code, s.stock_name
            FROM stocks s
            WHERE s.is_active = TRUE
              AND s.market IN ('KOSPI', 'KOSDAQ')
              AND s.stock_name NOT LIKE '%%스팩%%'
              AND s.stock_code NOT IN (
                  SELECT stock_code FROM foreign_ownership WHERE time = %s
              )
            ORDER BY s.stock_code
        """, (target_date,))
        return cur.fetchall()


# ── 병렬 수집 worker (module-level, pickle 가능) ──────────────────────────
def _fetch_hist(client, code, name, start, end):
    rows = client.get_hist(code, start, end)
    return code, name, rows


def _fetch_investor(client, code, name, start, end):
    rows = client.get_investor(code, start, end)
    return code, name, rows


def _fetch_foreign(client, code, name, start, end):
    rows = client.get_foreign(code, start, end)
    return code, name, rows


# ── DB UPSERT ─────────────────────────────────────────────────────────────
OHLCV_SQL = """
INSERT INTO ohlcv_daily
    (time, stock_code, open_price, high_price, low_price, close_price, volume, trading_value)
VALUES %s
ON CONFLICT (time, stock_code) DO UPDATE SET
    open_price    = EXCLUDED.open_price,
    high_price    = EXCLUDED.high_price,
    low_price     = EXCLUDED.low_price,
    close_price   = EXCLUDED.close_price,
    volume        = EXCLUDED.volume,
    trading_value = EXCLUDED.trading_value
WHERE (ohlcv_daily.open_price, ohlcv_daily.high_price, ohlcv_daily.low_price,
       ohlcv_daily.close_price, ohlcv_daily.volume, ohlcv_daily.trading_value)
   IS DISTINCT FROM
      (EXCLUDED.open_price, EXCLUDED.high_price, EXCLUDED.low_price,
       EXCLUDED.close_price, EXCLUDED.volume, EXCLUDED.trading_value)
"""

MKTCAP_SQL = """
INSERT INTO market_cap_daily (time, stock_code, market_cap)
VALUES %s
ON CONFLICT (time, stock_code) DO UPDATE SET
    market_cap = EXCLUDED.market_cap
WHERE market_cap_daily.market_cap IS DISTINCT FROM EXCLUDED.market_cap
"""

INVESTOR_SQL = """
INSERT INTO investor_trading
    (time, stock_code, investor_type, net_buy_value, net_buy_volume)
VALUES %s
ON CONFLICT (time, stock_code, investor_type) DO UPDATE SET
    net_buy_value  = EXCLUDED.net_buy_value,
    net_buy_volume = EXCLUDED.net_buy_volume
WHERE (investor_trading.net_buy_value, investor_trading.net_buy_volume)
   IS DISTINCT FROM
      (EXCLUDED.net_buy_value, EXCLUDED.net_buy_volume)
"""

FOREIGN_SQL = """
INSERT INTO foreign_ownership
    (time, stock_code, frn_ownership_ratio, frn_ownership_vol, frn_limit_ratio)
VALUES %s
ON CONFLICT (time, stock_code) DO UPDATE SET
    frn_ownership_ratio = EXCLUDED.frn_ownership_ratio,
    frn_ownership_vol   = EXCLUDED.frn_ownership_vol,
    frn_limit_ratio     = EXCLUDED.frn_limit_ratio
WHERE (foreign_ownership.frn_ownership_ratio, foreign_ownership.frn_ownership_vol,
       foreign_ownership.frn_limit_ratio)
   IS DISTINCT FROM
      (EXCLUDED.frn_ownership_ratio, EXCLUDED.frn_ownership_vol,
       EXCLUDED.frn_limit_ratio)
"""


def upsert_batch(conn, sql: str, rows: list[tuple]) -> tuple[int, int]:
    """
    Returns: (changed_rows, total_rows)
    changed_rows: 실제 INSERT되거나 값이 달라서 UPDATE된 건수
    total_rows:   시도한 전체 건수 (changed + skipped)
    """
    if not rows:
        return 0, 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
        changed = cur.rowcount  # WHERE 조건 불만족(값 동일)은 카운트 안 됨
    conn.commit()
    return changed, len(rows)


# ── 특이사항 분석 ─────────────────────────────────────────────────────────
def analyze_anomalies(ohlcv_rows: list[dict],
                      investor_rows: list[dict],
                      prev_close: dict,
                      halt_suspects: dict) -> list[dict]:
    """
    특이사항 목록 반환
    각 항목: {"type", "stock_code", "stock_name", "date", "detail", "value"}

    halt_suspects: get_halt_suspects() 결과 {stock_code: (consecutive_days, close_price)}
                   연속 무거래일이 THRESHOLD_HALT_SUSPECT 미만인 종목은 포함되지 않음
    """
    anomalies = []
    price_changes = []  # 급등/급락 전체 수집 → TOP N만 anomalies에 추가

    # ── OHLCV 특이사항 ─────────────────────────────────────────
    for r in ohlcv_rows:
        code  = r["stock_code"]
        name  = r.get("stock_name", code)
        dt    = r["date"]
        close = r["close_price"] or 0
        high  = r["high_price"]  or 0
        low   = r["low_price"]   or 0
        vol   = r["volume"]      or 0

        # 거래량 0 → 연속일수 기반 분류
        # 1~2일은 소형주/ETF의 일상적 무거래일 수 있으므로 노이즈로 스킵
        if vol == 0 and close > 0 and code in halt_suspects:
            consec, _ = halt_suspects[code]
            is_spac = "스팩" in name
            if is_spac:
                # 스팩은 합병 전까지 장기 무거래가 정상 → 5일+ 이상만 별도 표시
                if consec >= THRESHOLD_HALT_CONFIRM:
                    anomalies.append({
                        "type": "무거래(스팩)",
                        "stock_code": code, "stock_name": name, "date": dt,
                        "detail": f"거래량=0 연속 {consec}일, 종가={close:,}원",
                        "value": consec,
                    })
            elif consec >= THRESHOLD_HALT_CONFIRM:
                anomalies.append({
                    "type": "거래정지",
                    "stock_code": code, "stock_name": name, "date": dt,
                    "detail": f"거래량=0 연속 {consec}일, 종가={close:,}원",
                    "value": consec,
                })
            else:  # THRESHOLD_HALT_SUSPECT <= consec < THRESHOLD_HALT_CONFIRM
                anomalies.append({
                    "type": "거래정지의심",
                    "stock_code": code, "stock_name": name, "date": dt,
                    "detail": f"거래량=0 연속 {consec}일, 종가={close:,}원",
                    "value": consec,
                })

        # OHLCV 논리 오류
        if high and low and high < low:
            anomalies.append({
                "type": "OHLCV오류",
                "stock_code": code, "stock_name": name, "date": dt,
                "detail": f"고가({high:,}) < 저가({low:,})",
                "value": high - low,
            })

        # 전일 대비 급등락 감지
        prev = prev_close.get(code)
        if prev and prev > 0 and close > 0:
            signed_rate = (close - prev) / prev
            chg_rate = abs(signed_rate)
            if chg_rate > THRESHOLD_PRICE_EVENT:
                # ±30% 초과: 한국 가격제한(±30%) 밖 → 정상 거래 불가능 → 이벤트 확정
                # (무상감자, 주식병합, 주식분할, 유상증자 권리락 등)
                direction = "상승" if signed_rate > 0 else "하락"
                event_hint = "주식병합/분할 의심" if signed_rate > 0 else "무상감자/대규모권리락 의심"
                anomalies.append({
                    "type": "주가이벤트의심",
                    "stock_code": code, "stock_name": name, "date": dt,
                    "detail": (
                        f"{direction} {chg_rate*100:.1f}%  "
                        f"{prev:,}원 → {close:,}원  "
                        f"({signed_rate*100:+.1f}%)  [{event_hint}]"
                    ),
                    "value": chg_rate,
                })
            else:
                price_changes.append({
                    "type": "가격급등" if signed_rate > 0 else "가격급락",
                    "stock_code": code, "stock_name": name, "date": dt,
                    "detail": f"전일종가={prev:,}원 → 당일종가={close:,}원 ({signed_rate*100:+.1f}%)",
                    "value": chg_rate,
                    "signed_rate": signed_rate,
                })

    # 급등/급락 TOP N만 anomalies에 추가
    rises = sorted([x for x in price_changes if x["signed_rate"] > 0], key=lambda x: x["value"], reverse=True)
    drops = sorted([x for x in price_changes if x["signed_rate"] < 0], key=lambda x: x["value"], reverse=True)
    for item in rises[:TOP_PRICE_CHANGE_COUNT] + drops[:TOP_PRICE_CHANGE_COUNT]:
        anomalies.append({k: v for k, v in item.items() if k != "signed_rate"})

    # ── 투자자 수급 특이사항 ────────────────────────────────────
    # 종목별 날짜별 순매수 집계
    net_by_stock = defaultdict(lambda: defaultdict(int))
    names = {}
    for r in investor_rows:
        code = r["stock_code"]
        dt   = r["date"]
        net_by_stock[code][dt] += (r["net_buy_value"] or 0)
        names[code] = r.get("stock_name", code)

    for code, dates in net_by_stock.items():
        for dt, net_total in dates.items():
            if abs(net_total) >= THRESHOLD_LARGE_NET_BUY:
                direction = "대규모순매수" if net_total > 0 else "대규모순매도"
                anomalies.append({
                    "type": direction,
                    "stock_code": code, "stock_name": names.get(code, code),
                    "date": dt,
                    "detail": f"전체투자자 순매수합계={net_total/1e8:+.1f}억원",
                    "value": abs(net_total),
                })

    return anomalies


# ── 종목 마스터 자동 갱신 ────────────────────────────────────────────────
def sync_stock_master(conn, client) -> dict:
    """
    STEP 0: 종목 마스터 갱신
    - /api/stock/code    → 신규 상장 종목 INSERT
    - /api/stock/expired → 상장폐지 종목 is_active=False UPDATE
    - 두 API 모두에 없는 DB 활성 종목 → is_active=False UPDATE (ETF 청산 등)

    Returns: {"new_listed": [...], "delisted": [...], "ghost_delisted": [...], "errors": [...]}
    """
    result = {"new_listed": [], "delisted": [], "ghost_delisted": [], "errors": []}

    # DB 전체 종목 코드 + 활성 여부
    with conn.cursor() as cur:
        cur.execute("SELECT stock_code, is_active FROM stocks")
        db_stocks = {row[0]: row[1] for row in cur.fetchall()}

    api_active_codes: set[str] = set()
    api_expired_codes: set[str] = set()

    # ── 신규 상장 종목 ─────────────────────────────────────────
    try:
        api_stocks = client.get_stock_codes()
        api_active_codes = {s["code"] for s in api_stocks if s["code"]}
        renamed: list[tuple[str, str]] = []
        for s in api_stocks:
            code = s["code"]
            if not code:
                continue
            if code in db_stocks:
                # 기존 — stock_name 변경 시만 UPDATE (사명 변경 반영)
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE stocks
                        SET stock_name = %s, updated_at = NOW()
                        WHERE stock_code = %s
                          AND stock_name IS DISTINCT FROM %s
                    """, (s["name"], code, s["name"]))
                    if cur.rowcount > 0:
                        renamed.append((code, s["name"]))
                conn.commit()
            else:
                # 신규
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO stocks
                            (stock_code, stock_name, market, standard_code, listing_date, is_active)
                        VALUES (%s, %s, %s, %s, %s, TRUE)
                        ON CONFLICT (stock_code) DO NOTHING
                    """, (code, s["name"], s["market"], s["standard_code"], s["listing_date"]))
                conn.commit()
                result["new_listed"].append(code)
        if renamed:
            result["renamed"] = renamed
    except Exception as e:
        result["errors"].append(f"신규 상장 조회 실패: {e}")

    # ── 상장폐지 종목 ──────────────────────────────────────────
    try:
        # DB 활성 종목 중 가장 오래된 상장일을 startDate로 사용
        # → API 기본값(today-365)보다 넓게 조회해 누락 방지
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MIN(listing_date) FROM stocks
                WHERE is_active = TRUE AND listing_date IS NOT NULL
            """)
            oldest_listing = cur.fetchone()[0]

        expired_start = oldest_listing if oldest_listing else date(2000, 1, 1)
        expired = client.get_expired_codes(start_date=expired_start)
        api_expired_codes = {s["code"] for s in expired if s["code"]}
        for s in expired:
            code = s["code"]
            if not code:
                continue
            # DB에 있고 현재 is_active=True인 경우만 처리
            if db_stocks.get(code) is True:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE stocks
                        SET is_active      = FALSE,
                            delisting_date = %s,
                            updated_at     = NOW()
                        WHERE stock_code = %s AND is_active = TRUE
                    """, (s["delisting_date"], code))
                conn.commit()
                result["delisted"].append(code)
    except Exception as e:
        result["errors"].append(f"상장폐지 조회 실패: {e}")

    # ── 두 API 모두에 없는 활성 종목 (ETF 청산 등) ────────────────
    # api_active_codes, api_expired_codes 둘 다 정상 수집된 경우에만 실행
    # 안전 장치: API 반환 종목 수가 DB 활성 종목의 90% 미만이면 스킵
    #   (API 일시 장애로 목록이 불완전하면 정상 종목을 오비활성화 방지)
    db_active_count = sum(1 for v in db_stocks.values() if v)
    api_coverage = len(api_active_codes) / db_active_count if db_active_count else 0
    if api_active_codes and api_expired_codes is not None and api_coverage >= 0.9:
        known_api_codes = api_active_codes | api_expired_codes
        for code, is_active in db_stocks.items():
            if is_active and code not in known_api_codes:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE stocks
                        SET is_active  = FALSE,
                            updated_at = NOW()
                        WHERE stock_code = %s AND is_active = TRUE
                    """, (code,))
                conn.commit()
                result["ghost_delisted"].append(code)

    return result


# ── 전일 종가 조회 ────────────────────────────────────────────────────────
def get_prev_close(conn, target_date: date) -> dict[str, float]:
    """target_date 직전 영업일의 종가 딕셔너리"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (stock_code)
                stock_code, close_price
            FROM ohlcv_daily
            WHERE time < %s
            ORDER BY stock_code, time DESC
        """, (target_date,))
        return {row[0]: row[1] for row in cur.fetchall()}


# ── 거래정지 의심 종목 조회 (연속 무거래일수) ─────────────────────────────
def get_halt_suspects(conn, target_date: date) -> dict[str, tuple[int, float]]:
    """
    target_date 기준 volume=0 연속일수 조회 (DB 직접 쿼리)
    반환: {stock_code: (consecutive_days, close_price)}
           consecutive_days: target_date 포함 연속 무거래 거래일 수
           90일 이상 연속이면 90으로 표시 (상한선)
    """
    with conn.cursor() as cur:
        cur.execute("""
            WITH recent AS (
                SELECT
                    stock_code,
                    volume,
                    close_price,
                    ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY time DESC) AS rn
                FROM ohlcv_daily
                WHERE time <= %s
                  AND time > %s - INTERVAL '90 days'
            ),
            zero_today AS (
                -- target_date(rn=1)에 volume=0인 종목만 추림
                SELECT stock_code, close_price
                FROM recent
                WHERE rn = 1 AND volume = 0 AND close_price > 0
            ),
            first_nonzero AS (
                -- 과거로 거슬러 올라가 첫 번째 volume>0 날의 rn을 찾음
                SELECT r.stock_code, MIN(r.rn) AS nonzero_rn
                FROM recent r
                JOIN zero_today z ON z.stock_code = r.stock_code
                WHERE r.volume > 0
                GROUP BY r.stock_code
            )
            SELECT
                z.stock_code,
                -- nonzero_rn=5 이면 rn 1~4가 연속 0 → 4일
                COALESCE(fn.nonzero_rn - 1, 90) AS consecutive_days,
                z.close_price
            FROM zero_today z
            LEFT JOIN first_nonzero fn ON fn.stock_code = z.stock_code
        """, (target_date, target_date))
        return {row[0]: (int(row[1]), float(row[2])) for row in cur.fetchall()}


# ── 메인 업데이트 로직 ────────────────────────────────────────────────────
def run_update(target_date: date = None, missing_only: bool = False) -> dict:
    """
    일별 업데이트 실행
    missing_only=True: target_date에 누락된 종목만 재수집 (이미 수집된 종목 스킵)
    Returns: 결과 딕셔너리 (보고서 생성용)
    """
    started_at = datetime.now(KST)
    conn = get_conn()
    client = InfomaxClient()

    # ── 업데이트 날짜 결정 ─────────────────────────────────────
    if target_date:
        start_date = end_date = target_date
    else:
        start_date, end_date = get_update_range(conn)
        if start_date > end_date:
            print(f"  업데이트할 데이터 없음 (DB 최신: {end_date}, 어제: {end_date})")
            conn.close()
            return {"skipped_no_data": True, "start_date": start_date, "end_date": end_date}

    # 단일 휴장일 타겟이면 주식 수집 skip (배당/휴일 파이프라인은 main에서 별도 진행)
    if start_date == end_date and is_market_closed(conn, start_date):
        print(f"  ℹ️ {start_date} 휴장일 — 주식 데이터 수집 skip")
        conn.close()
        return {"skipped_holiday": True, "start_date": start_date, "end_date": end_date}

    # ─────────────────────────────────────────────────────────
    # STEP 0: 종목 마스터 갱신 (신규 상장 / 상장폐지 자동 반영)
    # ─────────────────────────────────────────────────────────
    print("[0/2] 종목 마스터 갱신 중...")
    master_sync = {"new_listed": [], "delisted": [], "ghost_delisted": [], "errors": []}
    try:
        master_sync = sync_stock_master(conn, client)
        if master_sync["new_listed"]:
            codes_str = ", ".join(master_sync["new_listed"][:5])
            suffix    = f" 외 {len(master_sync['new_listed'])-5}개" if len(master_sync["new_listed"]) > 5 else ""
            print(f"  ✅ 신규 상장 {len(master_sync['new_listed'])}개 추가: {codes_str}{suffix}")
        if master_sync["delisted"]:
            codes_str = ", ".join(master_sync["delisted"][:5])
            suffix    = f" 외 {len(master_sync['delisted'])-5}개" if len(master_sync["delisted"]) > 5 else ""
            print(f"  ✅ 상장폐지 처리 {len(master_sync['delisted'])}개: {codes_str}{suffix}")
        if master_sync["ghost_delisted"]:
            codes_str = ", ".join(master_sync["ghost_delisted"][:5])
            suffix    = f" 외 {len(master_sync['ghost_delisted'])-5}개" if len(master_sync["ghost_delisted"]) > 5 else ""
            print(f"  ✅ API 미등록 종목 비활성화 {len(master_sync['ghost_delisted'])}개: {codes_str}{suffix}")
        if not any([master_sync["new_listed"], master_sync["delisted"], master_sync["ghost_delisted"], master_sync["errors"]]):
            print("  ✅ 변동 없음 (신규 상장 / 상장폐지 없음)")
        for err in master_sync["errors"]:
            print(f"  ⚠️  {err}")
    except Exception as e:
        print(f"  ⚠️  종목 마스터 갱신 실패 (수집은 계속 진행): {e}")
        master_sync["errors"].append(str(e))

    if missing_only and target_date:
        all_stocks    = get_missing_ohlcv_stocks(conn, target_date)
        kospi_kosdaq  = get_missing_investor_stocks(conn, target_date)
        foreign_stocks = get_missing_foreign_stocks(conn, target_date)
        print(f"  [재수집 모드] ohlcv 누락: {len(all_stocks)}개 | 수급 누락: {len(kospi_kosdaq)}개 | 지분율 누락: {len(foreign_stocks)}개")
    else:
        all_stocks    = get_stocks(conn, include_etf=True)
        kospi_kosdaq  = get_stocks(conn, include_etf=False)
        foreign_stocks = get_foreign_stocks(conn)
    code_to_name = {c: n for c, n in all_stocks}

    total_stocks    = len(all_stocks)
    investor_stocks = len(kospi_kosdaq)
    foreign_count   = len(foreign_stocks)

    print(f"\n{'='*70}")
    print(f"  일별 업데이트 시작: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  업데이트 기간: {start_date} ~ {end_date}")
    print(f"  전체 종목: {total_stocks}개 | 수급 대상: {investor_stocks}개 | 지분율 대상: {foreign_count}개")
    print(f"{'='*70}\n")

    # 전일 종가 (급등락 감지용)
    prev_close = get_prev_close(conn, start_date)

    # ── 결과 집계 변수 ─────────────────────────────────────────
    result = {
        "started_at":     started_at,
        "start_date":     start_date,
        "end_date":       end_date,
        "master_sync":    master_sync,
        "ohlcv":          {"success": 0, "fail": 0, "rows": 0, "changed": 0, "skipped": 0, "fail_codes": []},
        "market_cap":     {"rows": 0, "changed": 0, "skipped": 0},
        "investor":       {"success": 0, "fail": 0, "rows": 0, "changed": 0, "skipped": 0, "fail_codes": []},
        "foreign":        {"success": 0, "fail": 0, "rows": 0, "changed": 0, "skipped": 0, "fail_codes": []},
        "ohlcv_data":     [],   # 분석용 raw rows
        "investor_data":  [],   # 분석용 raw rows
        "anomalies":      [],
        "errors":         [],
    }

    # ─────────────────────────────────────────────────────────
    # STEP 1: OHLCV + 시가총액 수집 (전 종목, 병렬)
    # ─────────────────────────────────────────────────────────
    print(f"[1/2] OHLCV + 시가총액 수집 ({total_stocks}개 종목, workers={MAX_WORKERS})...")

    ohlcv_batch  = []
    mktcap_batch = []
    all_ohlcv_rows = []
    done_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_hist, client, code, name, start_date, end_date): code
            for code, name in all_stocks
        }
        for future in as_completed(futures):
            code, name, rows = future.result()
            done_count += 1

            if not rows:
                result["ohlcv"]["fail"] += 1
                result["ohlcv"]["fail_codes"].append(code)
            else:
                result["ohlcv"]["success"] += 1
                for r in rows:
                    if r["date"] is None:
                        continue
                    r["stock_name"] = name
                    all_ohlcv_rows.append(r)

                    ohlcv_batch.append((
                        r["date"], r["stock_code"],
                        r["open_price"], r["high_price"],
                        r["low_price"],  r["close_price"],
                        r["volume"],     r["trading_value"],
                    ))

                    if r["close_price"] and r["listed_shares"]:
                        mkt_cap = r["close_price"] * r["listed_shares"]
                        mktcap_batch.append((r["date"], r["stock_code"], mkt_cap))

            # 배치 저장 (500건마다, 메인 스레드에서만 실행)
            if len(ohlcv_batch) >= 500:
                ch, tot = upsert_batch(conn, OHLCV_SQL, ohlcv_batch)
                result["ohlcv"]["changed"] += ch
                result["ohlcv"]["skipped"] += tot - ch
                result["ohlcv"]["rows"]    += tot
                ohlcv_batch.clear()
            if len(mktcap_batch) >= 500:
                ch, tot = upsert_batch(conn, MKTCAP_SQL, mktcap_batch)
                result["market_cap"]["changed"] += ch
                result["market_cap"]["skipped"] += tot - ch
                result["market_cap"]["rows"]    += tot
                mktcap_batch.clear()

            if done_count % 500 == 0 or done_count == total_stocks:
                print(f"  [{done_count:4}/{total_stocks}] 진행 중... (성공:{result['ohlcv']['success']} 실패:{result['ohlcv']['fail']})")

            # 첫 번째 체크포인트: 실제 수집 데이터 샘플 확인
            if done_count == 500 and all_ohlcv_rows:
                print(f"  ── 중간 샘플 체크 (최근 수집 5건) ──")
                for r in all_ohlcv_rows[-5:]:
                    print(
                        f"     {r['stock_code']}  {r.get('stock_name','')[:8]:<8}  "
                        f"{r['date']}  종가={r['close_price'] or 0:,}  거래량={r['volume'] or 0:,}"
                    )
                bad = sum(1 for r in all_ohlcv_rows if not r.get("close_price") or r["close_price"] < 0)
                if bad > len(all_ohlcv_rows) * 0.1:
                    print(f"  ⚠️  비정상 종가 {bad}건 ({bad/len(all_ohlcv_rows):.0%}) 감지 — 수집 데이터 확인 필요!")

    # 잔여 저장
    if ohlcv_batch:
        ch, tot = upsert_batch(conn, OHLCV_SQL, ohlcv_batch)
        result["ohlcv"]["changed"] += ch
        result["ohlcv"]["skipped"] += tot - ch
        result["ohlcv"]["rows"]    += tot
    if mktcap_batch:
        ch, tot = upsert_batch(conn, MKTCAP_SQL, mktcap_batch)
        result["market_cap"]["changed"] += ch
        result["market_cap"]["skipped"] += tot - ch
        result["market_cap"]["rows"]    += tot

    result["ohlcv_data"] = all_ohlcv_rows
    print(f"  ✅ OHLCV {result['ohlcv']['rows']:,}건 저장 (변경:{result['ohlcv']['changed']:,} / 스킵:{result['ohlcv']['skipped']:,})")
    print(f"  ✅ 시가총액 {result['market_cap']['rows']:,}건 저장 (변경:{result['market_cap']['changed']:,} / 스킵:{result['market_cap']['skipped']:,})")

    # ─────────────────────────────────────────────────────────
    # STEP 2: 투자자별 수급 수집 (KOSPI + KOSDAQ, 병렬)
    # ─────────────────────────────────────────────────────────
    print(f"\n[2/2] 투자자별 수급 수집 ({investor_stocks}개 종목, workers={MAX_WORKERS})...")

    investor_batch    = []
    all_investor_rows = []
    done_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_investor, client, code, name, start_date, end_date): code
            for code, name in kospi_kosdaq
        }
        for future in as_completed(futures):
            code, name, rows = future.result()
            done_count += 1

            if not rows:
                result["investor"]["fail"] += 1
                result["investor"]["fail_codes"].append(code)
            else:
                result["investor"]["success"] += 1
                for r in rows:
                    if r["date"] is None:
                        continue
                    r["stock_name"] = name
                    all_investor_rows.append(r)
                    investor_batch.append((
                        r["date"], r["stock_code"], r["investor_type"],
                        r["net_buy_value"], r["net_buy_volume"],
                    ))

            if len(investor_batch) >= 500:
                ch, tot = upsert_batch(conn, INVESTOR_SQL, investor_batch)
                result["investor"]["changed"] += ch
                result["investor"]["skipped"] += tot - ch
                result["investor"]["rows"]    += tot
                investor_batch.clear()

            if done_count % 500 == 0 or done_count == investor_stocks:
                print(f"  [{done_count:4}/{investor_stocks}] 진행 중... (성공:{result['investor']['success']} 실패:{result['investor']['fail']})")

            # 첫 번째 체크포인트: 실제 수집 데이터 샘플 확인
            if done_count == 500 and all_investor_rows:
                print(f"  ── 중간 샘플 체크 (최근 수집 5건) ──")
                for r in all_investor_rows[-5:]:
                    print(
                        f"     {r['stock_code']}  {r.get('stock_name','')[:8]:<8}  "
                        f"{r['date']}  {r.get('investor_type',''):<8}  "
                        f"순매수={r.get('net_buy_value') or 0:+,}"
                    )
                bad = sum(1 for r in all_investor_rows if r.get("net_buy_value") is None)
                if bad > len(all_investor_rows) * 0.1:
                    print(f"  ⚠️  순매수 NULL {bad}건 ({bad/len(all_investor_rows):.0%}) 감지 — 수집 데이터 확인 필요!")

    if investor_batch:
        ch, tot = upsert_batch(conn, INVESTOR_SQL, investor_batch)
        result["investor"]["changed"] += ch
        result["investor"]["skipped"] += tot - ch
        result["investor"]["rows"]    += tot

    result["investor_data"] = all_investor_rows
    print(f"  ✅ 수급 {result['investor']['rows']:,}건 저장 (변경:{result['investor']['changed']:,} / 스킵:{result['investor']['skipped']:,})")

    # ─────────────────────────────────────────────────────────
    # STEP 3: 외국인 지분율 수집 (ETF/SPAC 제외, 병렬)
    # ─────────────────────────────────────────────────────────
    print(f"\n[3/3] 외국인 지분율 수집 ({foreign_count}개 종목, workers={MAX_WORKERS})...")

    foreign_batch = []
    done_count    = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_foreign, client, code, name, start_date, end_date): code
            for code, name in foreign_stocks
        }
        for future in as_completed(futures):
            code, name, rows = future.result()
            done_count += 1

            if not rows:
                result["foreign"]["fail"] += 1
                result["foreign"]["fail_codes"].append(code)
            else:
                result["foreign"]["success"] += 1
                for r in rows:
                    if r["date"] is None:
                        continue
                    foreign_batch.append((
                        r["date"], r["stock_code"],
                        r["frn_ownership_ratio"],
                        r["frn_ownership_vol"],
                        r["frn_limit_ratio"],
                    ))

            if len(foreign_batch) >= 500:
                ch, tot = upsert_batch(conn, FOREIGN_SQL, foreign_batch)
                result["foreign"]["changed"] += ch
                result["foreign"]["skipped"] += tot - ch
                result["foreign"]["rows"]    += tot
                foreign_batch.clear()

            if done_count % 500 == 0 or done_count == foreign_count:
                print(f"  [{done_count:4}/{foreign_count}] 진행 중... (성공:{result['foreign']['success']} 실패:{result['foreign']['fail']})")

    if foreign_batch:
        ch, tot = upsert_batch(conn, FOREIGN_SQL, foreign_batch)
        result["foreign"]["changed"] += ch
        result["foreign"]["skipped"] += tot - ch
        result["foreign"]["rows"]    += tot

    print(f"  ✅ 지분율 {result['foreign']['rows']:,}건 저장 (변경:{result['foreign']['changed']:,} / 스킵:{result['foreign']['skipped']:,})")

    # ─────────────────────────────────────────────────────────
    # STEP 4: 특이사항 분석
    # ─────────────────────────────────────────────────────────
    print("\n[분석] 특이사항 감지 중... (STEP 4)")
    halt_suspects = get_halt_suspects(conn, end_date)
    result["anomalies"] = analyze_anomalies(
        result["ohlcv_data"],
        result["investor_data"],
        prev_close,
        halt_suspects,
    )
    print(f"  ✅ 특이사항 {len(result['anomalies'])}건 감지")

    result["finished_at"] = datetime.now(KST)
    conn.close()
    return result


# ── 보고서 생성 ───────────────────────────────────────────────────────────
def generate_report(result: dict) -> str:
    """상세 보고서 텍스트 생성"""
    started  = result["started_at"]
    finished = result["finished_at"]
    elapsed  = (finished - started).total_seconds()
    s_date   = result["start_date"]
    e_date   = result["end_date"]

    master_sync = result.get("master_sync", {})
    ohlcv    = result["ohlcv"]
    mktcap   = result["market_cap"]
    investor = result["investor"]
    foreign  = result["foreign"]
    anomalies = result["anomalies"]

    lines = []
    W = 72

    def sep(char="="):
        lines.append(char * W)

    def title(text):
        sep()
        lines.append(f"  {text}")
        sep()

    def sub(text):
        lines.append(f"\n── {text} {'─'*(W-5-len(text))}")

    # ── 헤더 ──────────────────────────────────────────────────
    title("📊 일별 데이터 업데이트 보고서")
    lines.append(f"  실행 일시 : {started.strftime('%Y-%m-%d %H:%M:%S KST')}")
    lines.append(f"  완료 일시 : {finished.strftime('%Y-%m-%d %H:%M:%S KST')}")
    lines.append(f"  소요 시간 : {int(elapsed//3600)}시간 {int(elapsed%3600//60)}분 {int(elapsed%60)}초")
    lines.append(f"  업데이트 기간 : {s_date} ~ {e_date}")
    lines.append("")

    # ── 종목 마스터 갱신 ───────────────────────────────────────
    sub("0. 종목 마스터 갱신")
    new_listed      = master_sync.get("new_listed",      [])
    delisted        = master_sync.get("delisted",        [])
    ghost_delisted  = master_sync.get("ghost_delisted",  [])
    ms_errors       = master_sync.get("errors",          [])

    if new_listed:
        codes_str = ", ".join(new_listed[:10])
        suffix = f" 외 {len(new_listed)-10}개" if len(new_listed) > 10 else ""
        lines.append(f"\n  신규 상장  : {len(new_listed)}개  →  {codes_str}{suffix}")
    else:
        lines.append("\n  신규 상장  : 없음")

    if delisted:
        codes_str = ", ".join(delisted[:10])
        suffix = f" 외 {len(delisted)-10}개" if len(delisted) > 10 else ""
        lines.append(f"  상장폐지   : {len(delisted)}개  →  {codes_str}{suffix}")
    else:
        lines.append("  상장폐지   : 없음")

    if ghost_delisted:
        codes_str = ", ".join(ghost_delisted[:10])
        suffix = f" 외 {len(ghost_delisted)-10}개" if len(ghost_delisted) > 10 else ""
        lines.append(f"  API 미등록 비활성화: {len(ghost_delisted)}개  →  {codes_str}{suffix}")
    else:
        lines.append("  API 미등록 비활성화: 없음")

    if ms_errors:
        for err in ms_errors:
            lines.append(f"  ⚠️  {err}")
    lines.append("")

    # ── 수집 요약 ──────────────────────────────────────────────
    sub("1. 수집 요약")
    lines.append(f"  {'항목':<18} {'성공':>7} {'실패':>7} {'전체레코드':>11} {'신규/변경':>10} {'스킵(동일)':>11}")
    lines.append(f"  {'-'*68}")
    lines.append(
        f"  {'OHLCV (일봉)':<18} {ohlcv['success']:>7,} {ohlcv['fail']:>7,} "
        f"{ohlcv['rows']:>11,} {ohlcv['changed']:>10,} {ohlcv['skipped']:>11,}"
    )
    lines.append(
        f"  {'시가총액':<18} {'(OHLCV와 동일)':>14} "
        f"{mktcap['rows']:>11,} {mktcap['changed']:>10,} {mktcap['skipped']:>11,}"
    )
    lines.append(
        f"  {'투자자별 수급':<18} {investor['success']:>7,} {investor['fail']:>7,} "
        f"{investor['rows']:>11,} {investor['changed']:>10,} {investor['skipped']:>11,}"
    )
    lines.append(
        f"  {'외국인 지분율':<18} {foreign['success']:>7,} {foreign['fail']:>7,} "
        f"{foreign['rows']:>11,} {foreign['changed']:>10,} {foreign['skipped']:>11,}"
    )
    total_rows    = ohlcv['rows']    + mktcap['rows']    + investor['rows']    + foreign['rows']
    total_changed = ohlcv['changed'] + mktcap['changed'] + investor['changed'] + foreign['changed']
    total_skipped = ohlcv['skipped'] + mktcap['skipped'] + investor['skipped'] + foreign['skipped']
    lines.append(f"  {'-'*68}")
    lines.append(
        f"  {'합계':<18} {'':>14} "
        f"{total_rows:>11,} {total_changed:>10,} {total_skipped:>11,}"
    )
    lines.append("")

    # ── 테이블별 상세 ──────────────────────────────────────────
    sub("2. 테이블별 상세 결과")

    lines.append(f"\n  [ohlcv_daily]")
    lines.append(f"    전체 건수  : {ohlcv['rows']:,}건")
    lines.append(f"    신규/변경  : {ohlcv['changed']:,}건")
    lines.append(f"    스킵(동일) : {ohlcv['skipped']:,}건")
    lines.append(f"    성공 종목  : {ohlcv['success']:,}개")
    lines.append(f"    실패 종목  : {ohlcv['fail']:,}개")
    if ohlcv['fail_codes']:
        codes_str = ', '.join(ohlcv['fail_codes'][:20])
        suffix = f" 외 {ohlcv['fail']-20}개" if ohlcv['fail'] > 20 else ""
        lines.append(f"    실패 코드  : {codes_str}{suffix}")

    lines.append(f"\n  [market_cap_daily]")
    lines.append(f"    전체 건수  : {mktcap['rows']:,}건")
    lines.append(f"    신규/변경  : {mktcap['changed']:,}건")
    lines.append(f"    스킵(동일) : {mktcap['skipped']:,}건")
    lines.append(f"    산출 방식  : close_price × listed_shares (hist API)")

    lines.append(f"\n  [investor_trading]")
    lines.append(f"    전체 건수  : {investor['rows']:,}건")
    lines.append(f"    신규/변경  : {investor['changed']:,}건")
    lines.append(f"    스킵(동일) : {investor['skipped']:,}건")
    lines.append(f"    성공 종목  : {investor['success']:,}개")
    lines.append(f"    실패 종목  : {investor['fail']:,}개")
    if investor['fail_codes']:
        codes_str = ', '.join(investor['fail_codes'][:20])
        suffix = f" 외 {investor['fail']-20}개" if investor['fail'] > 20 else ""
        lines.append(f"    실패 코드  : {codes_str}{suffix}")

    lines.append(f"\n  [foreign_ownership]")
    lines.append(f"    전체 건수  : {foreign['rows']:,}건")
    lines.append(f"    신규/변경  : {foreign['changed']:,}건")
    lines.append(f"    스킵(동일) : {foreign['skipped']:,}건")
    lines.append(f"    성공 종목  : {foreign['success']:,}개")
    lines.append(f"    실패 종목  : {foreign['fail']:,}개")
    lines.append(f"    수집 대상  : ETF/SPAC 제외 KOSPI+KOSDAQ")
    if foreign['fail_codes']:
        codes_str = ', '.join(foreign['fail_codes'][:20])
        suffix = f" 외 {foreign['fail']-20}개" if foreign['fail'] > 20 else ""
        lines.append(f"    실패 코드  : {codes_str}{suffix}")
    lines.append("")

    # ── 특이사항 ────────────────────────────────────────────────
    sub("3. 특이사항")

    if not anomalies:
        lines.append("\n  ✅ 특이사항 없음")
    else:
        # 유형별 분류
        by_type = defaultdict(list)
        for a in anomalies:
            by_type[a["type"]].append(a)

        type_order = ["주가이벤트의심",
                      "거래정지", "거래정지의심", "무거래(스팩)", "OHLCV오류",
                      "가격급등", "가격급락", "대규모순매수", "대규모순매도"]
        sorted_types = sorted(by_type.keys(),
                              key=lambda t: type_order.index(t) if t in type_order else 99)

        lines.append(f"\n  총 {len(anomalies)}건의 특이사항이 감지되었습니다.\n")

        for atype in sorted_types:
            items = by_type[atype]
            emoji = {
                "주가이벤트의심": "🚨",
                "거래정지":     "🔴",
                "거래정지의심": "🟡",
                "무거래(스팩)": "⚪",
                "OHLCV오류":    "❌",
                "가격급등":     "📈",
                "가격급락":     "📉",
                "대규모순매수": "💰",
                "대규모순매도": "💸",
            }.get(atype, "⚠️")

            sorted_items = sorted(items, key=lambda x: abs(x.get("value", 0) or 0), reverse=True)
            display_items = sorted_items[:30] if atype in ("가격급등", "가격급락") else sorted_items
            total_label = (
                f" (TOP {len(display_items)} / 전체 {len(items)}건)"
                if atype in ("가격급등", "가격급락") and len(items) > 30
                else f" {len(items)}건"
            )

            if atype == "주가이벤트의심":
                # 경고 박스로 강조 출력 — 절대 놓치면 안 되는 항목
                lines.append(f"  {'!'*W}")
                lines.append(f"  {emoji} [{atype}]{total_label}  ← ±30% 초과 = 수정계수 확인 필요!")
                lines.append(f"  {'비수정주가 불연속 발생 가능 → 향후 수정주가 적용 시 이 날짜 기준으로 처리':^{W}}")
                lines.append(f"  {'!'*W}")
            else:
                lines.append(f"  {emoji} [{atype}]{total_label}")

            lines.append(f"  {'날짜':<12} {'종목코드':<10} {'종목명':<18} {'상세'}")
            lines.append(f"  {'-'*68}")
            for a in display_items:
                dt_str   = str(a["date"]) if a["date"] else "-"
                lines.append(
                    f"  {dt_str:<12} {a['stock_code']:<10} "
                    f"{a['stock_name'][:16]:<18} {a['detail']}"
                )
            lines.append("")

    # ── 실패 종목 상세 ─────────────────────────────────────────
    if ohlcv['fail'] > 0 or investor['fail'] > 0 or foreign['fail'] > 0:
        sub("4. API 수집 실패 종목")
        lines.append("\n  ※ 실패 원인: API 응답 없음 / 해당 날짜 데이터 없음 (휴장일, 신규상장 전)")

        if ohlcv['fail_codes']:
            lines.append(f"\n  OHLCV 실패 ({ohlcv['fail']}개):")
            for c in ohlcv['fail_codes']:
                lines.append(f"    - {c}")

        if investor['fail_codes']:
            lines.append(f"\n  수급 실패 ({investor['fail']}개):")
            for c in investor['fail_codes']:
                lines.append(f"    - {c}")

        if foreign['fail_codes']:
            lines.append(f"\n  지분율 실패 ({foreign['fail']}개):")
            for c in foreign['fail_codes']:
                lines.append(f"    - {c}")
        lines.append("")

    # ── 푸터 ──────────────────────────────────────────────────
    sep()
    lines.append(f"  보고서 생성: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    sep()

    return "\n".join(lines)


def save_report(report_text: str, target_date: date) -> Path:
    fname = REPORTS_DIR / f"daily_update_{target_date.strftime('%Y%m%d')}.txt"
    fname.write_text(report_text, encoding="utf-8")
    return fname


# ── 진입점 ────────────────────────────────────────────────────────────────
def run_dividend_pipeline(end_date: date) -> dict:
    """
    DART 배당결정 공시 신규 수집 + LENS JSON export.
    daily_update 마지막 단계로 호출.

    수집 범위:
      start_date = DB의 마지막 announced_at + 1일 (자동 산출, 갭 자동 backfill)
      end_date   = 인자로 받은 날짜 + 1일 (당일 새벽 공시 포함)
    """
    from scripts.backfill_dividends import run_backfill, refresh_future_ex_dates
    from scripts.export_dividends import build_payload, write_atomic

    print("\n" + "=" * 70)
    print("  💰 배당 데이터 (DART 신규 공시 + LENS export)")
    print("=" * 70)

    # 1) start_date 자동 산출 — DB에서 가장 최근 공시 접수일 + 1일
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(MAX(announced_at)::date + 1, %s::date)
                  FROM dividends WHERE source = 'DART'
            """, (date(2022, 1, 1),))
            bgn = cur.fetchone()[0]
    finally:
        conn.close()

    end = end_date + timedelta(days=1)

    # 갭이 음수면 (DB가 미래 데이터) skip
    if bgn > end:
        print(f"\n[배당-1] 신규 공시 없음 (DB last={bgn-timedelta(days=1)} ≥ end={end_date})")
        bf = {"found": 0, "new": 0, "parsed": 0, "inserted": 0}
    else:
        gap_days = (end - bgn).days
        print(f"\n[배당-1] 신규 공시 수집 ({bgn} ~ {end_date}, 자동 산출 갭={gap_days}일)...")
        bf = run_backfill(bgn, end, workers=2)
        print(f"  → 검색 {bf['found']}건 / 신규 {bf['new']}건 / 적재 {bf['inserted']}건")

    # 2) 미래 ex_date 자동 보정 (ohlcv가 채워질 때마다 한국 공휴일 자연 반영)
    conn = get_conn()
    try:
        refreshed = refresh_future_ex_dates(conn)
        print(f"\n[배당-2] 미래 ex_date 보정: {refreshed}건 UPDATE")
    finally:
        conn.close()

    # 3) LENS export
    print(f"\n[배당-3] LENS JSON export...")
    conn = get_conn()
    try:
        payload = build_payload(conn)
    finally:
        conn.close()

    output_path = Path(settings.LENS_EXPORT_PATH)
    write_atomic(payload, output_path)
    print(f"  → {payload['count']:,}건 → {output_path}")

    return {
        "backfill": bf,
        "exported_count": payload["count"],
        "exported_path": str(output_path),
    }


INDEX_TRACKING_ETFS = [
    ("069500", "KOSPI200"),
    ("229200", "KOSDAQ150"),
]


def run_index_components_pipeline(target_date: date) -> dict:
    """
    KOSPI200/KOSDAQ150 구성종목 변동 감지 + SCD2 적재.
    매일 추적 ETF (KODEX 200/코스닥150) 의 PDF를 받아서 active 멤버십과 diff:
      - 신규 편입: INSERT effective_date=target_date, end_date=NULL
      - 편출: UPDATE end_date=target_date
    PDF 빈 응답(휴장/오류)이면 변경 적용 안 함.
    """
    print("\n" + "=" * 70)
    print("  📊 KOSPI200/KOSDAQ150 구성종목 SCD2 갱신")
    print("=" * 70)

    client = InfomaxClient()
    conn = get_conn()
    summary = {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT stock_code FROM stocks")
            known = {r[0] for r in cur.fetchall()}

        for etf_code, idx_name in INDEX_TRACKING_ETFS:
            rows = client.get_etf_portfolio(etf_code, target_date)
            if not rows:
                print(f"  [{idx_name}] {target_date} PDF 빈 응답 — skip (no-op)")
                summary[idx_name] = {"skipped": True}
                continue

            # stocks 매칭으로 의사코드(010010 원화현금) + 알파벳 종목 모두 처리
            new_set = {r["port_code"] for r in rows if r["port_code"]} & known

            with conn.cursor() as cur:
                cur.execute("""
                    SELECT stock_code FROM index_components
                     WHERE index_name = %s AND end_date IS NULL
                """, (idx_name,))
                cur_set = {r[0] for r in cur.fetchall()}

            added   = new_set - cur_set
            removed = cur_set - new_set
            unchanged = len(new_set & cur_set)

            with conn:
                with conn.cursor() as cur:
                    for code in removed:
                        cur.execute("""
                            UPDATE index_components SET end_date = %s
                             WHERE index_name = %s AND stock_code = %s AND end_date IS NULL
                        """, (target_date, idx_name, code))
                    for code in added:
                        cur.execute("""
                            INSERT INTO index_components (index_name, stock_code, effective_date)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (index_name, stock_code, effective_date) DO NOTHING
                        """, (idx_name, code, target_date))

            print(f"  [{idx_name}] PDF {len(new_set)}종목 / 기존 {len(cur_set)}종목"
                  f" → 편입 {len(added)} / 편출 {len(removed)} / 유지 {unchanged}")
            if added:
                print(f"    + {sorted(added)}")
            if removed:
                print(f"    - {sorted(removed)}")
            summary[idx_name] = {
                "pdf_count": len(new_set), "before": len(cur_set),
                "added": len(added), "removed": len(removed),
                "added_codes": sorted(added), "removed_codes": sorted(removed),
            }
    finally:
        conn.close()

    return summary


def run_etf_daily_snapshot_pipeline(target_date: date) -> dict:
    """
    한국 ETF 590개의 PDF + 마스터를 매일 스냅샷으로 적재.
    - etf_portfolio_daily: PDF 종목 + 현금 (snapshot_date=target_date)
    - etf_master_daily: creation_unit, listed_shares 등
    - 5일 FIFO: 6일 초과 데이터 DELETE
    LENS fNav 계산용 (creation_unit, cash, shares).
    """
    print("\n" + "=" * 70)
    print("  📦 ETF 일별 스냅샷 (PDF + 마스터, 5일 FIFO)")
    print("=" * 70)

    from scripts.seed_etf_portfolios import KOREA_ETF_SQL
    client = InfomaxClient()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(KOREA_ETF_SQL)
            etfs = cur.fetchall()

        pdf_rows = master_rows = 0
        empty_pdf = empty_master = 0
        errors = []

        for i, (etf_code, etf_name) in enumerate(etfs, 1):
            # 1) PDF (/api/etf/port)
            try:
                rows = client.get_etf_portfolio(etf_code, target_date)
                if rows:
                    seen = set()
                    pdf_values = []
                    for r in rows:
                        pc = r.get("port_code")
                        if not pc or pc in seen:
                            continue
                        seen.add(pc)
                        # 현금 항목 (KRD... 또는 port_name이 원화현금) 식별
                        is_cash = (pc.startswith("KRD") or
                                   "원화현금" in (r.get("port_name") or "") or
                                   "현금" in (r.get("port_name") or ""))
                        # shares: 일반 종목은 port_volume, 현금은 port_value (음수 가능)
                        shares = r.get("port_value") if is_cash else r.get("port_volume")
                        pdf_values.append((etf_code, target_date, pc,
                                           r.get("port_name"), shares, is_cash))
                    if pdf_values:
                        with conn:
                            with conn.cursor() as cur:
                                psycopg2.extras.execute_values(cur, """
                                    INSERT INTO etf_portfolio_daily
                                      (etf_code, snapshot_date, component_code, component_name, shares, is_cash)
                                    VALUES %s
                                    ON CONFLICT (etf_code, snapshot_date, component_code) DO UPDATE SET
                                      component_name = EXCLUDED.component_name,
                                      shares = EXCLUDED.shares,
                                      is_cash = EXCLUDED.is_cash
                                """, pdf_values, page_size=200)
                        pdf_rows += len(pdf_values)
                else:
                    empty_pdf += 1
            except Exception as e:
                errors.append((etf_code, "pdf", str(e)[:80]))

            # 2) 마스터 (/api/etp)
            try:
                m = client.get_etf_master(etf_code, target_date)
                if m:
                    with conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                INSERT INTO etf_master_daily
                                  (etf_code, snapshot_date, kr_name, kr_company,
                                   creation_unit, listed_shares, net_asset,
                                   underlying_index, tracking_multiple, replication, total_fee)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT (etf_code, snapshot_date) DO UPDATE SET
                                  kr_name = EXCLUDED.kr_name,
                                  kr_company = EXCLUDED.kr_company,
                                  creation_unit = EXCLUDED.creation_unit,
                                  listed_shares = EXCLUDED.listed_shares,
                                  net_asset = EXCLUDED.net_asset,
                                  underlying_index = EXCLUDED.underlying_index,
                                  tracking_multiple = EXCLUDED.tracking_multiple,
                                  replication = EXCLUDED.replication,
                                  total_fee = EXCLUDED.total_fee
                            """, (etf_code, target_date, m.get("kr_name"), m.get("kr_company"),
                                  m.get("creationunit"), m.get("listed_shares"), m.get("net_asset"),
                                  m.get("underlying_index"), m.get("tracking_multiple"),
                                  m.get("replication"), m.get("total_fee")))
                    master_rows += 1
                else:
                    empty_master += 1
            except Exception as e:
                errors.append((etf_code, "master", str(e)[:80]))

        # 5일 FIFO DELETE
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM etf_portfolio_daily WHERE snapshot_date < %s - INTERVAL '5 days'",
                            (target_date,))
                pdf_deleted = cur.rowcount
                cur.execute("DELETE FROM etf_master_daily WHERE snapshot_date < %s - INTERVAL '5 days'",
                            (target_date,))
                master_deleted = cur.rowcount

        print(f"  [완료] ETF {len(etfs)}개")
        print(f"    PDF 적재 {pdf_rows} / 빈응답 {empty_pdf} / 마스터 적재 {master_rows} / 빈응답 {empty_master}")
        print(f"    5일 FIFO DELETE: PDF {pdf_deleted} / 마스터 {master_deleted}")
        print(f"    에러 {len(errors)}")
        return {"etfs": len(etfs), "pdf_rows": pdf_rows, "master_rows": master_rows,
                "empty_pdf": empty_pdf, "empty_master": empty_master,
                "pdf_deleted": pdf_deleted, "master_deleted": master_deleted,
                "errors": len(errors)}
    finally:
        conn.close()


def run_indices_futures_daily_pipeline(target_date: date) -> dict:
    """
    지수 + 선물(NEAR/NEXT) 일별 OHLCV 누적 (인포맥스).
    target_date ~ 어제 (보통 1일치) 호출. 1000행 한도라 매일이면 무관.
    """
    print("\n" + "=" * 70)
    print("  📊 지수 + 선물 일별 OHLCV (인포맥스)")
    print("=" * 70)

    from datetime import timedelta as _td
    client = InfomaxClient()
    conn = get_conn()
    try:
        # 지수: indices 마스터 전체
        with conn.cursor() as cur:
            cur.execute("SELECT code FROM indices ORDER BY code")
            idx_codes = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT underlying_code FROM futures_underlyings WHERE underlying_type IN ('F','L') ORDER BY underlying_code")
            fut_codes = [r[0] for r in cur.fetchall()]

        # 7일 마진 (최근 누락분 회수)
        start = target_date - _td(days=7)
        end = target_date

        # 지수 OHLCV
        idx_rows = 0
        for code in idx_codes:
            try:
                data = client.get_index_hist(code, start, end)
            except Exception:
                continue
            rows = [(
                code, _parse_ymd_daily(r.get('date')),
                r.get('open_price'), r.get('high_price'), r.get('low_price'), r.get('close_price'),
                r.get('change_rate'), r.get('trading_volume'), r.get('trading_value'),
                r.get('marketcap'), r.get('constituents'),
            ) for r in data]
            if rows:
                with conn:
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_values(cur, """
                            INSERT INTO index_ohlcv_daily
                              (code, time, open, high, low, close, change_pct, volume, trading_value, marketcap, constituents)
                            VALUES %s
                            ON CONFLICT (code, time) DO UPDATE SET
                              open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                              close=EXCLUDED.close, change_pct=EXCLUDED.change_pct,
                              volume=EXCLUDED.volume, trading_value=EXCLUDED.trading_value,
                              marketcap=EXCLUDED.marketcap, constituents=EXCLUDED.constituents
                        """, rows, page_size=500)
                idx_rows += len(rows)

        # 선물 OHLCV (NEAR/NEXT)
        fut_rows = 0
        for uc in fut_codes:
            for klass in ['NEAR', 'NEXT']:
                try:
                    data = client.get_future_active(uc, start, end, contract_class=klass)
                except Exception:
                    continue
                rows = [(
                    uc, klass, _parse_ymd_daily(r.get('date')), r.get('code'),
                    r.get('open_price'), r.get('high_price'), r.get('low_price'), r.get('close_price'),
                    r.get('settle_price'), r.get('trading_volume'), r.get('trading_value'),
                    r.get('openInterest_volume'), r.get('theoretical_price'),
                    r.get('underlying_basis'), r.get('theoretical_basis'),
                ) for r in data]
                if rows:
                    dedup: dict = {}
                    for x in rows:
                        dedup[(x[0], x[1], x[2])] = x
                    rows = list(dedup.values())
                    with conn:
                        with conn.cursor() as cur:
                            psycopg2.extras.execute_values(cur, """
                                INSERT INTO futures_ohlcv_daily
                                  (underlying_code, contract_class, time, contract_code,
                                   open, high, low, close, settle_price,
                                   volume, trading_value, open_interest,
                                   theoretical_price, underlying_basis, theoretical_basis)
                                VALUES %s
                                ON CONFLICT (underlying_code, contract_class, time) DO UPDATE SET
                                  contract_code=EXCLUDED.contract_code,
                                  open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                                  close=EXCLUDED.close, settle_price=EXCLUDED.settle_price,
                                  volume=EXCLUDED.volume, trading_value=EXCLUDED.trading_value,
                                  open_interest=EXCLUDED.open_interest,
                                  theoretical_price=EXCLUDED.theoretical_price,
                                  underlying_basis=EXCLUDED.underlying_basis,
                                  theoretical_basis=EXCLUDED.theoretical_basis
                            """, rows, page_size=500)
                    fut_rows += len(rows)

        print(f"  [완료] 지수 {len(idx_codes)}종목 / 선물 {len(fut_codes)}underlying")
        print(f"    INSERT/UPDATE: 지수 {idx_rows} row / 선물 {fut_rows} row")
        return {"indices": len(idx_codes), "futures": len(fut_codes),
                "idx_rows": idx_rows, "fut_rows": fut_rows}
    finally:
        conn.close()


def _parse_ymd_daily(v) -> date:
    s = str(v)
    return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))


def _backfill_pids() -> list[int]:
    """LS 백필 프로세스 PID list. (backfill_30sec_bars / backfill_index / backfill_futures)"""
    import subprocess
    pids: list[int] = []
    for pat in ("backfill_30sec_bars.py", "backfill_index_minute_bars.py",
                "backfill_futures_minute_bars.py"):
        try:
            r = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                pids.extend(int(p) for p in r.stdout.strip().splitlines() if p.strip().isdigit())
        except Exception:
            pass
    return pids


def _ls_backfill_pause():
    """진행 중인 모든 LS 백필 프로세스 SIGSTOP. CONT는 수동 호출."""
    import os, signal
    paused = []
    for pid in _backfill_pids():
        try:
            os.kill(pid, signal.SIGSTOP)
            paused.append(pid)
        except ProcessLookupError:
            pass
        except PermissionError:
            print(f"  [WARN] PID {pid} STOP 권한 없음")
    if paused:
        print(f"  [LS pause] STOPPED PIDs: {paused}")
    return paused


def _ls_backfill_resume(pids: list[int]):
    import os, signal
    for pid in pids:
        try:
            os.kill(pid, signal.SIGCONT)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
    if pids:
        print(f"  [LS resume] CONT'd PIDs: {pids}")


def _last_loaded_date(table: str, code_col: str, code: str = None) -> date | None:
    """주어진 테이블/코드의 마지막 적재일 (KST) 반환. 없으면 None."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if code:
                cur.execute(
                    f"SELECT MAX((time AT TIME ZONE 'Asia/Seoul')::date) "
                    f"FROM {table} WHERE {code_col} = %s",
                    (code,))
            else:
                cur.execute(f"SELECT MAX((time AT TIME ZONE 'Asia/Seoul')::date) FROM {table}")
            row = cur.fetchone()
            return row[0] if row and row[0] else None
    finally:
        conn.close()


def _gap_business_days(table: str, code_col: str, target_date: date) -> list[date]:
    """table 마지막 적재일+1 ~ target_date 거래일 list (휴장일 제외)."""
    last = _last_loaded_date(table, code_col)
    start = (last + timedelta(days=1)) if last else date(2026, 1, 2)
    if start > target_date:
        return []
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT time FROM ohlcv_daily WHERE time BETWEEN %s AND %s ORDER BY time",
                (start, target_date),
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def run_minute_bars_pipeline(target_date: date) -> dict:
    """
    분봉 일배치 (종목/ETF, LS t8452).
    갭 backfill: ohlcv_intraday max(time)+1 ~ target_date 거래일 sweep.
    백필 동시 진행 시 SIGSTOP → 일배치 → SIGCONT (사용자 정책).
    """
    from collectors.ls_api import LsApiClient, LsApiError
    from scripts._minute_scope import fetch_minute_scope
    from scripts.backfill_30sec_bars import insert_bars

    print("\n" + "=" * 70)
    print("  ⏱️  분봉 일배치 — 종목/ETF (LS t8452)")
    print("=" * 70)

    paused_pids = _ls_backfill_pause()
    try:
        biz_days = _gap_business_days("ohlcv_intraday", "stock_code", target_date)
        if not biz_days:
            print(f"  [skip] 갭 0일 (target_date={target_date})")
            return {"days": 0, "rows": 0}

        conn = get_conn()
        try:
            codes = fetch_minute_scope(conn)
            print(f"  [스코프] {len(codes)} 종목 × {len(biz_days)} 일")
        finally:
            conn.close()

        client = LsApiClient()
        conn = get_conn()
        try:
            total_rows = 0
            empty = 0
            errors = []
            from time import time as now
            t0 = now()
            for di, day in enumerate(biz_days, 1):
                print(f"  ▶ day {di}/{len(biz_days)} = {day}", flush=True)
                for i, code in enumerate(codes, 1):
                    try:
                        bars, interval = client.get_intraday_bars(code, day)
                        if not bars:
                            empty += 1
                            continue
                        n = insert_bars(conn, code, bars, interval)
                        total_rows += n
                    except LsApiError as e:
                        errors.append((code, day, e.category))
                    except Exception as e:
                        errors.append((code, day, f"unexpected:{type(e).__name__}"))
                    if i % 500 == 0:
                        elapsed = now() - t0
                        print(f"    [{i}/{len(codes)}] 적재 {total_rows:,}row / 빈 {empty} / 에러 {len(errors)}", flush=True)

            elapsed = now() - t0
            print(f"\n  [완료] {len(biz_days)}일 / 소요 {elapsed/60:.1f}분 / 적재 {total_rows:,}row / 빈 {empty} / 에러 {len(errors)}")
            if errors:
                from collections import Counter
                print(f"    에러 카테고리: {dict(Counter(e[2] for e in errors))}")
            return {"days": len(biz_days), "stocks": len(codes), "rows": total_rows, "empty": empty, "errors": len(errors)}
        finally:
            conn.close()
    finally:
        _ls_backfill_resume(paused_pids)


def run_index_minute_bars_pipeline(target_date: date) -> dict:
    """
    지수 30초봉 일배치 (LS t8418, /indtp/chart).
    갭 backfill: index_ohlcv_intraday max(time)+1 ~ target_date.
    스코프: KOSPI200(101) + KOSDAQ150(301) 만 (사용자 정책).
    """
    from collectors.ls_api import LsApiClient, LsApiError
    from scripts.backfill_index_minute_bars import insert_bars

    print("\n" + "=" * 70)
    print("  ⏱️  지수 분봉 일배치 (LS t8418)")
    print("=" * 70)

    biz_days = _gap_business_days("index_ohlcv_intraday", "index_code", target_date)
    if not biz_days:
        print(f"  [skip] 갭 0일")
        return {"days": 0, "rows": 0}

    client = LsApiClient()
    codes = ["101", "301"]  # KOSPI200, KOSDAQ150
    print(f"  [스코프] {len(codes)} 지수 (KOSPI200, KOSDAQ150) × {len(biz_days)} 일")

    conn = get_conn()
    try:
        total_rows = 0
        empty = 0
        errors = []
        from time import time as now
        t0 = now()
        for di, day in enumerate(biz_days, 1):
            print(f"  ▶ day {di}/{len(biz_days)} = {day}", flush=True)
            for i, code in enumerate(codes, 1):
                try:
                    bars = client.get_index_intraday_bars(code, day, ncnt=0)
                    if not bars:
                        empty += 1
                        continue
                    n = insert_bars(conn, code, bars, 30)
                    total_rows += n
                except LsApiError as e:
                    errors.append((code, day, e.category))
                except Exception as e:
                    errors.append((code, day, f"unexpected:{type(e).__name__}"))
                if i % 100 == 0:
                    print(f"    [{i}/{len(codes)}] 적재 {total_rows:,}row", flush=True)

        elapsed = now() - t0
        print(f"\n  [완료] {len(biz_days)}일 / 소요 {elapsed/60:.1f}분 / 적재 {total_rows:,}row / 빈 {empty} / 에러 {len(errors)}")
        return {"days": len(biz_days), "indices": len(codes), "rows": total_rows, "empty": empty, "errors": len(errors)}
    finally:
        conn.close()


def run_futures_minute_bars_pipeline(target_date: date) -> dict:
    """
    지수선물 30초봉 일배치 (LS t8465).
    갭 backfill: futures_ohlcv_intraday max(time)+1 ~ target_date.
    스코프: KOSPI200 F + KOSDAQ150 F 중 근월+다음월물만 (4개, 매일 자동 갱신).
    주식선물(t8406)은 별도 함수 (당일만, run_stockfut_minute_today_pipeline).
    """
    from collectors.ls_api import LsApiClient, LsApiError
    from scripts.backfill_futures_minute_bars import fetch_index_futures_master, insert_bars

    print("\n" + "=" * 70)
    print("  ⏱️  지수선물 분봉 일배치 (LS t8465, 근월+다음월물만)")
    print("=" * 70)

    biz_days = _gap_business_days("futures_ohlcv_intraday", "futures_code", target_date)
    if not biz_days:
        print(f"  [skip] 갭 0일")
        return {"days": 0, "rows": 0}

    client = LsApiClient()
    pairs = fetch_index_futures_master(client)
    codes = [c for c, _ in pairs]
    print(f"  [스코프] {len(codes)} 선물 × {len(biz_days)} 일")
    for sh, hn in pairs:
        print(f"     {sh:10s} {hn}")

    conn = get_conn()
    try:
        total_rows = 0
        empty = 0
        errors = []
        from time import time as now
        t0 = now()
        for di, day in enumerate(biz_days, 1):
            print(f"  ▶ day {di}/{len(biz_days)} = {day}", flush=True)
            for i, code in enumerate(codes, 1):
                try:
                    bars = client.get_futures_intraday_bars(code, day, ncnt=0)
                    if not bars:
                        empty += 1
                        continue
                    n = insert_bars(conn, code, bars, 30)
                    total_rows += n
                except LsApiError as e:
                    errors.append((code, day, e.category))
                except Exception as e:
                    errors.append((code, day, f"unexpected:{type(e).__name__}"))

        elapsed = now() - t0
        print(f"\n  [완료] {len(biz_days)}일 / 소요 {elapsed/60:.1f}분 / 적재 {total_rows:,}row / 빈 {empty} / 에러 {len(errors)}")
        return {"days": len(biz_days), "futures": len(codes), "rows": total_rows, "empty": empty, "errors": len(errors)}
    finally:
        conn.close()


def run_stockfut_minute_today_pipeline(target_date: date) -> dict:
    """
    주식선물 30초봉 일배치 — **당일만** (LS t8406, historical 불가).
    target_date가 오늘 아니면 skip (어제 데이터 받기 불가능).
    스코프: t8401 master, 'F'(단일선물)만, 만기 미경과만.
    """
    from collectors.ls_api import LsApiClient, LsApiError
    import psycopg2.extras

    print("\n" + "=" * 70)
    print("  ⏱️  주식선물 분봉 일배치 (LS t8406, 당일만)")
    print("=" * 70)

    today_kst = datetime.now(KST).date()
    if target_date != today_kst:
        print(f"  [skip] target_date={target_date} ≠ 오늘({today_kst}) — t8406 historical 불가")
        return {"skipped": True, "reason": "not_today"}

    from collectors.ls_api import select_near_next_two
    client = LsApiClient()
    master = client.get_stockfut_master()
    # 종목별(basecode) 근월+다음월물만
    actives = select_near_next_two(master, target_date,
                                   group_key=lambda m: m.get("basecode", ""))
    print(f"  [스코프] master {len(master)} → 활성 {len(actives)} 주식선물 (종목당 최대 2 만기)")

    INSERT_SQL = """
    INSERT INTO futures_ohlcv_intraday
        (futures_code, time, interval_seconds, open, high, low, close, volume, trading_value, open_interest)
    VALUES %s
    ON CONFLICT (futures_code, time, interval_seconds) DO UPDATE SET
        open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
        close = EXCLUDED.close, volume = EXCLUDED.volume,
        trading_value = EXCLUDED.trading_value,
        open_interest = EXCLUDED.open_interest
    """

    conn = get_conn()
    try:
        total_rows = 0
        empty = 0
        errors = []
        from time import time as now
        t0 = now()
        for i, m in enumerate(actives, 1):
            sh = m.get("shcode", "")
            try:
                bars = client.get_stockfut_today_bars(sh, bgubun=0)  # 30초봉
                if not bars:
                    empty += 1
                    continue
                rows = [LsApiClient.stockfut_t8406_to_db_row(sh, b, target_date, 30) for b in bars]
                rows = [r for r in rows if r and r["close"] not in (None, 0)]
                if not rows:
                    continue
                values = [(r["futures_code"], r["time"], r["interval_seconds"],
                           r["open"], r["high"], r["low"], r["close"],
                           r["volume"], r["trading_value"], r["open_interest"]) for r in rows]
                with conn:
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_values(cur, INSERT_SQL, values, page_size=500)
                total_rows += len(rows)
            except LsApiError as e:
                errors.append((sh, e.category))
            except Exception as e:
                errors.append((sh, f"unexpected:{type(e).__name__}"))
            if i % 50 == 0:
                print(f"    [{i}/{len(actives)}] 적재 {total_rows:,}row", flush=True)

        elapsed = now() - t0
        print(f"\n  [완료] 소요 {elapsed/60:.1f}분 / 적재 {total_rows:,}row / 빈 {empty} / 에러 {len(errors)}")
        return {"actives": len(actives), "rows": total_rows, "empty": empty, "errors": len(errors)}
    finally:
        conn.close()


def export_futures_master_json() -> dict:
    """주식선물 master JSON export → LENS futures_master.json (LENS realtime 공유 SSoT).
    LS t8401 master + 종목별 근월/차월 식별 + DB join (futures_underlyings + stocks).
    매일 daily_update 끝에서 호출 — LENS는 이 파일만 읽으면 자체 LS 호출 불필요."""
    import json as _json
    from collections import defaultdict, Counter
    from collectors.ls_api import LsApiClient, _parse_expiry_yyyymm, select_near_next_two

    OUT_PATH = Path("/home/una0/projects/LENS/data/futures_master.json")

    print("\n" + "=" * 70)
    print("  📤 LENS futures_master.json export (LS t8401 master)")
    print("=" * 70)

    today = datetime.now(KST).date()

    # 1) LS t8401 master
    client = LsApiClient()
    master = client.get_stockfut_master()
    print(f"  t8401 master: {len(master)} rows")

    # 2) 단일선물 (basecode별 근월+차월)
    actives = select_near_next_two(master, today,
                                   group_key=lambda m: m.get("basecode", ""))

    # 3) 스프레드 — basecode별 그룹
    spreads_by_base: dict[str, list] = defaultdict(list)
    for m in master:
        if "SP" in m.get("hname", ""):
            spreads_by_base[m.get("basecode", "")].append(m)

    # 4) DB join — futures_underlyings + stocks
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT fu.stock_code, s.stock_name, s.market
                FROM futures_underlyings fu
                JOIN stocks s ON s.stock_code = fu.stock_code
                WHERE fu.underlying_type = 'L'
            """)
            stock_info = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    finally:
        conn.close()

    # 5) basecode별 contract list
    by_base: dict[str, list] = defaultdict(list)
    for m in actives:
        by_base[m.get("basecode", "")].append(m)

    items: list[dict] = []
    front_months: list[str] = []
    back_months: list[str] = []

    def _build_leg(m: dict):
        exp = _parse_expiry_yyyymm(m.get("hname", ""))
        if not exp:
            return None
        return {
            "code": m["shcode"],
            "name": m["hname"].strip(),
            "expiry": exp.strftime("%Y%m%d"),
            "days_left": (exp - today).days,
            "multiplier": 10.0,
        }

    for basecode_full, contracts in by_base.items():
        # basecode "A069260" → "069260"
        if not basecode_full.startswith("A"):
            continue
        base_code = basecode_full[1:]
        info = stock_info.get(base_code)
        if not info:
            continue
        base_name, market = info

        # 만기 ascending
        contracts.sort(key=lambda m: _parse_expiry_yyyymm(m.get("hname", "")) or date.max)
        if not contracts:
            continue

        front = _build_leg(contracts[0])
        back = _build_leg(contracts[1]) if len(contracts) > 1 else None
        if not front:
            continue

        front_months.append(front["expiry"][:6])
        if back:
            back_months.append(back["expiry"][:6])

        # 스프레드 매칭 — front+back 만기 페어
        spread_code = None
        if back and basecode_full in spreads_by_base:
            f_ym = front["expiry"][:6]   # 예 "202605"
            b_ym = back["expiry"][:6]
            for sp in spreads_by_base[basecode_full]:
                hn = sp.get("hname", "")
                # SP hname: "TKG휴켐스 SP 2605-2607" 형식 또는 변형
                # 간단히 두 만기 모두 hname에 포함되는지 (YYMM 형식)
                f_yymm = f_ym[2:]
                b_yymm = b_ym[2:]
                if f_yymm in hn and b_yymm in hn:
                    spread_code = sp["shcode"]
                    break

        item = {
            "base_code": base_code,
            "base_name": base_name,
            "market": market,
            "front": front,
        }
        if back:
            item["back"] = back
        if spread_code:
            item["spread_code"] = spread_code
        items.append(item)

    fm = Counter(front_months).most_common(1)[0][0] if front_months else ""
    bm = Counter(back_months).most_common(1)[0][0] if back_months else ""

    payload = {
        "updated": today.isoformat(),
        "front_month": fm,
        "back_month": bm,
        "count": len(items),
        "items": sorted(items, key=lambda x: x["base_code"]),
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OUT_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        _json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp_path.replace(OUT_PATH)  # atomic write

    print(f"  [export] {len(items)} 종목 / front={fm} back={bm} → {OUT_PATH}")
    return {"items": len(items), "front_month": fm, "back_month": bm}


def run_krx_holidays_pipeline() -> dict:
    """
    KRX 휴장일 산출 → DB UPSERT → LENS JSON write.
    daily_update에서 매일 호출 — 임시공휴일 발표 / ohlcv 갱신 자동 반영.
    """
    from scripts.export_krx_holidays import run_export

    print("\n" + "=" * 70)
    print("  📅 KRX 휴장일 (DB SSoT + LENS export)")
    print("=" * 70)
    result = run_export()
    print(f"  ohlcv_max: {result['ohlcv_max']} / 산출 {result['computed']}건 "
          f"→ UPSERT {result['upserted']} / DELETE {result['deleted']}")
    print(f"  LENS JSON: {result['json_rows']}건 → {result['json_path']}")
    return result


def main(target_date: date = None, missing_only: bool = False):
    try:
        result = run_update(target_date, missing_only)

        # 휴장일/데이터 없음: 미니 보고서만 남기고 후속 파이프라인은 진행
        if result.get("skipped_holiday") or result.get("skipped_no_data"):
            end_date = result["end_date"]
            tag = "휴장일" if result.get("skipped_holiday") else "신규 영업일 없음"
            mini = (f"[{datetime.now(KST):%Y-%m-%d %H:%M:%S KST}] "
                    f"{end_date} {tag} — 주식 수집 skip\n")
            print("\n" + mini)
            fpath = REPORTS_DIR / f"daily_update_{end_date.strftime('%Y%m%d')}_skip.txt"
            fpath.write_text(mini, encoding="utf-8")
            print(f"📁 보고서 저장: {fpath}")
        else:
            report = generate_report(result)
            print("\n" + report)
            end_date = result["end_date"]
            fpath = save_report(report, end_date)
            print(f"\n📁 보고서 저장: {fpath}")

            # 품질 체크 (수집한 경우만)
            try:
                run_quality_checks(end_date)
            except Exception as qc_err:
                print(f"\n⚠️  품질 체크 중 오류 (업데이트 결과에는 영향 없음): {qc_err}")

        # 배당 공시 수집 + LENS export (휴장일에도 진행 — DART는 휴일에도 공시 등록 가능)
        try:
            run_dividend_pipeline(result["end_date"])
        except Exception as div_err:
            print(f"\n⚠️  배당 단계 오류 (업데이트 결과에는 영향 없음): {div_err}")

        # KRX 휴장일 SSoT 갱신 + LENS export
        try:
            run_krx_holidays_pipeline()
        except Exception as hol_err:
            print(f"\n⚠️  KRX 휴장일 단계 오류 (업데이트 결과에는 영향 없음): {hol_err}")

        # KOSPI200/KOSDAQ150 구성종목 SCD2 갱신 (휴장일이면 자체 skip)
        try:
            run_index_components_pipeline(result["end_date"])
        except Exception as idx_err:
            print(f"\n⚠️  지수 구성종목 단계 오류 (업데이트 결과에는 영향 없음): {idx_err}")

        # ETF 일별 스냅샷 (PDF + 마스터, 5일 FIFO)
        try:
            run_etf_daily_snapshot_pipeline(result["end_date"])
        except Exception as etf_err:
            print(f"\n⚠️  ETF 스냅샷 단계 오류 (업데이트 결과에는 영향 없음): {etf_err}")

        # 지수 + 선물 일별 OHLCV (인포맥스)
        try:
            run_indices_futures_daily_pipeline(result["end_date"])
        except Exception as idx_err:
            print(f"\n⚠️  지수+선물 단계 오류 (업데이트 결과에는 영향 없음): {idx_err}")

        # 분봉 일배치는 04:00 KST 별도 cron (job_minute_bars_daily)으로 분리됨
        # 주식선물 30초봉은 22:00 KST 별도 cron (job_stockfut_today, t8406 당일만)으로 분리됨
        # futures_master.json export도 분봉 일배치 끝에서 호출 — daily_update는 LS API 호출 0건
        # (이전 위치에서 04:00 일배치와 LS hit 동시 발생해 5xx 빈발)

    except Exception as e:
        err_msg = traceback.format_exc()
        print(f"\n❌ 업데이트 중 오류 발생:\n{err_msg}", file=sys.stderr)
        # 오류 보고서 저장
        today = datetime.now(KST).date()
        err_report = f"업데이트 실패\n실행시각: {datetime.now(KST)}\n\n{err_msg}"
        fpath = REPORTS_DIR / f"daily_update_{today.strftime('%Y%m%d')}_ERROR.txt"
        fpath.write_text(err_report, encoding="utf-8")
        sys.exit(1)


if __name__ == "__main__":
    # --missing-only 플래그 파싱
    missing_only_flag = "--missing-only" in sys.argv
    date_args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if date_args:
        try:
            td = datetime.strptime(date_args[0], "%Y%m%d").date()
        except ValueError:
            print("날짜 형식 오류. 사용법: python daily_update.py YYYYMMDD [--missing-only]")
            sys.exit(1)
    else:
        td = None

    if missing_only_flag and td is None:
        print("--missing-only는 날짜 지정 시에만 사용 가능합니다.")
        sys.exit(1)

    main(td, missing_only_flag)
