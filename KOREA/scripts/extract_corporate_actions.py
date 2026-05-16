"""
ohlcv_daily의 adj_factor 변화 일자 → corporate_actions 자동 추출.

원리:
- adj_factor = 그날 시점에서 본 누적 보정 factor
- 권리락일 d 기준: d 이전 row는 factor = X (보정 적용), d 이후는 factor = X / event_price_factor
- 같은 종목에서 인접 일자 factor 차이 발견 → event_date = factor 변경 시작 일자
- event price_factor = 옛 factor / 새 factor (이벤트 발생 시 곱셈 비율)

예: LS일렉트릭 010120
- 4/10 adj_factor = 0.2 (4/13 분할 적용)
- 4/13 adj_factor = 1.0 (분할 후, 추가 이벤트 없음)
- event_date = 4/13, price_factor = 0.2 / 1.0 = 0.2 → 1:5 분할

사용: python scripts/extract_corporate_actions.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg2

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings


DETECT_SQL = """
WITH ranked AS (
    SELECT stock_code, time, adj_factor,
           LAG(adj_factor) OVER (PARTITION BY stock_code ORDER BY time) AS prev_factor
    FROM ohlcv_daily
    WHERE adj_factor IS NOT NULL
),
events AS (
    SELECT stock_code,
           time AS event_date,
           prev_factor AS old_factor,
           adj_factor  AS new_factor,
           -- price_factor = 옛 factor / 새 factor (이벤트로 인한 가격 곱셈 비율)
           ROUND((prev_factor / NULLIF(adj_factor, 0))::numeric, 10) AS price_factor
    FROM ranked
    WHERE prev_factor IS NOT NULL
      AND ABS(prev_factor - adj_factor) > 0.001
)
SELECT stock_code, event_date, old_factor, new_factor, price_factor
FROM events
ORDER BY event_date DESC, stock_code
"""

UPSERT_SQL = """
INSERT INTO corporate_actions
    (stock_code, event_date, event_type, ratio_before, ratio_after, price_factor,
     share_factor, source, description, applied, created_at)
VALUES
    (%s, %s, 'UNKNOWN_FROM_FACTOR', NULL, NULL, %s,
     NULL, 'LS', %s, FALSE, NOW())
ON CONFLICT (stock_code, event_date, event_type) DO UPDATE SET
    price_factor = EXCLUDED.price_factor,
    description = EXCLUDED.description
"""


def main():
    parser = argparse.ArgumentParser(description="adj_factor 변화 → corporate_actions 자동 추출")
    parser.add_argument("--dry-run", action="store_true", help="INSERT 없이 출력만")
    args = parser.parse_args()

    conn = psycopg2.connect(
        host=settings.DB_HOST, dbname=settings.DB_NAME,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(DETECT_SQL)
            events = cur.fetchall()

        print(f"[추출] {len(events)}건 corporate action 발견 (factor 변화 일자)\n")

        # 종목명 lookup
        name_map = {}
        if events:
            codes = list({e[0] for e in events})
            with conn.cursor() as cur:
                cur.execute("SELECT stock_code, stock_name FROM stocks WHERE stock_code = ANY(%s)", (codes,))
                name_map = dict(cur.fetchall())

        # event_type 추정 (price_factor 기반)
        def classify(pf):
            if pf is None: return "UNKNOWN"
            pf = float(pf)
            if pf < 1: return f"SPLIT (1:{1/pf:.2f})"
            elif pf > 1: return f"REVERSE_SPLIT ({pf:.2f}:1)"
            return "NO_CHANGE"

        # 상위 30건 미리보기
        print(f"  {'event_date':12s} {'code':8s} {'name':15s} {'old':>10s} {'new':>10s} {'price_factor':>14s}  추정")
        print('  ' + '-' * 95)
        for e in events[:30]:
            code, ed, old, new, pf = e
            name = name_map.get(code, '?')
            kind = classify(pf)
            print(f"  {str(ed):12s} {code:8s} {name[:15]:15s} {old:>10.4f} {new:>10.4f} {pf:>14.6f}  {kind}")
        if len(events) > 30:
            print(f"  ... 총 {len(events)}건")

        if args.dry_run:
            print(f"\n[dry-run] INSERT skip")
            return

        # INSERT
        rows = [
            (e[0], e[1], float(e[4]),
             f"factor change {float(e[2]):.4f}→{float(e[3]):.4f} ({classify(e[4])})")
            for e in events if e[4] is not None
        ]
        if rows:
            with conn:
                with conn.cursor() as cur:
                    cur.executemany(UPSERT_SQL, rows)
            print(f"\n[적재] corporate_actions UPSERT {len(rows)}건")
        else:
            print(f"\n[적재] INSERT 0건")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
