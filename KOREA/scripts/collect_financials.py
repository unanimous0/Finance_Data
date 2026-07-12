"""
분기 재무지표 수집 (FnGuide XML)
- 대상: DB 활성 종목 전체 (ETF/ETN 제외)
- 주기: 분기별 1회 (각 분기 보고서 제출 마감 후)
- upsert: (stock_code, period_end, fs_type) 기준 덮어쓰기
  → 잠정(P) 시점에 저장 후 확정 시 자동 갱신
"""
import sys
import time
import logging
import argparse
from datetime import datetime

sys.path.insert(0, ".")
import psycopg2
from config.settings import settings
from collectors.fnguide import fetch_quarterly


def get_connection():
    return psycopg2.connect(
        host=settings.DB_HOST, dbname=settings.DB_NAME,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
    )

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

UPSERT_SQL = """
INSERT INTO financial_metrics_quarterly (
    stock_code, period_end, fs_type, data_type,
    revenue, operating_profit, net_income, controlling_ni,
    total_assets, total_equity, controlling_equity,
    eps, bps, dps,
    per, pbr, roe, roa, operating_margin,
    shares_outstanding, collected_at
) VALUES (
    %(stock_code)s, %(period_end)s, %(fs_type)s, %(data_type)s,
    %(revenue)s, %(operating_profit)s, %(net_income)s, %(controlling_ni)s,
    %(total_assets)s, %(total_equity)s, %(controlling_equity)s,
    %(eps)s, %(bps)s, %(dps)s,
    %(per)s, %(pbr)s, %(roe)s, %(roa)s, %(operating_margin)s,
    %(shares_outstanding)s, NOW()
)
ON CONFLICT (stock_code, period_end, fs_type) DO UPDATE SET
    data_type           = EXCLUDED.data_type,
    revenue             = EXCLUDED.revenue,
    operating_profit    = EXCLUDED.operating_profit,
    net_income          = EXCLUDED.net_income,
    controlling_ni      = EXCLUDED.controlling_ni,
    total_assets        = EXCLUDED.total_assets,
    total_equity        = EXCLUDED.total_equity,
    controlling_equity  = EXCLUDED.controlling_equity,
    eps                 = COALESCE(EXCLUDED.eps,  financial_metrics_quarterly.eps),
    bps                 = COALESCE(EXCLUDED.bps,  financial_metrics_quarterly.bps),
    dps                 = COALESCE(EXCLUDED.dps,  financial_metrics_quarterly.dps),
    per                 = EXCLUDED.per,
    pbr                 = EXCLUDED.pbr,
    roe                 = EXCLUDED.roe,
    roa                 = EXCLUDED.roa,
    operating_margin    = EXCLUDED.operating_margin,
    shares_outstanding  = EXCLUDED.shares_outstanding,
    collected_at        = NOW()
"""


def get_target_stocks(conn) -> list[str]:
    """활성 주식 종목 (ETF/ETN 제외)"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT stock_code FROM stocks
            WHERE is_active = TRUE
              AND market IN ('KOSPI', 'KOSDAQ')
            ORDER BY stock_code
        """)
        return [r[0] for r in cur.fetchall()]


def run(stock_codes: list[str] | None = None, delay: float = 0.5):
    conn = get_connection()
    try:
        targets = stock_codes or get_target_stocks(conn)
        total = len(targets)
        log.info("수집 대상: %d 종목", total)

        inserted = updated = skipped = 0
        for i, code in enumerate(targets, 1):
            rows = fetch_quarterly(code)
            if not rows:
                skipped += 1
                if i % 100 == 0:
                    log.info("[%d/%d] %s 건너뜀 (데이터 없음)", i, total, code)
                time.sleep(delay)
                continue

            with conn.cursor() as cur:
                for row in rows:
                    if row["period_end"] is None:
                        continue
                    try:
                        cur.execute(UPSERT_SQL, row)
                    except Exception as e:
                        conn.rollback()
                        log.warning("upsert 실패 %s %s %s: %s", code, row.get("period_end"), row.get("fs_type"), e)
                        continue
            conn.commit()
            inserted += len(rows)

            if i % 50 == 0 or i == total:
                log.info("[%d/%d] %s: %d레코드 upsert (누적 %d건)", i, total, code, len(rows), inserted)

            time.sleep(delay)

        log.info("완료 — upsert %d건, 건너뜀 %d종목", inserted, skipped)
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("codes", nargs="*", help="특정 종목코드 (미입력시 전체)")
    parser.add_argument("--delay", type=float, default=0.5, help="요청 간격(초)")
    args = parser.parse_args()
    run(stock_codes=args.codes or None, delay=args.delay)
