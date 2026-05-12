"""
한국 ETF의 PDF 일괄 시드 (etf_portfolios 첫 적재).

스코프:
- stocks 테이블 market='ETF' AND active 중 해외 키워드 제외 = 한국 ETF ~631개
- 각 ETF에 대해 인포맥스 /api/etf/port 호출 → component 종목 INSERT
- 의사코드(010010 원화현금) 등 stocks에 없는 코드는 FK 매칭으로 자연 제외

운영:
- 1회성 시드 — 이후엔 daily_update의 update_etf_portfolios()가 SCD2로 갱신
- ~11분 (631 호출 × 1.05초)

사용:
    python scripts/seed_etf_portfolios.py --date 20260508
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import psycopg2
import psycopg2.extras

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from collectors.infomax import InfomaxClient
from config.settings import settings


KOREA_ETF_SQL = """
SELECT stock_code, stock_name FROM stocks
 WHERE market = 'ETF' AND is_active = TRUE
   AND NOT (
       stock_name ~ '(미국|나스닥|NASDAQ|S&P|필라델피아|차이나|항셍|일본|베트남|인도|유럽|뉴욕|INDXX|SOLACTIVE|WTI|원유|은선물|천연가스|옥수수|대두|엔비디아|테슬라|구글|팔란티어|마이크로소프트|아마존|애플|메타)'
       OR (stock_name LIKE '%(H)%' AND stock_name NOT LIKE '%KRX%')
       OR (stock_name LIKE '%글로벌%' AND stock_name NOT LIKE '%K-글로벌%' AND stock_name NOT LIKE '%K글로벌%')
   )
ORDER BY stock_code
"""


def _conn():
    return psycopg2.connect(host=settings.DB_HOST, dbname=settings.DB_NAME,
                             user=settings.DB_USER, password=settings.DB_PASSWORD)


def fetch_korea_etfs(conn) -> list[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(KOREA_ETF_SQL)
        return cur.fetchall()


def fetch_known_stocks(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT stock_code FROM stocks")
        return {r[0] for r in cur.fetchall()}


INSERT_SQL = """
INSERT INTO etf_portfolios (etf_code, component_code, weight, shares, effective_date, end_date)
VALUES %s
ON CONFLICT (etf_code, component_code, effective_date) DO UPDATE
   SET weight = EXCLUDED.weight, shares = EXCLUDED.shares
"""


def seed(target_date: date):
    client = InfomaxClient()
    conn = _conn()
    try:
        etfs   = fetch_korea_etfs(conn)
        known  = fetch_known_stocks(conn)
        print(f"[ETF SCD2 시드] 한국 ETF {len(etfs)}개 / target_date={target_date}")

        total_rows = 0
        empty = 0
        errors: list[tuple[str, str, str]] = []
        from time import time as now

        t0 = now()
        for i, (etf_code, etf_name) in enumerate(etfs, 1):
            try:
                rows = client.get_etf_portfolio(etf_code, target_date)
            except Exception as e:
                errors.append((etf_code, etf_name, str(e)))
                continue
            if not rows:
                empty += 1
                continue

            etf_value = rows[0].get("etf_value") or 0
            seen: dict[str, tuple] = {}  # component_code → (weight, shares)
            for r in rows:
                pc = r.get("port_code")
                if not pc or pc not in known:
                    continue  # 의사코드/선물/상장X 자연 제외
                pv = r.get("port_value") or 0
                weight_pct = (pv / etf_value * 100) if etf_value else None
                shares = r.get("port_volume")
                if pc in seen:
                    # 같은 컴포넌트 중복 행 — weight 합산 (분리 보유분 합치기)
                    prev_w, prev_s = seen[pc]
                    seen[pc] = (
                        (prev_w or 0) + (weight_pct or 0) if (prev_w is not None or weight_pct is not None) else None,
                        (prev_s or 0) + (shares or 0) if (prev_s is not None or shares is not None) else None,
                    )
                else:
                    seen[pc] = (weight_pct, shares)
            valid = [(etf_code, pc, w, s, target_date, None) for pc, (w, s) in seen.items()]

            if valid:
                with conn:
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_values(cur, INSERT_SQL, valid, page_size=200)
                total_rows += len(valid)

            if i % 100 == 0 or i == len(etfs):
                elapsed = now() - t0
                rate = i / elapsed
                eta = (len(etfs) - i) / rate if rate else 0
                print(f"  [{i}/{len(etfs)}] {etf_code} {etf_name[:20]:<20} "
                      f"valid={len(valid):>3} 누적={total_rows:>6,} "
                      f"빈응답={empty} 에러={len(errors)} ETA={eta:.0f}s")

        print(f"\n[완료] 적재 row={total_rows:,} / 빈응답 ETF={empty} / 에러={len(errors)}")
        if errors:
            print(f"  에러 샘플 (최대 10): {errors[:10]}")

        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS rows,
                       COUNT(DISTINCT etf_code) AS etfs,
                       COUNT(DISTINCT component_code) AS comps_uniq
                  FROM etf_portfolios WHERE end_date IS NULL
            """)
            n, e, c = cur.fetchone()
        print(f"\n[etf_portfolios active] rows={n:,} / ETF={e} / 고유 component={c}")
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="YYYYMMDD")
    args = p.parse_args()
    target = datetime.strptime(args.date, "%Y%m%d").date()
    seed(target)


if __name__ == "__main__":
    main()
