"""adj_close 미적재 일괄 복구 러너 (일회성 운영 스크립트).

`backfill_missing_adj`를 넓은 구간에 한 번 돌리기 위한 얇은 래퍼.
일자별로 커밋하므로 중단해도 안전하고, 재실행하면 남은 NULL만 다시 처리한다(멱등).

사용: python scripts/_adj_repair_run.py 20240423 20260729
"""

import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.daily_update import get_conn, backfill_missing_adj


def _ymd(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def main() -> None:
    start, end = _ymd(sys.argv[1]), _ymd(sys.argv[2])
    conn = get_conn()
    try:
        t0 = time.time()
        print(f"[adj 복구] {start} ~ {end}", flush=True)
        r = backfill_missing_adj(conn, start, end)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM ohlcv_daily "
                "WHERE time BETWEEN %s AND %s AND adj_close IS NULL", (start, end))
            left = cur.fetchone()[0]
        print(f"[완료] {r['days']}일자 / {r['rows']:,}행 복구 / 잔여 {left:,}행 "
              f"/ {time.time() - t0:.0f}s", flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
