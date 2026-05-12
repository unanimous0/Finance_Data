"""
30초봉 백필 (LS t1302 gubun=0)

스코프 (Phase 6): scripts/_minute_scope.py 의 fetch_minute_scope()
- KOSPI200 + KOSDAQ150 (active 멤버) + 한국 ETF (해외 키워드 제외)
- 약 981종목

운영 자체:
- TPS 1 단일 워커 → 1종목 1일 약 1초 (페이징 cnt=900으로 1일=1호출)
- 86거래일 × 981종목 ≈ 23.5시간

재실행 안전:
- INSERT ON CONFLICT DO UPDATE — 동일 (stock_code, time) 갱신
- 휴장일은 LS API 자동 빈 응답 → skip
- 실패 종목 별도 retry list 산출

사용:
    # sanity (50종목 × 5일)
    python scripts/backfill_30sec_bars.py --from 20260504 --to 20260508 --limit 50

    # 본격 (전체 스코프, 2026-01-02 ~ 어제)
    python scripts/backfill_30sec_bars.py --from 20260102

    # 특정 종목만
    python scripts/backfill_30sec_bars.py --from 20260504 --to 20260508 --codes 005930,000660
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
from scripts._minute_scope import fetch_minute_scope


INSERT_SQL = """
INSERT INTO ohlcv_intraday
    (stock_code, time, exchange, interval_seconds, open, high, low, close, volume, trading_value)
VALUES %s
ON CONFLICT (stock_code, time, exchange, interval_seconds) DO UPDATE SET
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
    """ohlcv_daily의 거래일 (휴장일 제외 — 진실 기반)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT time FROM ohlcv_daily WHERE time BETWEEN %s AND %s ORDER BY time",
            (start, end),
        )
        return [r[0] for r in cur.fetchall()]


def insert_bars(conn, code: str, bars: list[dict], interval_seconds: int) -> int:
    """t8452 응답 봉 list → ohlcv_intraday INSERT (멱등)"""
    rows = [LsApiClient.t8452_to_db_row(code, b, interval_seconds) for b in bars]
    rows = [r for r in rows if r]
    if not rows:
        return 0
    values = [(r["stock_code"], r["time"], r["exchange"], r["interval_seconds"],
               r["open"], r["high"], r["low"], r["close"],
               r["volume"], r["trading_value"]) for r in rows]
    with conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, INSERT_SQL, values, page_size=500)
    return len(rows)


def run_backfill(start: date, end: date, codes: list[str]) -> dict:
    client = LsApiClient()
    conn = _conn()
    try:
        biz_days = fetch_business_days(conn, start, end)
        if not biz_days:
            print(f"  [백필] 거래일 0건 ({start}~{end})")
            return {"days": 0, "stocks": 0, "rows": 0, "errors": []}

        print(f"  [백필] {start}~{end} 거래일 {len(biz_days)}일 × 종목 {len(codes)}개"
              f" ≈ 예상 {len(biz_days) * len(codes)}호출 / {len(biz_days) * len(codes) * 1.05 / 60:.0f}분")

        total_rows = 0
        errors: list[tuple[str, date, str]] = []  # (code, day, category)
        empty_days = 0
        t0 = time.time()
        completed = 0
        for di, day in enumerate(biz_days, 1):
            print(f"  ▶ day {di}/{len(biz_days)} = {day}", flush=True)
            for ci, code in enumerate(codes, 1):
                completed += 1
                t_call = time.time()
                try:
                    bars, interval = client.get_intraday_bars(code, day)
                    if not bars:
                        empty_days += 1
                        continue
                    n = insert_bars(conn, code, bars, interval)
                    total_rows += n
                    # 호출 5초 이상 걸린 종목만 로그
                    dur = time.time() - t_call
                    if dur > 5.0:
                        print(f"    slow {code} {dur:.1f}s {n}row", flush=True)
                except LsApiError as e:
                    errors.append((code, day, e.category))
                    print(f"    err {code} {e.category} {time.time()-t_call:.1f}s", flush=True)
                except Exception as e:
                    errors.append((code, day, f"unexpected:{type(e).__name__}"))
                    print(f"    EXC {code} {type(e).__name__} {time.time()-t_call:.1f}s", flush=True)

                # 진행 로그 — 100호출마다 + 매 5분 heartbeat
                if completed % 100 == 0 or (time.time() - t0) % 300 < 1:
                    elapsed = time.time() - t0
                    pct = completed / (len(biz_days) * len(codes)) * 100
                    rate = completed / elapsed if elapsed else 0
                    eta_min = (len(biz_days) * len(codes) - completed) / rate / 60 if rate else 0
                    print(f"    [{completed}/{len(biz_days)*len(codes)} {pct:.2f}%] "
                          f"적재 {total_rows:,}row / 빈응답 {empty_days} / 에러 {len(errors)} "
                          f"/ ETA {eta_min:.0f}min", flush=True)

        elapsed = time.time() - t0
        print(f"\n  [백필 완료] 소요 {elapsed/60:.1f}분 / 적재 {total_rows:,}row "
              f"/ 빈응답 {empty_days} / 에러 {len(errors)}")
        if errors:
            from collections import Counter
            cat_count = Counter(e[2] for e in errors)
            print(f"    에러 카테고리: {dict(cat_count)}")
            print(f"    샘플 (최대 10): {errors[:10]}")
        return {
            "days": len(biz_days), "stocks": len(codes), "rows": total_rows,
            "empty_days": empty_days, "errors": errors,
        }
    finally:
        conn.close()


def verify_sanity(start: date, end: date, codes: list[str], tolerance_pct: float = 5.0):
    """30초봉 SUM(volume) vs ohlcv_daily.volume 비교 (±tolerance% 이내인지)."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                WITH m AS (
                    SELECT stock_code, (time AT TIME ZONE 'Asia/Seoul')::date AS d,
                           SUM(volume) AS m_vol, SUM(trading_value) AS m_tv
                      FROM ohlcv_intraday
                     WHERE stock_code = ANY(%s) AND time >= %s AND time < %s + INTERVAL '1 day'
                     GROUP BY stock_code, d
                )
                SELECT m.stock_code, m.d, m.m_vol, d.volume,
                       ABS(m.m_vol - d.volume)::float / NULLIF(d.volume,0) * 100 AS diff_pct
                  FROM m
                  JOIN ohlcv_daily d
                    ON d.stock_code = m.stock_code AND d.time = m.d
                 ORDER BY diff_pct DESC NULLS LAST
            """, (codes, start, end))
            rows = cur.fetchall()

        if not rows:
            print("  [verify] 비교 가능한 행 없음")
            return

        within = sum(1 for r in rows if r[4] is not None and r[4] <= tolerance_pct)
        outside = [r for r in rows if r[4] is None or r[4] > tolerance_pct]
        avg_diff = sum(r[4] for r in rows if r[4] is not None) / len(rows)

        print(f"\n  [Sanity] {len(rows)}개 (종목, 일자) 페어 검증")
        print(f"    ±{tolerance_pct}% 이내: {within}/{len(rows)} ({within/len(rows)*100:.1f}%)")
        print(f"    평균 |차이|: {avg_diff:.2f}%")
        if outside:
            print(f"    범위 밖 상위 5:")
            for code, d, mv, dv, diff in outside[:5]:
                print(f"      {code} {d}: 30초봉 SUM={mv} vs daily {dv} → diff={diff}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="30초봉 백필")
    parser.add_argument("--from", dest="start", required=True, help="YYYYMMDD")
    parser.add_argument("--to",   dest="end",   default=None,  help="YYYYMMDD (기본: 어제)")
    parser.add_argument("--codes", default=None, help="콤마로 구분된 종목코드 (기본: 전체 스코프)")
    parser.add_argument("--limit", type=int, default=None, help="스코프 상위 N개로 제한 (sanity 용도)")
    parser.add_argument("--no-verify", action="store_true", help="자동 검증 skip")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y%m%d").date()
    end   = datetime.strptime(args.end, "%Y%m%d").date() if args.end else (date.today() - timedelta(days=1))

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        with _conn() as c:
            codes = fetch_minute_scope(c)
        if args.limit:
            codes = codes[:args.limit]

    print(f"[30초봉 백필] {start} ~ {end} / 종목 {len(codes)}개")
    result = run_backfill(start, end, codes)

    if not args.no_verify and result["rows"] > 0:
        verify_sanity(start, end, codes)


if __name__ == "__main__":
    main()
