"""
분봉 절단 복구 (LS tr_cont 페이징 누락 사고, 2026-08-30)

배경:
    collectors/ls_api._post_generic 이 tr_cont/tr_cont_key 헤더를 "N"/"" 로 하드코딩해
    t8418(지수)·t8465(지수선물)이 2페이지를 요청해도 서버가 첫 페이지를 재전송했다.
    결과로 **하루 정확히 500봉만** 적재 (지수 11:11~15:30 / 지수선물 11:26~15:45).
    오전장이 통째로 빠진 상태가 2026-01-02부터 8개월간 누적됐다.
    t8452(종목)는 tr_cont를 제대로 넘기고 있었으나 간헐적으로 같은 signature가 43건 발생.

    헤더 수정 후, 이미 잘려 적재된 과거분을 재수신해 메우는 스크립트.
    UPSERT 라 멱등 — 중단 후 재실행하면 남은 것만 다시 집는다.

복구 가능 범위 (실측, 2026-08-30):
    t8465 지수선물 : 2025-12 ~ 현재 → 2026년 전 구간 복구 가능
    t8452 종목     : historical 제공 → 복구 가능
    t8418 지수     : **직전 1세션만** 제공 → 과거 복구 불가 (별도 방안 필요)

사용:
    python scripts/repair_intraday_truncation.py --target futures        # 지수선물
    python scripts/repair_intraday_truncation.py --target stock          # 종목/ETF
    python scripts/repair_intraday_truncation.py --target futures --dry-run
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import psycopg2

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from collectors.ls_api import LsApiClient, LsApiError, select_ncnt
from config.settings import settings

# 하루 정상 봉수 (페이징 완주 실측, 2026-08-28 기준)
#   지수선물 821봉 08:45:30~15:45 / 지수 761봉 09:00:30~15:30 / 종목 760~761봉
# 절단 판정은 넉넉히 잡는다 — 거래 없는 종목이 자연히 적은 경우와 구분하려고
# "정확히 500" 이 아니라 임계 이하로 보되, 재수신 후 개선이 없으면 그대로 둔다.
TRUNC_THRESHOLD = {"futures": 600, "stock": 600}


def _conn():
    return psycopg2.connect(
        host=settings.DB_HOST, dbname=settings.DB_NAME,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
    )


def find_truncated(conn, target: str, start: date, end: date) -> list[tuple[str, date, int]]:
    """절단 의심 (code, day, 현재봉수) 목록. 봉수 오름차순."""
    tbl, col = (("futures_ohlcv_intraday", "futures_code") if target == "futures"
                else ("ohlcv_intraday", "stock_code"))
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Asia/Seoul'")
        cur.execute(f"""
            SELECT {col}, (time AT TIME ZONE 'Asia/Seoul')::date AS d, count(*) AS bars
            FROM {tbl}
            WHERE interval_seconds = 30
              AND time >= %s::date AND time < (%s::date + 1)
            GROUP BY 1, 2
            HAVING count(*) <= %s
            ORDER BY 2, 1
        """, (start, end, TRUNC_THRESHOLD[target]))
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def _refetch(client, target: str, code: str, day: date):
    """(bars, interval_seconds) 반환."""
    if target == "futures":
        return client.get_futures_intraday_bars(code, day, ncnt=0), 30
    bars, interval = client.get_intraday_bars(code, day)
    return bars, interval


def _insert(conn, target: str, code: str, bars: list[dict], interval: int) -> int:
    if target == "futures":
        from scripts.backfill_futures_minute_bars import insert_bars
    else:
        from scripts.backfill_30sec_bars import insert_bars
    return insert_bars(conn, code, bars, interval)


def run(target: str, start: date, end: date, dry_run: bool = False) -> dict:
    conn = _conn()
    try:
        plan = find_truncated(conn, target, start, end)
        if not plan:
            print(f"  [{target}] 절단 code-day 0건 — 복구할 것 없음")
            return {"planned": 0}

        before_total = sum(p[2] for p in plan)
        days = sorted({p[1] for p in plan})
        codes = sorted({p[0] for p in plan})
        print(f"\n{'='*70}")
        print(f"  분봉 절단 복구: {target}")
        print(f"{'='*70}")
        print(f"  대상 : {len(plan):,} code-day  ({len(codes)} 코드 × {len(days)} 일)")
        print(f"  기간 : {days[0]} ~ {days[-1]}")
        print(f"  현재 : {before_total:,}봉  (평균 {before_total/len(plan):.0f}봉/일)")
        # 페이징이 고쳐졌으므로 code-day 당 2콜(1.05s TPS) 예상
        print(f"  예상 : 호출 ~{len(plan)*2:,}건 / {len(plan)*2*1.05/60:.0f}분")
        if dry_run:
            print("\n  [dry-run] 호출하지 않고 종료")
            for c, d, b in plan[:10]:
                print(f"     {d} {c}  현재 {b}봉")
            if len(plan) > 10:
                print(f"     … 외 {len(plan)-10:,}건")
            return {"planned": len(plan), "dry_run": True}

        client = LsApiClient()
        rows_written = 0
        improved = same = empty = 0
        errors: list[tuple] = []
        t0 = time.time()

        for i, (code, day, before) in enumerate(plan, 1):
            try:
                bars, interval = _refetch(client, target, code, day)
                if not bars:
                    empty += 1
                    continue
                n = _insert(conn, target, code, bars, interval)
                rows_written += n
                if len(bars) > before:
                    improved += 1
                else:
                    same += 1
            except LsApiError as e:
                errors.append((code, str(day), e.category))
                print(f"    err {code} {day} {e.category}", flush=True)
            except Exception as e:
                errors.append((code, str(day), f"unexpected:{type(e).__name__}"))
                print(f"    EXC {code} {day} {type(e).__name__}: {e}", flush=True)

            if i % 25 == 0 or i == len(plan):
                el = time.time() - t0
                rate = i / el if el else 0
                eta = (len(plan) - i) / rate / 60 if rate else 0
                print(f"    [{i}/{len(plan)} {i/len(plan)*100:.1f}%] "
                      f"개선 {improved} / 변화없음 {same} / 빈 {empty} / 에러 {len(errors)} "
                      f"/ ETA {eta:.0f}min", flush=True)

        el = time.time() - t0
        print(f"\n  [완료] 소요 {el/60:.1f}분 / 개선 {improved:,} / 변화없음 {same:,} "
              f"/ 빈 {empty} / 에러 {len(errors)}")
        if errors:
            print(f"    에러 분류: {dict(Counter(e[2] for e in errors))}")
            print(f"    sample: {errors[:5]}")

        # 사후 검증 — DB 실제 상태로 재조회 (in-memory 카운트 신뢰 안 함)
        remain = find_truncated(conn, target, start, end)
        after_total = sum(p[2] for p in remain)
        print(f"\n  [검증] 잔여 절단 {len(remain):,} code-day "
              f"(복구 전 {len(plan):,})")
        if remain:
            print(f"         잔여분 현재 {after_total:,}봉 — 아래 10건 sample")
            for c, d, b in remain[:10]:
                print(f"           {d} {c}  {b}봉")
        return {"planned": len(plan), "improved": improved, "same": same,
                "empty": empty, "errors": len(errors), "remaining": len(remain),
                "rows_written": rows_written}
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description="분봉 절단 복구 (tr_cont 페이징 사고)")
    ap.add_argument("--target", choices=["futures", "stock"], default="futures")
    ap.add_argument("--from", dest="start", default="20260101", help="YYYYMMDD")
    ap.add_argument("--to", dest="end", default=None, help="YYYYMMDD (기본: 오늘)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    start = datetime.strptime(a.start, "%Y%m%d").date()
    end = datetime.strptime(a.end, "%Y%m%d").date() if a.end else date.today()
    r = run(a.target, start, end, a.dry_run)
    print(f"\nRESULT: {r}")


if __name__ == "__main__":
    main()
