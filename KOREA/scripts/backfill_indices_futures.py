"""
지수 + 섹터지수 + 선물 (지수/주식 근월/원월) 일별 OHLCV 백필.

수집 소스:
  - 지수 마스터:        /api/index/code  (type=K/Q/X/T/N union)
  - 지수 OHLCV:         /api/index/hist  (1000행 한도 → chunks)
  - 선물 underlying:    /api/future/code → distinct underlying_code
  - 선물 active/2active: /api/future/active|2active  (1000행 한도 → chunks)

사용:
    python scripts/backfill_indices_futures.py 20220102 20260512
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

from collectors.infomax import InfomaxClient
from config.settings import settings


SECTOR_KEYWORDS = ['헬스케어','건설','금융','에너지','철강','소재','산업재','자유소비재',
                   '생활소비재','정보기술','반도체','커뮤니케이션','유틸리티','부동산',
                   '경기소비재','자동차','은행','보험','증권','조선','기계','화학',
                   '2차전지','바이오','BBIG','배당','성장']


def _conn():
    return psycopg2.connect(host=settings.DB_HOST, dbname=settings.DB_NAME,
                             user=settings.DB_USER, password=settings.DB_PASSWORD)


def _is_sector(name: str) -> bool:
    n = name or ""
    return any(kw in n for kw in SECTOR_KEYWORDS)


def chunked_dates(start: date, end: date, chunk_days: int = 700):
    """1000행 한도 회피 위해 거래일 기준 약 700일 chunks."""
    cur = start
    while cur <= end:
        c_end = min(cur + timedelta(days=chunk_days - 1), end)
        yield cur, c_end
        cur = c_end + timedelta(days=1)


def seed_indices_master(conn, client: InfomaxClient) -> int:
    """indices 마스터 적재 (K/Q/X/T/N 전체)."""
    rows = []
    for t in ['K', 'Q', 'X', 'T', 'N']:
        for x in client.get_index_codes(t):
            rows.append((
                x.get('code'),
                x.get('kr_name'),
                x.get('en_name'),
                t,
                x.get('return_type'),
                _is_sector(x.get('kr_name', '')),
            ))
        time.sleep(1.1)
    with conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO indices (code, kr_name, en_name, index_type, return_type, is_sector)
                VALUES %s
                ON CONFLICT (code) DO UPDATE SET
                    kr_name = EXCLUDED.kr_name,
                    en_name = EXCLUDED.en_name,
                    index_type = EXCLUDED.index_type,
                    return_type = EXCLUDED.return_type,
                    is_sector = EXCLUDED.is_sector
            """, rows, page_size=200)
    return len(rows)


def seed_futures_underlyings(conn, client: InfomaxClient) -> int:
    """futures_underlyings 마스터 적재 (F + L 우선)."""
    rows: dict[str, tuple] = {}
    for ut in ['F', 'L', 'C']:
        for x in client.get_future_codes(ut):
            uc = x.get('underlying_code')
            if not uc or uc in rows:
                continue
            kr_name = (x.get('kr_name') or '').split('F')[0].strip()
            rows[uc] = (uc, kr_name, ut, None)
        time.sleep(1.1)
    with conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO futures_underlyings (underlying_code, kr_name, underlying_type, stock_code)
                VALUES %s
                ON CONFLICT (underlying_code) DO UPDATE SET
                    kr_name = EXCLUDED.kr_name,
                    underlying_type = EXCLUDED.underlying_type
            """, list(rows.values()), page_size=200)
    return len(rows)


def _parse_ymd(v) -> date:
    s = str(v)
    return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))


def backfill_indices_ohlcv(conn, client: InfomaxClient, start: date, end: date) -> int:
    """모든 지수 일별 OHLCV 백필."""
    with conn.cursor() as cur:
        cur.execute("SELECT code FROM indices ORDER BY code")
        codes = [r[0] for r in cur.fetchall()]
    print(f"  지수 {len(codes)}개")

    total = 0
    for i, code in enumerate(codes, 1):
        rows = []
        for s, e in chunked_dates(start, end, 700):
            try:
                data = client.get_index_hist(code, s, e)
            except Exception as exc:
                print(f"    {code} [{s}~{e}] EXC: {exc}", flush=True)
                continue
            for r in data:
                rows.append((
                    code,
                    _parse_ymd(r.get('date')),
                    r.get('open_price'), r.get('high_price'), r.get('low_price'), r.get('close_price'),
                    r.get('change_rate'),
                    r.get('trading_volume'),
                    r.get('trading_value'),
                    r.get('marketcap'),
                    r.get('constituents'),
                ))
        if rows:
            with conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, """
                        INSERT INTO index_ohlcv_daily
                          (code, time, open, high, low, close, change_pct,
                           volume, trading_value, marketcap, constituents)
                        VALUES %s
                        ON CONFLICT (code, time) DO UPDATE SET
                          open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                          close=EXCLUDED.close, change_pct=EXCLUDED.change_pct,
                          volume=EXCLUDED.volume, trading_value=EXCLUDED.trading_value,
                          marketcap=EXCLUDED.marketcap, constituents=EXCLUDED.constituents
                    """, rows, page_size=500)
            total += len(rows)
        if i % 20 == 0:
            print(f"    [{i}/{len(codes)}] {code} 누적 {total:,} row", flush=True)
    print(f"  [지수 완료] {total:,} row")
    return total


def backfill_futures_ohlcv(conn, client: InfomaxClient, start: date, end: date) -> int:
    """선물 NEAR/NEXT 시계열 백필 (F + L)."""
    with conn.cursor() as cur:
        cur.execute("SELECT underlying_code FROM futures_underlyings WHERE underlying_type IN ('F','L') ORDER BY underlying_code")
        underlyings = [r[0] for r in cur.fetchall()]
    print(f"  futures underlying {len(underlyings)}개 × 2(NEAR/NEXT) = {len(underlyings)*2} 호출")

    total = 0
    for i, uc in enumerate(underlyings, 1):
        for klass in ['NEAR', 'NEXT']:
            rows = []
            for s, e in chunked_dates(start, end, 700):
                try:
                    data = client.get_future_active(uc, s, e, contract_class=klass)
                except Exception as exc:
                    print(f"    {uc} {klass} [{s}~{e}] EXC: {exc}", flush=True)
                    continue
                for r in data:
                    rows.append((
                        uc, klass, _parse_ymd(r.get('date')),
                        r.get('code'),
                        r.get('open_price'), r.get('high_price'), r.get('low_price'), r.get('close_price'),
                        r.get('settle_price'),
                        r.get('trading_volume'), r.get('trading_value'),
                        r.get('openInterest_volume'),
                        r.get('theoretical_price'),
                        r.get('underlying_basis'), r.get('theoretical_basis'),
                    ))
            if rows:
                # 같은 (underlying, class, date) 중복 행 제거 (롤오버 시점 케이스) — 마지막 등장 유지
                dedup: dict[tuple, tuple] = {}
                for r in rows:
                    dedup[(r[0], r[1], r[2])] = r
                rows = list(dedup.values())
                with conn:
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_values(cur, """
                            INSERT INTO futures_ohlcv_daily
                              (underlying_code, contract_class, time, contract_code,
                               open, high, low, close, settle_price,
                               volume, trading_value, open_interest,
                               theoretical_price, underlying_basis, theoretical_basis)
                            VALUES %s
                            ON CONFLICT (underlying_code, contract_class, time) DO UPDATE SET
                              contract_code=EXCLUDED.contract_code,
                              open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                              close=EXCLUDED.close, settle_price=EXCLUDED.settle_price,
                              volume=EXCLUDED.volume, trading_value=EXCLUDED.trading_value,
                              open_interest=EXCLUDED.open_interest,
                              theoretical_price=EXCLUDED.theoretical_price,
                              underlying_basis=EXCLUDED.underlying_basis,
                              theoretical_basis=EXCLUDED.theoretical_basis
                        """, rows, page_size=500)
                total += len(rows)
        if i % 20 == 0:
            print(f"    [{i}/{len(underlyings)}] {uc} 누적 {total:,} row", flush=True)
    print(f"  [선물 완료] {total:,} row")
    return total


def main():
    p = argparse.ArgumentParser()
    p.add_argument("start", help="YYYYMMDD")
    p.add_argument("end", help="YYYYMMDD")
    args = p.parse_args()
    start = datetime.strptime(args.start, "%Y%m%d").date()
    end   = datetime.strptime(args.end,   "%Y%m%d").date()

    print("=" * 70)
    print(f"지수+선물 일별 OHLCV 백필: {start} ~ {end}")
    print("=" * 70)

    client = InfomaxClient()
    conn = _conn()
    try:
        t0 = time.time()
        print("\n[1/4] indices 마스터 적재...")
        n = seed_indices_master(conn, client)
        print(f"  → {n}개")

        print("\n[2/4] futures_underlyings 마스터 적재...")
        n = seed_futures_underlyings(conn, client)
        print(f"  → {n}개")

        print("\n[3/4] 지수 일별 OHLCV 백필...")
        backfill_indices_ohlcv(conn, client, start, end)

        print("\n[4/4] 선물 일별 OHLCV 백필...")
        backfill_futures_ohlcv(conn, client, start, end)

        print(f"\n전체 소요: {(time.time()-t0)/60:.1f}분")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
