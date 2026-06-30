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

from collectors.infomax import InfomaxClient, pick_nearest_deferred, InfomaxDailyLimitError
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


def backfill_futures_ohlcv(conn, client: InfomaxClient, start: date, end: date,
                           underlying_types: tuple = ('F', 'L'),
                           skip_codes: set = None, on_complete=None) -> dict:
    """선물 NEAR/NEXT 시계열 백필. underlying_types로 F(지수선물)/L(주식선물) 선택.

    재개 지원:
      skip_codes : 이미 완료된 underlying_code set → 건너뜀.
      on_complete: 한 underlying의 전 청크 성공 시 호출되는 콜백(uc). 진행상태 저장용.
    InfomaxDailyLimitError 발생 시 현재 underlying은 미완료로 두고 즉시 깨끗이 중단
    (남은 청크 헛호출 방지). 반환 dict로 재개 여부 판단.

    반환: {"total": int, "completed": [uc...], "stopped_by_limit": bool}
    """
    skip_codes = skip_codes or set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT underlying_code FROM futures_underlyings WHERE underlying_type = ANY(%s) ORDER BY underlying_code",
            (list(underlying_types),))
        underlyings = [r[0] for r in cur.fetchall()]
    todo = [u for u in underlyings if u not in skip_codes]
    print(f"  futures underlying {len(underlyings)}개({','.join(underlying_types)}) — "
          f"완료 {len(underlyings) - len(todo)} / 잔여 {len(todo)}", flush=True)

    total = 0
    completed: list = []
    stopped_by_limit = False
    for i, uc in enumerate(todo, 1):
        try:
            for klass in ['NEAR', 'NEXT']:
                rows = []
                # NEXT(2active)는 날짜마다 원월물 여러 개(~6~10행/일) 반환 + API 1000행 한도.
                # 700일 청크면 truncate되어 과거가 잘림 → NEXT는 90일 청크(≤~990행)로 분할.
                chunk_days = 90 if klass == 'NEXT' else 700
                for s, e in chunked_dates(start, end, chunk_days):
                    try:
                        data = client.get_future_active(uc, s, e, contract_class=klass)
                    except InfomaxDailyLimitError:
                        raise  # 상위 except로 → 전체 중단
                    except Exception as exc:
                        print(f"    {uc} {klass} [{s}~{e}] EXC: {exc}", flush=True)
                        continue
                    if klass == 'NEXT':
                        data = pick_nearest_deferred(data)  # 진짜 차근월(만기 최소)만
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
        except InfomaxDailyLimitError:
            stopped_by_limit = True
            print(f"    ⛔ 일일 한도 도달 — {uc} 미완료로 중단 "
                  f"(이번 실행 완료 {len(completed)} / 누적 {total:,} row)", flush=True)
            break

        completed.append(uc)
        if on_complete:
            on_complete(uc)
        if i % 20 == 0:
            print(f"    [{i}/{len(todo)}] {uc} 누적 {total:,} row", flush=True)

    status = "한도중단" if stopped_by_limit else "전체완료"
    print(f"  [선물 {status}] 이번 실행 완료 {len(completed)} underlying / {total:,} row")
    return {"total": total, "completed": completed, "stopped_by_limit": stopped_by_limit}


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
