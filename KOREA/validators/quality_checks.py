"""
데이터 품질 체크 모듈

일별 수집 완료 후 데이터 이상 여부를 검사하고
결과를 data_quality_checks 테이블에 저장합니다.

체크 항목:
    NULL_CHECK
        - ohlcv_daily: close_price, volume NULL
        - investor_trading: net_buy_value, net_buy_volume NULL

    RANGE_CHECK
        - ohlcv_daily: 고가 < 저가, 고가 < 시가/종가, 종가 <= 0

    CONSISTENCY_CHECK
        - ohlcv_daily ↔ market_cap_daily 종목 수 불일치
        - investor_trading: KOSPI/KOSDAQ 종목 중 4개 투자자 유형 미달

사용법:
    python validators/quality_checks.py              # 어제 날짜 자동 체크
    python validators/quality_checks.py 20260220     # 특정 날짜 체크
"""

import sys
import json
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings

KST = ZoneInfo("Asia/Seoul")

INVESTOR_TYPES = {"FOREIGN", "INSTITUTION", "PENSION", "RETAIL"}


# ── DB 연결 ───────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


# ── 결과 저장 ─────────────────────────────────────────────────────────────────
def save_check_result(conn, table_name: str, check_date: date,
                      check_type: str, issue_count: int, details: dict):
    """체크 결과 1건을 data_quality_checks 테이블에 INSERT"""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO data_quality_checks
                (table_name, check_date, check_type, issue_count, details)
            VALUES (%s, %s, %s, %s, %s::jsonb)
        """, (
            table_name,
            check_date,
            check_type,
            issue_count,
            json.dumps(details, ensure_ascii=False, default=str),
        ))
    conn.commit()


# ── 날짜 데이터 존재 확인 ──────────────────────────────────────────────────────
def has_data_for_date(conn, check_date: date) -> bool:
    """체크 날짜에 ohlcv_daily 데이터가 있는지 확인 (휴장일 조기 종료용)"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM ohlcv_daily WHERE time = %s LIMIT 1",
            (check_date,),
        )
        return cur.fetchone() is not None


# ── NULL 체크 ─────────────────────────────────────────────────────────────────
def check_ohlcv_null(conn, check_date: date) -> dict:
    """
    ohlcv_daily NULL 체크

    - close_price IS NULL: 거래정지(volume=0) 제외
    - volume IS NULL
    """
    with conn.cursor() as cur:
        # 거래가 있는데 종가가 NULL인 종목
        cur.execute("""
            SELECT stock_code
            FROM ohlcv_daily
            WHERE time = %s
              AND close_price IS NULL
              AND (volume IS NULL OR volume > 0)
            ORDER BY stock_code
        """, (check_date,))
        null_close = [r[0] for r in cur.fetchall()]

        # volume 자체가 NULL
        cur.execute("""
            SELECT stock_code
            FROM ohlcv_daily
            WHERE time = %s AND volume IS NULL
            ORDER BY stock_code
        """, (check_date,))
        null_volume = [r[0] for r in cur.fetchall()]

    issue_count = len(null_close) + len(null_volume)
    details = {
        "null_close_price_count": len(null_close),
        "null_volume_count":      len(null_volume),
        "null_close_price_codes": null_close[:30],
        "null_volume_codes":      null_volume[:30],
    }
    return {
        "table":       "ohlcv_daily",
        "type":        "NULL_CHECK",
        "issue_count": issue_count,
        "details":     details,
    }


def check_investor_null(conn, check_date: date) -> dict:
    """
    investor_trading NULL 체크

    - net_buy_value 또는 net_buy_volume이 NULL인 레코드
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT stock_code, investor_type
            FROM investor_trading
            WHERE time = %s
              AND (net_buy_value IS NULL OR net_buy_volume IS NULL)
            ORDER BY stock_code, investor_type
        """, (check_date,))
        null_rows = cur.fetchall()

    issue_count = len(null_rows)
    details = {
        "null_row_count": issue_count,
        "samples": [
            {"stock_code": r[0], "investor_type": r[1]}
            for r in null_rows[:30]
        ],
    }
    return {
        "table":       "investor_trading",
        "type":        "NULL_CHECK",
        "issue_count": issue_count,
        "details":     details,
    }


# ── 범위/논리 체크 ────────────────────────────────────────────────────────────
def check_ohlcv_range(conn, check_date: date) -> dict:
    """
    ohlcv_daily 가격 논리 체크

    - 고가 < 저가
    - 고가 < 시가 또는 고가 < 종가
    - 종가 <= 0 (거래량 > 0인 종목)
    """
    with conn.cursor() as cur:
        # 고저가 역전 (high < low)
        cur.execute("""
            SELECT stock_code, high_price, low_price
            FROM ohlcv_daily
            WHERE time = %s
              AND high_price IS NOT NULL
              AND low_price IS NOT NULL
              AND high_price < low_price
        """, (check_date,))
        high_lt_low = cur.fetchall()

        # 고가가 시가 또는 종가보다 낮은 경우
        cur.execute("""
            SELECT stock_code, open_price, high_price, close_price
            FROM ohlcv_daily
            WHERE time = %s
              AND high_price IS NOT NULL
              AND (
                  (open_price  IS NOT NULL AND high_price < open_price)  OR
                  (close_price IS NOT NULL AND high_price < close_price)
              )
        """, (check_date,))
        high_anomaly = cur.fetchall()

        # 거래가 있는데 종가 <= 0
        cur.execute("""
            SELECT stock_code, close_price
            FROM ohlcv_daily
            WHERE time = %s
              AND close_price IS NOT NULL
              AND close_price <= 0
              AND volume > 0
        """, (check_date,))
        zero_price = cur.fetchall()

    issue_count = len(high_lt_low) + len(high_anomaly) + len(zero_price)
    details = {
        "high_lt_low_count":            len(high_lt_low),
        "high_lt_open_or_close_count":  len(high_anomaly),
        "zero_or_negative_price_count": len(zero_price),
        "high_lt_low_codes": [
            {"stock_code": r[0], "high": r[1], "low": r[2]}
            for r in high_lt_low
        ],
        "high_anomaly_codes": [
            {"stock_code": r[0], "open": r[1], "high": r[2], "close": r[3]}
            for r in high_anomaly[:20]
        ],
        "zero_price_codes": [
            {"stock_code": r[0], "close_price": r[1]}
            for r in zero_price[:20]
        ],
    }
    return {
        "table":       "ohlcv_daily",
        "type":        "RANGE_CHECK",
        "issue_count": issue_count,
        "details":     details,
    }


# ── 일관성 체크 ───────────────────────────────────────────────────────────────
def check_ohlcv_market_cap_consistency(conn, check_date: date) -> dict:
    """
    ohlcv_daily ↔ market_cap_daily 종목 불일치 체크

    - ohlcv에는 있지만 market_cap에 없는 종목
    - market_cap에는 있지만 ohlcv에 없는 종목
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT o.stock_code
            FROM ohlcv_daily o
            LEFT JOIN market_cap_daily m
                   ON o.time = m.time AND o.stock_code = m.stock_code
            WHERE o.time = %s AND m.stock_code IS NULL
            ORDER BY o.stock_code
        """, (check_date,))
        ohlcv_only = [r[0] for r in cur.fetchall()]

        cur.execute("""
            SELECT m.stock_code
            FROM market_cap_daily m
            LEFT JOIN ohlcv_daily o
                   ON m.time = o.time AND m.stock_code = o.stock_code
            WHERE m.time = %s AND o.stock_code IS NULL
            ORDER BY m.stock_code
        """, (check_date,))
        mktcap_only = [r[0] for r in cur.fetchall()]

    issue_count = len(ohlcv_only) + len(mktcap_only)
    details = {
        "ohlcv_only_count":      len(ohlcv_only),
        "market_cap_only_count": len(mktcap_only),
        "ohlcv_only_codes":      ohlcv_only[:30],
        "market_cap_only_codes": mktcap_only[:30],
    }
    return {
        "table":       "ohlcv_daily,market_cap_daily",
        "type":        "CONSISTENCY_CHECK",
        "issue_count": issue_count,
        "details":     details,
    }


def check_investor_type_completeness(conn, check_date: date) -> dict:
    """
    investor_trading 투자자 유형 완전성 체크

    KOSPI/KOSDAQ 활성 종목에 대해:
    - 4개 유형(FOREIGN/INSTITUTION/PENSION/RETAIL) 미만 수집된 종목
    - 수급 데이터가 아예 없는 종목
    """
    with conn.cursor() as cur:
        # 4개 미만 수집된 종목
        cur.execute("""
            SELECT it.stock_code, COUNT(DISTINCT it.investor_type) AS type_count,
                   ARRAY_AGG(DISTINCT it.investor_type ORDER BY it.investor_type) AS types
            FROM investor_trading it
            JOIN stocks s ON it.stock_code = s.stock_code
            WHERE it.time = %s
              AND s.is_active = TRUE
              AND s.market IN ('KOSPI', 'KOSDAQ')
            GROUP BY it.stock_code
            HAVING COUNT(DISTINCT it.investor_type) < 4
            ORDER BY it.stock_code
        """, (check_date,))
        incomplete = cur.fetchall()

        # 수급 데이터 자체가 없는 KOSPI/KOSDAQ 종목 수
        cur.execute("""
            SELECT COUNT(*)
            FROM stocks
            WHERE is_active = TRUE
              AND market IN ('KOSPI', 'KOSDAQ')
              AND stock_code NOT IN (
                  SELECT DISTINCT stock_code
                  FROM investor_trading
                  WHERE time = %s
              )
        """, (check_date,))
        missing_count = cur.fetchone()[0]

    issue_count = len(incomplete) + missing_count
    details = {
        "incomplete_type_count": len(incomplete),
        "missing_entirely_count": missing_count,
        "incomplete_samples": [
            {
                "stock_code": r[0],
                "type_count": r[1],
                "types_present": list(r[2]),
                "types_missing": sorted(INVESTOR_TYPES - set(r[2])),
            }
            for r in incomplete[:20]
        ],
    }
    return {
        "table":       "investor_trading",
        "type":        "CONSISTENCY_CHECK",
        "issue_count": issue_count,
        "details":     details,
    }


# ── 메인 진입점 ───────────────────────────────────────────────────────────────
def run_quality_checks(check_date: date = None) -> list[dict]:
    """
    모든 품질 체크 실행 후 DB에 저장

    Args:
        check_date: 체크할 날짜 (None이면 어제)

    Returns:
        체크 결과 목록 (각 항목: table, type, issue_count, details)
    """
    if check_date is None:
        check_date = datetime.now(KST).date() - timedelta(days=1)

    conn = get_conn()

    # 해당 날짜에 데이터가 없으면 조기 종료 (휴장일 등)
    if not has_data_for_date(conn, check_date):
        print(f"  ⏭  {check_date} 데이터 없음 (휴장일 또는 미수집) — 체크 생략")
        conn.close()
        return []

    checks = [
        check_ohlcv_null,
        check_ohlcv_range,
        check_investor_null,
        check_investor_type_completeness,
        check_ohlcv_market_cap_consistency,
    ]

    print(f"\n{'─'*60}")
    print(f"  데이터 품질 체크: {check_date}")
    print(f"{'─'*60}")

    results = []
    for check_fn in checks:
        try:
            result = check_fn(conn, check_date)
            save_check_result(
                conn,
                result["table"],
                check_date,
                result["type"],
                result["issue_count"],
                result["details"],
            )
            icon = "✅" if result["issue_count"] == 0 else "⚠️ "
            print(f"  {icon} [{result['type']}] {result['table']}: 이슈 {result['issue_count']}건")
            results.append(result)
        except Exception as e:
            print(f"  ❌ {check_fn.__name__} 실패: {e}")

    conn.close()

    total_issues = sum(r["issue_count"] for r in results)
    status = "이상 없음" if total_issues == 0 else f"총 {total_issues}건 이슈 발견"
    print(f"{'─'*60}")
    print(f"  결과: {status} ({len(results)}개 체크 완료)")
    print(f"{'─'*60}\n")

    return results


if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            target = datetime.strptime(sys.argv[1], "%Y%m%d").date()
        except ValueError:
            print("날짜 형식 오류. 사용법: python quality_checks.py YYYYMMDD")
            sys.exit(1)
    else:
        target = None

    run_quality_checks(target)
