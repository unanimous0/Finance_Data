"""
상장주식수 갱신 스크립트 — LS t1102 listing × 1000 → floating_shares.total_shares
- 대상: DB 활성 종목 전체 (KOSPI + KOSDAQ + ETF)
- 주기: 주 1회 (일요일 03:30 KST cron)
- floating_shares 테이블에 오늘 기준 upsert (base_date = today)
- 용도: daily_update STEP 1 market_cap = close × total_shares

단위: t1102 listing = 천주 → × 1000 = 주
"""

from __future__ import annotations

import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from collectors.ls_api import LsApiClient
from config.settings import settings

KST = ZoneInfo("Asia/Seoul")

UPSERT_SQL = """
INSERT INTO floating_shares (stock_code, base_date, total_shares)
VALUES %s
ON CONFLICT (stock_code, base_date) DO UPDATE SET
    total_shares = EXCLUDED.total_shares
"""


def get_conn():
    return psycopg2.connect(
        host=settings.DB_HOST, dbname=settings.DB_NAME,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
    )


def get_all_stocks(conn) -> list[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT stock_code, stock_name FROM stocks
            WHERE is_active = TRUE
              AND market IN ('KOSPI', 'KOSDAQ', 'ETF', 'KONEX')
            ORDER BY stock_code
        """)
        return cur.fetchall()


def main():
    today = date.today()
    conn = get_conn()
    ls = LsApiClient()
    url = f"{settings.LS_BASE_URL}/stock/market-data"

    try:
        stocks = get_all_stocks(conn)
        total = len(stocks)
        print(f"[상장주식수 갱신] {today} / {total}개 종목")

        batch = []
        ok = skip = err = 0

        for i, (code, name) in enumerate(stocks, 1):
            try:
                data = ls._post_generic("t1102", url, "t1102InBlock", {"shcode": code})
                out = data.get("t1102OutBlock") or {}
                listing = out.get("listing")
                if listing and int(listing) > 0:
                    total_shares = int(listing) * 1000
                    batch.append((code, today, total_shares))
                    ok += 1
                else:
                    skip += 1
            except Exception as e:
                print(f"  err {code} {name}: {e}", flush=True)
                err += 1

            if len(batch) >= 500:
                with conn:
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_values(cur, UPSERT_SQL, batch, page_size=200)
                batch.clear()

            if i % 500 == 0 or i == total:
                print(f"  [{i:>5}/{total}] ok={ok} skip={skip} err={err}", flush=True)

        if batch:
            with conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, UPSERT_SQL, batch, page_size=200)

        print(f"[완료] ok={ok} / skip={skip} / err={err}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
