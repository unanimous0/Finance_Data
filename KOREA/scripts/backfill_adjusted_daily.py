"""
일봉 수정주가(adjusted price) backfill — LS sujung=Y 활용.

대상: stocks.is_active=TRUE 전 종목 × 2022-01-03 ~ 어제 일봉
처리: ohlcv_daily의 (stock_code, time) 매칭 → adj_open/high/low/close + adj_factor 적재
정책: raw 컬럼(open_price, close_price 등)은 절대 안 건드림. adj_* 컬럼만 UPSERT.

사용:
    python scripts/backfill_adjusted_daily.py                       # 전 종목
    python scripts/backfill_adjusted_daily.py --codes 010120,005930 # 특정 종목
    python scripts/backfill_adjusted_daily.py --from 20220103 --to 20260515
    python scripts/backfill_adjusted_daily.py --limit 100           # 상위 100개 (sanity)
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg2
import psycopg2.extras

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from collectors.ls_api import LsApiClient, LsApiError
from config.settings import settings


UPDATE_SQL = """
UPDATE ohlcv_daily SET
    adj_open       = data.adj_open::numeric,
    adj_high       = data.adj_high::numeric,
    adj_low        = data.adj_low::numeric,
    adj_close      = data.adj_close::numeric,
    adj_factor     = data.adj_factor::numeric,
    adj_updated_at = NOW()
FROM (VALUES %s) AS data(time, stock_code, adj_open, adj_high, adj_low, adj_close, adj_factor)
WHERE ohlcv_daily.time = data.time::date
  AND ohlcv_daily.stock_code = data.stock_code
"""


def _conn():
    return psycopg2.connect(
        host=settings.DB_HOST, dbname=settings.DB_NAME,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
    )


def fetch_scope(conn) -> list[str]:
    """is_active=TRUE 전 종목 (ETF 포함)."""
    with conn.cursor() as cur:
        cur.execute("SELECT stock_code FROM stocks WHERE is_active = TRUE ORDER BY stock_code")
        return [r[0] for r in cur.fetchall()]


def _parse_ymd(s: str) -> date:
    return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))


def process_stock(client: LsApiClient, code: str, sdate: date, edate: date) -> tuple[int, str]:
    """한 종목 raw + adj 호출 → UPDATE rows 반환."""
    try:
        raw = client.get_daily_bars(code, sdate=sdate, edate=edate, sujung="N")
        adj = client.get_daily_bars(code, sdate=sdate, edate=edate, sujung="Y")
    except LsApiError as e:
        return 0, f"err:{e.category}"
    except Exception as e:
        return 0, f"exc:{type(e).__name__}"

    if not raw or not adj:
        return 0, "empty"

    raw_map = {b["date"]: b for b in raw if b.get("date")}
    adj_map = {b["date"]: b for b in adj if b.get("date")}

    rows = []
    for ymd, ab in adj_map.items():
        rb = raw_map.get(ymd)
        if not rb:
            continue
        raw_close = rb.get("close")
        adj_close = ab.get("close")
        if not raw_close or not adj_close:
            continue
        factor = adj_close / raw_close if raw_close else 1.0
        rows.append((
            _parse_ymd(ymd),
            code,
            ab.get("open"),
            ab.get("high"),
            ab.get("low"),
            adj_close,
            round(factor, 10),
        ))
    return rows, "ok"


def run_backfill(start: date, end: date, codes: list[str]) -> dict:
    client = LsApiClient()
    conn = _conn()
    try:
        total = len(codes)
        print(f"[일봉 adjusted 백필] {start} ~ {end} / 종목 {total}개 / TPS 1 → ~{total*2*1.05/60:.0f}분 예상")
        t0 = time.time()
        update_rows = 0
        ok = 0
        empty = 0
        err = 0
        empty_codes = []
        err_codes = []
        for i, code in enumerate(codes, 1):
            rows, status = process_stock(client, code, start, end)
            if status == "ok" and rows:
                with conn:
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_values(cur, UPDATE_SQL, rows, page_size=500)
                update_rows += len(rows)
                ok += 1
            elif status == "empty":
                empty += 1
                empty_codes.append(code)
            else:
                err += 1
                err_codes.append((code, status))
                print(f"  err {code}: {status}", flush=True)

            if i % 100 == 0 or i == total:
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed else 0
                eta_min = (total - i) / rate / 60 if rate else 0
                print(f"  [{i:>5}/{total} {i/total*100:.1f}%] UPDATE {update_rows:,}row / ok {ok} / 빈 {empty} / 에러 {err} / ETA {eta_min:.0f}min", flush=True)

        elapsed = time.time() - t0
        print(f"\n[완료] 소요 {elapsed/60:.1f}분 / ok {ok} / 빈 {empty} / 에러 {err} / UPDATE {update_rows:,}row")
        if empty_codes[:5]:
            print(f"  빈응답 샘플: {empty_codes[:5]}")
        if err_codes[:5]:
            print(f"  에러 샘플: {err_codes[:5]}")
        return {"ok": ok, "empty": empty, "errors": err, "rows": update_rows}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="일봉 수정주가 backfill (LS sujung=Y)")
    parser.add_argument("--from", dest="start", default="20220103", help="YYYYMMDD")
    parser.add_argument("--to",   dest="end",   default=None,        help="YYYYMMDD (기본: 어제)")
    parser.add_argument("--codes", default=None, help="콤마 구분 종목코드 (기본: is_active=TRUE 전체)")
    parser.add_argument("--limit", type=int, default=None, help="상위 N개 (sanity 용)")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y%m%d").date()
    end   = datetime.strptime(args.end, "%Y%m%d").date() if args.end else (date.today() - timedelta(days=1))

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        with _conn() as c:
            codes = fetch_scope(c)
        if args.limit:
            codes = codes[:args.limit]

    run_backfill(start, end, codes)


if __name__ == "__main__":
    main()
