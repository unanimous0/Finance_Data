"""
외국인 지분율 백필 스크립트 (일회성)
백필 완료 후 이 파일은 삭제해도 됩니다.

대상: ETF/SPAC 제외 전체 종목 (~2,640개)
범위: 2002-06-14 ~ 현재 (약 24년)
소요: 약 6시간 (API 1.05s/call × ~21,000 calls / workers=4)

API 제약: 응답 최대 1,000건 → 3년 단위 청크로 분할

사용법:
    python scripts/collect_foreign_ownership.py             # 전체 백필 (2002~현재)
    python scripts/collect_foreign_ownership.py --start 20230101 --end 20251231
"""

import sys
import traceback
from pathlib import Path
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings
from collectors.infomax import InfomaxClient

KST = ZoneInfo("Asia/Seoul")

BACKFILL_CHUNK_YEARS = 3   # 250 거래일/년 × 3년 = 750건 < API 한도 1,000건
MAX_WORKERS = 4

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


def get_conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


def get_target_stocks(conn) -> list[tuple[str, str]]:
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


def _fetch_foreign(client, code, name, start, end):
    rows = client.get_foreign(code, start, end)
    return code, name, rows


def collect_chunk(conn, client, stocks, start_date: date, end_date: date, chunk_label: str) -> dict:
    total  = len(stocks)
    result = {"rows": 0, "changed": 0, "skipped": 0, "fail": 0}
    batch  = []
    done   = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_foreign, client, code, name, start_date, end_date): code
            for code, name in stocks
        }
        for future in as_completed(futures):
            code, name, rows = future.result()
            done += 1

            if rows:
                for r in rows:
                    if r["date"] is None:
                        continue
                    batch.append((
                        r["date"], r["stock_code"],
                        r["frn_ownership_ratio"],
                        r["frn_ownership_vol"],
                        r["frn_limit_ratio"],
                    ))
            else:
                result["fail"] += 1

            if len(batch) >= 1000:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, FOREIGN_SQL, batch, page_size=500)
                    ch = cur.rowcount
                conn.commit()
                result["changed"] += ch
                result["skipped"] += len(batch) - ch
                result["rows"]    += len(batch)
                batch.clear()

            if done % 500 == 0 or done == total:
                print(f"    [{chunk_label}] [{done:4}/{total}] 누적저장:{result['rows']:,}건 실패:{result['fail']}개")

    if batch:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, FOREIGN_SQL, batch, page_size=500)
            ch = cur.rowcount
        conn.commit()
        result["changed"] += ch
        result["skipped"] += len(batch) - ch
        result["rows"]    += len(batch)

    return result


def get_ohlcv_date_range(conn) -> tuple[date, date]:
    with conn.cursor() as cur:
        cur.execute("SELECT MIN(time), MAX(time) FROM ohlcv_daily")
        return cur.fetchone()


def run_backfill(start_date: date = None, end_date: date = None):
    conn_tmp = get_conn()
    ohlcv_start, ohlcv_end = get_ohlcv_date_range(conn_tmp)
    conn_tmp.close()

    if start_date is None:
        start_date = ohlcv_start
    if end_date is None:
        end_date = ohlcv_end

    # 3년 단위 청크 생성
    chunks = []
    cs = start_date
    while cs <= end_date:
        ce = date(min(cs.year + BACKFILL_CHUNK_YEARS, end_date.year + 1), 1, 1) - timedelta(days=1)
        ce = min(ce, end_date)
        chunks.append((cs, ce))
        cs = ce + timedelta(days=1)

    conn   = get_conn()
    client = InfomaxClient()
    stocks = get_target_stocks(conn)

    print(f"\n{'='*70}")
    print(f"  외국인 지분율 백필")
    print(f"  기간: {start_date} ~ {end_date}  |  종목: {len(stocks):,}개  |  청크: {len(chunks)}개")
    print(f"{'='*70}\n")

    all_rows    = 0
    all_changed = 0
    all_fail    = 0
    started_at  = datetime.now(KST)

    for i, (cs, ce) in enumerate(chunks, 1):
        label = f"{i}/{len(chunks)} ({cs.year}~{ce.year})"
        print(f"\n[청크 {label}] {cs} ~ {ce}")
        r = collect_chunk(conn, client, stocks, cs, ce, label)
        all_rows    += r["rows"]
        all_changed += r["changed"]
        all_fail    += r["fail"]
        elapsed = (datetime.now(KST) - started_at).total_seconds()
        print(f"  → 청크 완료: {r['rows']:,}건 저장 / 누적 {all_rows:,}건 / 경과 {int(elapsed//60)}분")

    elapsed = (datetime.now(KST) - started_at).total_seconds()
    conn.close()

    print(f"\n{'='*70}")
    print(f"  백필 완료!")
    print(f"  총 저장: {all_rows:,}건 (변경: {all_changed:,} / 스킵: {all_rows-all_changed:,})")
    print(f"  총 실패: {all_fail}개 종목-청크")
    print(f"  소요 시간: {int(elapsed//3600)}시간 {int(elapsed%3600//60)}분 {int(elapsed%60)}초")
    print(f"{'='*70}\n")
    print("✅ 백필 완료. 이 스크립트는 삭제해도 됩니다.")


def main():
    args = sys.argv[1:]
    start = end = None
    if "--start" in args:
        start = datetime.strptime(args[args.index("--start") + 1], "%Y%m%d").date()
    if "--end" in args:
        end = datetime.strptime(args[args.index("--end") + 1], "%Y%m%d").date()
    run_backfill(start, end)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(f"\n❌ 오류:\n{traceback.format_exc()}", file=sys.stderr)
        sys.exit(1)
