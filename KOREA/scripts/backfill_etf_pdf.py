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

from collectors.infomax import InfomaxClient, InfomaxDailyLimitError
from config.settings import settings

KST = ZoneInfo("Asia/Seoul")


# 인포맥스 일별 한도 = 00:00 KST 리셋. 백필 안전 가동 윈도우는 10:00 ~ 24:00.
# - 00:00 ~ 10:00 sleep: 일별 한도 리셋 후 daily_update(02:00) + etf_snapshot(08:30) 우선권 보장
# - 10:00 ~ 24:00 가동: etf_snapshot ~09:00 종료 + 1시간 안전 마진
SAFE_WINDOW_START_HOUR = 10


def wait_for_safe_window():
    """현재 시각이 00:00 ~ 10:00 KST 사이면 오늘 10:00까지 대기.
    반복 잡 우선권 + 일별 한도(00:00 리셋) 침범 방지."""
    now = datetime.now(KST)
    if now.hour >= SAFE_WINDOW_START_HOUR:
        return  # 이미 안전 윈도우 (10:00 ~ 23:59)
    resume = now.replace(hour=SAFE_WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    secs = (resume - now).total_seconds()
    print(f"  [SAFE-WINDOW] 안전 윈도우 밖 ({now:%H:%M}) — {resume:%H:%M} KST까지 {secs/3600:.1f}h 대기"
          f" (daily_update + etf_snapshot 우선권 보장)", flush=True)
    time.sleep(secs)
    print(f"  [SAFE-WINDOW] 대기 완료, 재개", flush=True)


def wait_until_midnight():
    """인포맥스 일별 한도 초과 → 다음 안전 윈도우 시작(다음날 10:00 KST)까지 대기.
    이름은 historical (자정 = 한도 리셋 시점). 실제론 자정 + 10h(반복 잡 우선권)."""
    now = datetime.now(KST)
    if now.hour < SAFE_WINDOW_START_HOUR:
        # 오늘 아직 10시 전이면 오늘 10시까지
        resume = now.replace(hour=SAFE_WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    else:
        # 10시 이후면 다음날 10시까지
        resume = (now + timedelta(days=1)).replace(hour=SAFE_WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    secs = (resume - now).total_seconds()
    print(f"  [LIMIT] 인포맥스 일별 한도 초과 — {resume:%m/%d %H:%M} KST까지 {secs/3600:.1f}h 대기"
          f" (한도 리셋 00:00 + 반복 잡 우선권)", flush=True)
    time.sleep(secs)
    print(f"  [LIMIT] 대기 완료, 재개", flush=True)


def maybe_pause_for_daily_update():
    """daily_update.py 또는 etf_snapshot.py 둘 중 하나라도 살아있으면 sleep.
    둘 다 인포맥스 60 RPM 한도 점유 → 동시 호출 시 429/timeout 발생."""
    import subprocess
    targets = ["scripts/daily_update.py", "scripts/etf_snapshot.py"]
    while True:
        busy = None
        try:
            for t in targets:
                r = subprocess.run(
                    ["pgrep", "-f", t],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    busy = t
                    break
            if busy is None:
                return  # 모두 미실행 → 즉시 진행
        except Exception:
            return  # pgrep 실패 시 진행 (안전 측)
        now = datetime.now(KST)
        print(f"  [PAUSE] {busy} 진행 중 ({now:%H:%M}) — 60s 후 재확인", flush=True)
        time.sleep(60)


# _minute_scope의 ETF filter와 동일 (해외 제외, KRX/H 예외)
DOMESTIC_ETF_SQL = """
SELECT stock_code, stock_name FROM stocks
WHERE market = 'ETF' AND is_active = TRUE
  AND NOT (
      stock_name ~ '(미국|나스닥|NASDAQ|S&P|필라델피아|차이나|항셍|일본|베트남|인도|유럽|뉴욕|INDXX|SOLACTIVE|WTI|원유|은선물|천연가스|옥수수|대두|엔비디아|테슬라|구글|팔란티어|마이크로소프트|아마존|애플|메타)'
      OR (stock_name LIKE '%(H)%' AND stock_name NOT LIKE '%KRX%')
      OR (stock_name LIKE '%글로벌%' AND stock_name NOT LIKE '%K-글로벌%' AND stock_name NOT LIKE '%K글로벌%')
      OR stock_name ~ '(채권|리츠|REIT|싱가포르|혼합|커버드콜|금현물|Gold|GOLD)'
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


def fetch_existing_pairs(conn, start: date, end: date) -> set[tuple[str, date]]:
    """etf_portfolio_daily에 이미 적재된 (etf_code, snapshot_date) 쌍.
    daily etf_snapshot이 매일 적재한 데이터를 백필이 중복 호출하지 않도록 제외용."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT etf_code, snapshot_date FROM etf_portfolio_daily "
            "WHERE snapshot_date BETWEEN %s AND %s",
            (start, end))
        return {(r[0], r[1]) for r in cur.fetchall()}


def process_etf_day(client, conn, etf_code, target_date) -> tuple[int, int, str]:
    """1 ETF × 1 day → PDF + master 적재. 반환: (pdf_rows, master_rows, status)"""
    try:
        rows = client.get_etf_portfolio(etf_code, target_date)
        m = client.get_etf_master(etf_code, target_date)
    except InfomaxDailyLimitError:
        raise  # 호출자(main loop)가 자정 대기 처리
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
                    m.get("creationunit"), m.get("listed_shares"), m.get("net_asset"),
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
    parser.add_argument("--desc", action="store_true",
                        help="최신 일자부터 옛 일자 순으로 (점진 분석용)")
    parser.add_argument("--max-calls", type=int, default=None,
                        help="일별 최대 API 호출 수 self-limit (옵션). 미지정 시 인포맥스가 한도 초과 응답할 때까지 호출 — 반복 잡 남은 한도 풀로 활용.")
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
        if args.desc:
            biz_days = list(reversed(biz_days))

        # 이미 적재된 (etf, date) 쌍 제외 (daily etf_snapshot 커버 구간 중복 호출 방지)
        existing = fetch_existing_pairs(conn, start, end)
        full_total = len(etfs) * len(biz_days)
        work_list = [(d, etf_code, etf_name)
                     for d in biz_days
                     for etf_code, etf_name in etfs
                     if (etf_code, d) not in existing]
        total = len(work_list)
        skipped_existing = full_total - total

        order = "desc (최신→옛)" if args.desc else "asc (옛→최신)"
        print(f"[ETF PDF 백필 {order}] {start}~{end} 거래일 {len(biz_days)}일 × ETF {len(etfs)}개")
        print(f"  전체 {full_total:,} 중 이미 적재 {skipped_existing:,} 건 skip → 실제 호출 {total:,}")
        print(f"  TPS 1 (60 RPM 기준) → 예상 ~{total/60:.0f}분 = ~{total/3600:.1f}시간")

        if args.dry_run:
            print("[dry-run] 종료")
            return

        t0 = time.time()
        ok = empty = err = 0
        total_pdf = total_master = 0
        i = 0
        daily_calls = 0
        for d, etf_code, etf_name in work_list:
            i += 1
            daily_calls += 1
            if args.max_calls is not None and daily_calls > args.max_calls:
                print(f"  [MAX-CALLS] 일별 self-limit {args.max_calls}콜 도달 — 09:30 재개 대기", flush=True)
                wait_until_midnight()
                daily_calls = 1
            wait_for_safe_window()           # 00:00~10:00 sleep — 반복 잡에 한도 양보
            maybe_pause_for_daily_update()   # daily_update/etf_snapshot 진행 중이면 PAUSE (방어적 이중 가드)
            try:
                pdf_n, master_n, status = process_etf_day(client, conn, etf_code, d)
            except InfomaxDailyLimitError:
                wait_until_midnight()
                daily_calls = 1
                pdf_n, master_n, status = process_etf_day(client, conn, etf_code, d)
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                print(f"  [DB 재연결] {type(e).__name__}: {e}", flush=True)
                try:
                    conn.close()
                except Exception:
                    pass
                conn = _conn()
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
