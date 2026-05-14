"""
지수선물 30초봉 백필 (LS t8465, /futureoption/chart)

스코프: KP200 F + KQ150 F **현재 살아있는 모든 단일선물** (스프레드 SP 제외).
        각 contract는 자기 유효 구간 ([prev_prev_quarterly_expiry+1, 만기일]) 안에서만 fetch.

정책 (사용자):
- 각 contract 데이터는 "처음 NEXT(원월)이 됐을 때부터" 만기일까지만 의미 있음
- NEXT 시작 = 직전 직전 분기 만기일+1 (= 두 분기 전 contract 만기 직후 = NEXT로 격상)
- 그 이전엔 farther future라 거래량 거의 0 → fetch 무의미

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


def fetch_index_futures_master(client: LsApiClient,
                               near_next_only: date = None) -> list[tuple[str, str]]:
    """KP200 F + KQ150 F 단일선물 (스프레드 SP 제외).

    near_next_only=None (default): 살아있는 모든 단일선물 (백필용 — 각 contract 유효 구간 내 fetch)
    near_next_only=date: 그 날짜 기준 NEAR + NEXT 4개만 (daily cron용)
    """
    from collectors.ls_api import select_near_next_two

    master: list[dict] = []
    d = client._post_generic("t8467",
        f"{BASE_URL}/futureoption/market-data", "t8467InBlock", {"gubun": ""})
    master.extend(d.get("t8467OutBlock", []) or [])
    d = client._post_generic("t8435",
        f"{BASE_URL}/futureoption/market-data", "t8435InBlock", {"gubun": "SF"})
    master.extend(d.get("t8435OutBlock", []) or [])

    actives = [m for m in master if "SP" not in m.get("hname", "")]

    if near_next_only is not None:
        def _gk(m):
            return "KOSDAQ150" if m.get("hname", "").strip().startswith("KQF") else "KOSPI200"
        actives = select_near_next_two(actives, near_next_only, group_key=_gk)

    return [(m["shcode"], m["hname"]) for m in actives]


def _second_thursday(year: int, month: int) -> date:
    d = date(year, month, 1)
    offset = (3 - d.weekday()) % 7
    return d + timedelta(days=offset + 7)


def _useful_start_date(contract_expiry: date) -> date:
    """contract가 NEXT(원월)으로 격상된 날 = 직전 직전 분기 만기일+1.
    예: 6월물 만기=6/11/26 → prev_prev=12월물 만기 12/11/25 → useful_start = 12/12/25
    예: 9월물 만기=9/10/26 → prev_prev=3월물 만기 3/12/26 → useful_start = 3/13/26"""
    Q = [3, 6, 9, 12]
    q_idx = Q.index(contract_expiry.month)
    pp_year = contract_expiry.year if q_idx >= 2 else contract_expiry.year - 1
    pp_month = Q[(q_idx - 2) % 4]
    return _second_thursday(pp_year, pp_month) + timedelta(days=1)


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


def run_backfill(start: date, end: date, codes_with_hname: list[tuple[str, str]], ncnt: int = 0):
    """codes_with_hname = [(shcode, hname), ...] — hname에서 만기 파싱해 유효 구간 결정."""
    from collectors.ls_api import _parse_expiry_yyyymm

    client = LsApiClient()
    conn = _conn()
    try:
        biz_days = fetch_business_days(conn, start, end)
        if not biz_days:
            print(f"  [지수선물 백필] 거래일 0건")
            return

        # 각 contract 유효 구간 계산
        useful_ranges: dict[str, tuple[date, date]] = {}
        for sh, hn in codes_with_hname:
            exp = _parse_expiry_yyyymm(hn)
            if not exp:
                print(f"    [skip] {sh} {hn} — 만기 파싱 실패")
                continue
            us = _useful_start_date(exp)
            useful_ranges[sh] = (us, exp)

        # (day, code) pair 생성 — useful range 안에서만
        plan: list[tuple[date, str]] = []
        for day in biz_days:
            for sh, _ in codes_with_hname:
                if sh not in useful_ranges:
                    continue
                us, exp = useful_ranges[sh]
                if us <= day <= exp:
                    plan.append((day, sh))

        total = len(plan)
        if total == 0:
            print(f"  [지수선물 백필] 유효 구간 안에 든 (day,code) 0건")
            return

        print(f"  [지수선물 백필] {start}~{end} 거래일 {len(biz_days)}일 × 선물 {len(useful_ranges)}개"
              f" → 유효 호출 {total}건 / {total*1.05/60:.1f}분")
        for sh, hn in codes_with_hname:
            if sh in useful_ranges:
                us, exp = useful_ranges[sh]
                print(f"    {sh} {hn}: useful {us} ~ {exp}")
        interval_seconds = 30 if ncnt == 0 else 60

        total_rows = 0
        empty = 0
        errors: list[tuple] = []
        t0 = time.time()
        completed = 0

        for day, code in plan:
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

    cli = LsApiClient()
    if args.codes:
        # --codes로 명시한 경우 hname을 master에서 lookup
        master = fetch_index_futures_master(cli)
        master_map = {sh: hn for sh, hn in master}
        codes_with_hname = []
        for c in args.codes.split(","):
            c = c.strip()
            if not c: continue
            hn = master_map.get(c)
            if hn is None:
                print(f"  [warn] {c} master에 없음 (만기됐을 수 있음) — 만기 모르므로 skip")
                continue
            codes_with_hname.append((c, hn))
    else:
        master = fetch_index_futures_master(cli)
        print(f"[지수선물 master] {len(master)}개 단일선물 발견 (KP+KQ, 모든 살아있는 만기)")
        for sh, hn in master:
            print(f"    {sh}  {hn}")
        codes_with_hname = master

    print(f"[지수선물 30초봉 백필] {start} ~ {end} / 선물 {len(codes_with_hname)}개 / ncnt={args.ncnt}")
    run_backfill(start, end, codes_with_hname, ncnt=args.ncnt)


if __name__ == "__main__":
    main()
