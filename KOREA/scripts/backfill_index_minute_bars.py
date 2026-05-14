"""
지수 30초봉 백필 (LS t8418, /indtp/chart)

스코프(default): KOSPI200(101) + KOSDAQ150(301) — 사용자 정책
                 --codes로 임의 LS 지수코드 override 가능 (수동 디버그용)
                 --all-master 플래그로 t8424 전체업종 마스터 사용 (예외 케이스)
기간: --from (default 2026-01-02) ~ --to (default 어제)
TPS 1 단일 워커, 1지수 1일 ≈ 1.05초

사용:
    # 기본 백필 (KOSPI200 + KOSDAQ150 × 2026-01-02 ~ 어제)
    python scripts/backfill_index_minute_bars.py

    # 특정 기간/특정 지수
    python scripts/backfill_index_minute_bars.py --from 20260102 --to 20260301
    python scripts/backfill_index_minute_bars.py --codes 101,301,001

    # 전체 지수 master (t8424) — 예외 사용
    python scripts/backfill_index_minute_bars.py --all-master
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
INSERT INTO index_ohlcv_intraday
    (index_code, time, interval_seconds, open, high, low, close, volume, trading_value)
VALUES %s
ON CONFLICT (index_code, time, interval_seconds) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, volume = EXCLUDED.volume,
    trading_value = EXCLUDED.trading_value
"""


def _conn():
    return psycopg2.connect(
        host=settings.DB_HOST, dbname=settings.DB_NAME,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
    )


def fetch_business_days(conn, start: date, end: date) -> list[date]:
    """ohlcv_daily 거래일 (휴장일 자동 제외)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT time FROM ohlcv_daily WHERE time BETWEEN %s AND %s ORDER BY time",
            (start, end),
        )
        return [r[0] for r in cur.fetchall()]


def fetch_index_master(client: LsApiClient) -> list[tuple[str, str]]:
    """t8424 전체업종 호출 → [(upcode, hname), ...] 반환."""
    data = client._post_generic("t8424",
        f"{BASE_URL}/indtp/market-data", "t8424InBlock", {"gubun1": ""})
    if data.get("_no_data"):
        return []
    rows = data.get("t8424OutBlock", []) or []
    return [(r["upcode"], r["hname"]) for r in rows if r.get("upcode")]


def insert_bars(conn, index_code: str, bars: list[dict], interval_seconds: int) -> int:
    rows = [LsApiClient.index_bar_to_db_row(index_code, b, interval_seconds) for b in bars]
    rows = [r for r in rows if r]
    if not rows:
        return 0
    values = [(r["index_code"], r["time"], r["interval_seconds"],
               r["open"], r["high"], r["low"], r["close"],
               r["volume"], r["trading_value"]) for r in rows]
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
            print(f"  [지수 백필] 거래일 0건 ({start}~{end})")
            return
        print(f"  [지수 백필] {start}~{end} 거래일 {len(biz_days)}일 × 지수 {len(codes)}개"
              f" ≈ 예상 {len(biz_days)*len(codes)}호출 / {len(biz_days)*len(codes)*1.05/60:.0f}분")
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
                    bars = client.get_index_intraday_bars(code, day, ncnt=ncnt)
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

                if completed % 100 == 0:
                    elapsed = time.time() - t0
                    pct = completed / total * 100
                    rate = completed / elapsed if elapsed else 0
                    eta_min = (total - completed) / rate / 60 if rate else 0
                    print(f"    [{completed}/{total} {pct:.2f}%] 적재 {total_rows:,}row "
                          f"/ 빈 {empty} / 에러 {len(errors)} / ETA {eta_min:.0f}min", flush=True)

        elapsed = time.time() - t0
        print(f"\n  [지수 백필 완료] 소요 {elapsed/60:.1f}분 / 적재 {total_rows:,}row "
              f"/ 빈 {empty} / 에러 {len(errors)}")
        if errors:
            from collections import Counter
            cat = Counter(e[2] for e in errors)
            print(f"    에러 카테고리: {dict(cat)}  sample: {errors[:5]}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="지수 30초봉 백필 (t8418)")
    parser.add_argument("--from", dest="start", default="20260102", help="YYYYMMDD")
    parser.add_argument("--to",   dest="end",   default=None,        help="YYYYMMDD (기본: 어제)")
    parser.add_argument("--codes", default=None, help="콤마 구분 LS 지수코드 (기본: 101,301)")
    parser.add_argument("--all-master", action="store_true",
                        help="t8424 전체업종 master 사용 (예외 — 평소 KOSPI200/KOSDAQ150만)")
    parser.add_argument("--ncnt", type=int, default=0, help="0=30초봉(default), 1=1분봉")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y%m%d").date()
    end   = datetime.strptime(args.end, "%Y%m%d").date() if args.end else (date.today() - timedelta(days=1))

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.all_master:
        cli = LsApiClient()
        master = fetch_index_master(cli)
        print(f"[지수 master] t8424로 {len(master)}개 지수 발견 (--all-master)")
        codes = [c for c, _ in master]
    else:
        codes = ["101", "301"]  # KOSPI200, KOSDAQ150 (사용자 정책)
        print(f"[지수] 기본 스코프 KOSPI200(101) + KOSDAQ150(301)")

    print(f"[지수 30초봉 백필] {start} ~ {end} / 지수 {len(codes)}개 / ncnt={args.ncnt}")
    run_backfill(start, end, codes, ncnt=args.ncnt)


if __name__ == "__main__":
    main()
