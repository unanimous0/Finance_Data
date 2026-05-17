"""
ETF PDF (구성종목) + 마스터 과거 backfill — NAV 시계열 분석용.

대상: 국내 ETF 636개 (해외 ETF 제외, _minute_scope의 ETF 필터와 동일)
기간: --from (default 2026-01-02) ~ --to (default 어제 영업일)
ON CONFLICT (PK = etf_code, snapshot_date, component_code) → 재실행 안전

사용:
    python scripts/backfill_etf_pdf.py
    python scripts/backfill_etf_pdf.py --from 20260101 --to 20260515
    python scripts/backfill_etf_pdf.py --limit 10 --dry-run
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from collectors.infomax import InfomaxClient
from config.settings import settings

KST = ZoneInfo("Asia/Seoul")


def maybe_pause_for_daily_update():
    """매일 04:30~09:00 KST: daily_update cron이 인포맥스 한도(60 RPM) 점유.
    그 시간엔 백필 sleep해서 한도 초과(429/timeout) 회피."""
    now = datetime.now(KST)
    if 4 <= now.hour < 9:
        next_resume = now.replace(hour=9, minute=0, second=0, microsecond=0)
        wait = (next_resume - now).total_seconds()
        print(f"  [PAUSE] daily_update 시간대 ({now:%H:%M}) — {wait/60:.0f}분 대기 (~09:00 KST 재개)", flush=True)
        time.sleep(wait)


# _minute_scope의 ETF filter와 동일 (해외 제외, KRX/H 예외)
DOMESTIC_ETF_SQL = """
SELECT stock_code, stock_name FROM stocks
WHERE market = 'ETF' AND is_active = TRUE
  AND NOT (
      stock_name ~ '(미국|나스닥|NASDAQ|S&P|필라델피아|차이나|항셍|일본|베트남|인도|유럽|뉴욕|INDXX|SOLACTIVE|WTI|원유|은선물|천연가스|옥수수|대두|엔비디아|테슬라|구글|팔란티어|마이크로소프트|아마존|애플|메타)'
      OR (stock_name LIKE '%(H)%' AND stock_name NOT LIKE '%KRX%')
      OR (stock_name LIKE '%글로벌%' AND stock_name NOT LIKE '%K-글로벌%' AND stock_name NOT LIKE '%K글로벌%')
  )
ORDER BY stock_code
"""

PDF_UPSERT_SQL = """
INSERT INTO etf_portfolio_daily
  (etf_code, snapshot_date, component_code, component_name, shares, is_cash)
VALUES %s
ON CONFLICT (etf_code, snapshot_date, component_code) DO UPDATE SET
  component_name = EXCLUDED.component_name,
  shares = EXCLUDED.shares,
  is_cash = EXCLUDED.is_cash
"""

MASTER_UPSERT_SQL = """
INSERT INTO etf_master_daily
  (etf_code, snapshot_date, kr_name, kr_company,
   creation_unit, listed_shares, net_asset,
   underlying_index, tracking_multiple, replication, total_fee)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (etf_code, snapshot_date) DO UPDATE SET
  kr_name=EXCLUDED.kr_name, kr_company=EXCLUDED.kr_company,
  creation_unit=EXCLUDED.creation_unit, listed_shares=EXCLUDED.listed_shares,
  net_asset=EXCLUDED.net_asset, underlying_index=EXCLUDED.underlying_index,
  tracking_multiple=EXCLUDED.tracking_multiple,
  replication=EXCLUDED.replication, total_fee=EXCLUDED.total_fee
"""


def _conn():
    return psycopg2.connect(
        host=settings.DB_HOST, dbname=settings.DB_NAME,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
    )


def fetch_etfs(conn) -> list[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(DOMESTIC_ETF_SQL)
        return cur.fetchall()


def fetch_biz_days(conn, start: date, end: date) -> list[date]:
    """ohlcv_daily 기준 거래일 (휴장일 자동 제외)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT time FROM ohlcv_daily WHERE time BETWEEN %s AND %s ORDER BY time",
            (start, end))
        return [r[0] for r in cur.fetchall()]


def process_etf_day(client, conn, etf_code, target_date) -> tuple[int, int, str]:
    """1 ETF × 1 day → PDF + master 적재. 반환: (pdf_rows, master_rows, status)"""
    try:
        rows = client.get_etf_portfolio(etf_code, target_date)
        m = client.get_etf_master(etf_code, target_date)
    except Exception as e:
        return 0, 0, f"err:{type(e).__name__}"

    pdf_rows = 0
    if rows:
        seen = set()
        pdf_values = []
        for r in rows:
            pc = r.get("port_code")
            if not pc or pc in seen:
                continue
            seen.add(pc)
            is_cash = (pc.startswith("KRD") or
                       "원화현금" in (r.get("port_name") or "") or
                       "현금" in (r.get("port_name") or ""))
            shares = r.get("port_value") if is_cash else r.get("port_volume")
            pdf_values.append((etf_code, target_date, pc,
                               r.get("port_name"), shares, is_cash))
        if pdf_values:
            with conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, PDF_UPSERT_SQL, pdf_values, page_size=200)
            pdf_rows = len(pdf_values)

    master_rows = 0
    if m:
        with conn:
            with conn.cursor() as cur:
                cur.execute(MASTER_UPSERT_SQL, (
                    etf_code, target_date, m.get("kr_name"), m.get("kr_company"),
                    m.get("creation_unit"), m.get("listed_shares"), m.get("net_asset"),
                    m.get("underlying_index"), m.get("tracking_multiple"),
                    m.get("replication"), m.get("total_fee"),
                ))
                master_rows = 1

    if pdf_rows == 0 and master_rows == 0:
        return 0, 0, "empty"
    return pdf_rows, master_rows, "ok"


def main():
    parser = argparse.ArgumentParser(description="ETF PDF 과거 backfill (국내 ETF)")
    parser.add_argument("--from", dest="start", default="20260102", help="YYYYMMDD")
    parser.add_argument("--to",   dest="end",   default=None,        help="YYYYMMDD (기본: 어제)")
    parser.add_argument("--limit", type=int, default=None, help="상위 N ETF (sanity)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y%m%d").date()
    end   = datetime.strptime(args.end, "%Y%m%d").date() if args.end else (date.today() - timedelta(days=1))

    client = InfomaxClient()
    conn = _conn()
    try:
        etfs = fetch_etfs(conn)
        if args.limit:
            etfs = etfs[:args.limit]
        biz_days = fetch_biz_days(conn, start, end)
        total = len(etfs) * len(biz_days)
        print(f"[ETF PDF 백필] {start}~{end} 거래일 {len(biz_days)}일 × ETF {len(etfs)}개 = {total:,} 호출")
        print(f"  TPS 1 (60 RPM 기준) → 예상 ~{total/60:.0f}분 = ~{total/3600:.1f}시간")

        if args.dry_run:
            print("[dry-run] 종료")
            return

        t0 = time.time()
        ok = empty = err = 0
        total_pdf = total_master = 0
        i = 0
        for etf_code, etf_name in etfs:
            for d in biz_days:
                i += 1
                maybe_pause_for_daily_update()  # 04:30~09:00 daily_update와 충돌 회피
                pdf_n, master_n, status = process_etf_day(client, conn, etf_code, d)
                if status == "ok":
                    ok += 1
                    total_pdf += pdf_n
                    total_master += master_n
                elif status == "empty":
                    empty += 1
                else:
                    err += 1
                if i % 500 == 0:
                    elapsed = time.time() - t0
                    rate = i / elapsed if elapsed else 0
                    eta_min = (total - i) / rate / 60 if rate else 0
                    print(f"  [{i:>6}/{total} {i/total*100:.1f}%] ok {ok} / 빈 {empty} / 에러 {err} / PDF {total_pdf:,} / 마스터 {total_master:,} / ETA {eta_min:.0f}min", flush=True)

        elapsed = time.time() - t0
        print(f"\n[완료] 소요 {elapsed/60:.1f}분 / ok {ok} / 빈 {empty} / 에러 {err} / PDF {total_pdf:,} / 마스터 {total_master:,}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
