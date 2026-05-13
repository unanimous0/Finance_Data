"""
지수선물 30초봉 백필 (LS t8465, /futureoption/chart)

스코프: t8467 지수선물 마스터 → 단일선물(F XXX)만, 스프레드(SP) 제외
기간: --from (default 2026-01-02) ~ --to (default 어제)

주식선물은 t8406이 historical 불가능 → 별도 스크립트 없음 (당일 일배치만 가능)

사용:
    python scripts/backfill_futures_minute_bars.py
    python scripts/backfill_futures_minute_bars.py --codes A0166000,A0666000
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

from collectors.ls_api import LsApiClient, LsApiError, BASE_URL
from config.settings import settings


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


def _conn():
    return psycopg2.connect(
        host=settings.DB_HOST, dbname=settings.DB_NAME,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
    )


def fetch_business_days(conn, start: date, end: date) -> list[date]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT time FROM ohlcv_daily WHERE time BETWEEN %s AND %s ORDER BY time",
            (start, end),
        )
        return [r[0] for r in cur.fetchall()]


def fetch_index_futures_master(client: LsApiClient) -> list[tuple[str, str]]:
    """KOSPI200 F + KOSDAQ150 F 중 근월 + 다음월물만 (총 4개).
    매일 호출 시 만기 임박하면 자동 갱신 (select_near_next_two).
    """
    from collectors.ls_api import select_near_next_two
    from datetime import date as _date
    master: list[dict] = []
    # KOSPI200 F (t8467)
    d = client._post_generic("t8467",
        f"{BASE_URL}/futureoption/market-data", "t8467InBlock", {"gubun": ""})
    master.extend(d.get("t8467OutBlock", []) or [])
    # KOSDAQ150 F (t8435 gubun=SF)
    d = client._post_generic("t8435",
        f"{BASE_URL}/futureoption/market-data", "t8435InBlock", {"gubun": "SF"})
    master.extend(d.get("t8435OutBlock", []) or [])

    def _gk(m):
        h = m.get("hname", "")
        return "KOSDAQ150" if h.strip().startswith("KQF") else "KOSPI200"

    active = select_near_next_two(master, _date.today(), group_key=_gk)
    return [(m["shcode"], m["hname"]) for m in active]


def insert_bars(conn, futures_code: str, bars: list[dict], interval_seconds: int) -> int:
    rows = [LsApiClient.futures_bar_to_db_row(futures_code, b, interval_seconds) for b in bars]
    rows = [r for r in rows if r]
    if not rows:
        return 0
    values = [(r["futures_code"], r["time"], r["interval_seconds"],
               r["open"], r["high"], r["low"], r["close"],
               r["volume"], r["trading_value"], r["open_interest"]) for r in rows]
    with conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, INSERT_SQL, values, page_size=500)
    return len(rows)


def run_backfill(start: date, end: date, codes: list[str], ncnt: int = 0):
    client = LsApiClient()
    conn = _conn()
    try:
        biz_days = fetch_business_days(conn, start, end)
        if not biz_days:
            print(f"  [지수선물 백필] 거래일 0건")
            return
        print(f"  [지수선물 백필] {start}~{end} 거래일 {len(biz_days)}일 × 선물 {len(codes)}개"
              f" ≈ 예상 {len(biz_days)*len(codes)}호출 / {len(biz_days)*len(codes)*1.05/60:.1f}분")
        interval_seconds = 30 if ncnt == 0 else 60

        total_rows = 0
        empty = 0
        errors: list[tuple] = []
        t0 = time.time()
        completed = 0
        total = len(biz_days) * len(codes)

        for di, day in enumerate(biz_days, 1):
            print(f"  ▶ day {di}/{len(biz_days)} = {day}", flush=True)
            for ci, code in enumerate(codes, 1):
                completed += 1
                t_call = time.time()
                try:
                    bars = client.get_futures_intraday_bars(code, day, ncnt=ncnt)
                    if not bars:
                        empty += 1
                        continue
                    n = insert_bars(conn, code, bars, interval_seconds)
                    total_rows += n
                    dur = time.time() - t_call
                    if dur > 5.0:
                        print(f"    slow {code} {dur:.1f}s {n}row", flush=True)
                except LsApiError as e:
                    errors.append((code, day, e.category))
                    print(f"    err {code} {day} {e.category}", flush=True)
                except Exception as e:
                    errors.append((code, day, f"unexpected:{type(e).__name__}"))
                    print(f"    EXC {code} {day} {type(e).__name__}", flush=True)

                if completed % 50 == 0:
                    elapsed = time.time() - t0
                    pct = completed / total * 100
                    rate = completed / elapsed if elapsed else 0
                    eta_min = (total - completed) / rate / 60 if rate else 0
                    print(f"    [{completed}/{total} {pct:.2f}%] 적재 {total_rows:,}row "
                          f"/ 빈 {empty} / 에러 {len(errors)} / ETA {eta_min:.0f}min", flush=True)

        elapsed = time.time() - t0
        print(f"\n  [지수선물 백필 완료] 소요 {elapsed/60:.1f}분 / 적재 {total_rows:,}row "
              f"/ 빈 {empty} / 에러 {len(errors)}")
        if errors:
            from collections import Counter
            cat = Counter(e[2] for e in errors)
            print(f"    에러: {dict(cat)}  sample: {errors[:5]}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="지수선물 30초봉 백필 (t8465)")
    parser.add_argument("--from", dest="start", default="20260102", help="YYYYMMDD")
    parser.add_argument("--to",   dest="end",   default=None,        help="YYYYMMDD (기본: 어제)")
    parser.add_argument("--codes", default=None, help="콤마 구분 LS 선물코드 (기본: t8467 단일선물)")
    parser.add_argument("--ncnt", type=int, default=0, help="0=30초봉(default), 1=1분봉")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y%m%d").date()
    end   = datetime.strptime(args.end, "%Y%m%d").date() if args.end else (date.today() - timedelta(days=1))

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        cli = LsApiClient()
        master = fetch_index_futures_master(cli)
        print(f"[지수선물 master] t8467로 {len(master)}개 단일선물 발견")
        for sh, hn in master:
            print(f"    {sh}  {hn}")
        codes = [c for c, _ in master]

    print(f"[지수선물 30초봉 백필] {start} ~ {end} / 선물 {len(codes)}개 / ncnt={args.ncnt}")
    run_backfill(start, end, codes, ncnt=args.ncnt)


if __name__ == "__main__":
    main()
