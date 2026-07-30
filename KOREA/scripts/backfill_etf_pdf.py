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
from datetime import date, datetime, timedelta, time as dtime
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


# 08:30 아침 종합 보충(in-process, pgrep 미탐)이 도는 시간창 — 이 구간엔 백필 정지.
# 잡 = ETF 스냅샷 + 외인/OHLCV 누락보충으로 08:30~약10:00 인포맥스 점유(실측 어제 10:04 완료).
# --no-wait 로 안전윈도우(10:00)를 우회해도 이 창만은 항상 회피해 스냅샷/보충 완결성 보호.
SNAPSHOT_WINDOW = (dtime(8, 25), dtime(10, 5))


def wait_out_snapshot_window():
    """현재 시각이 08:25~10:05 KST 사이면 10:05 지날 때까지 60s 폴링 대기.
    08:30 아침 종합 보충(in-process)은 pgrep 가드로 못 잡으므로 시간창으로 회피."""
    while True:
        now = datetime.now(KST)
        if not (SNAPSHOT_WINDOW[0] <= now.time() < SNAPSHOT_WINDOW[1]):
            return
        print(f"  [SNAPSHOT-WINDOW] 08:30 ETF 스냅샷 시간창 ({now:%H:%M}) — 60s 후 재확인", flush=True)
        time.sleep(60)


def maybe_pause_for_daily_update():
    """인포맥스 한도를 쓰는 스케줄러 잡이 도는 동안 sleep (60 RPM 경합 회피).

    두 경로로 확인한다:
      1) **잡 마커 파일** (`schedulers.job_state`) — 02:00 daily_update / 08:30 종합 보충은
         스케줄러 안에서 in-process로 돌아 pgrep에 **절대 안 잡힌다**. 마커가 유일한 신호.
      2) pgrep — 사용자가 손으로 `python scripts/daily_update.py` 등을 돌린 경우 대비.
    마커는 PID를 포함해, 스케줄러가 비정상 종료해 마커가 남아도 stale로 무시된다
    (백필이 영원히 멈추지 않음)."""
    import subprocess
    from schedulers.job_state import active_job
    targets = ["scripts/daily_update.py", "scripts/etf_snapshot.py"]
    while True:
        busy = None
        job = active_job()
        if job:
            busy = f"{job.get('job')} (pid={job.get('pid')}, since {job.get('started')})"
        else:
            try:
                for t in targets:
                    r = subprocess.run(
                        ["pgrep", "-f", t],
                        capture_output=True, text=True, timeout=5,
                    )
                    if r.returncode == 0:
                        busy = t
                        break
            except Exception:
                return  # pgrep 실패 시 진행 (안전 측)
        if busy is None:
            return  # 모두 미실행 → 즉시 진행
        now = datetime.now(KST)
        print(f"  [PAUSE] {busy} 진행 중 ({now:%H:%M}) — 60s 후 재확인", flush=True)
        time.sleep(60)


# _minute_scope의 ETF filter와 동일 (해외 제외, KRX/H 예외)
DOMESTIC_ETF_SQL = """
SELECT stock_code, stock_name FROM stocks
WHERE market = 'ETF' AND is_active = TRUE
  AND NOT (
      stock_name ~ '(미국|나스닥|NASDAQ|S&P|필라델피아|차이나|항셍|일본|베트남|인도|유럽|뉴욕|INDXX|SOLACTIVE|WTI|원유|은선물|천연가스|옥수수|대두|엔비디아|테슬라|구글|팔란티어|마이크로소프트|아마존|애플|메타(?!버스))'
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


def _ensure_conn(conn):
    """살아있는 커넥션을 보장해 반환 (죽었거나 None이면 새로 연결).

    이 백필은 한도 대기(최대 ~21h) / 안전윈도우 대기(최대 ~10h) 동안 커넥션을
    그대로 붙들고 sleep 한다. 그 사이 서버 재시작·backend 종료로 커넥션이 끊기면
    깨어난 직후 첫 write에서 죽는다 (2026-07-24 10:00 크래시, 70h 방치).
    매 항목 직전 ping — 1 TPS라 왕복 1회 비용은 무시 가능."""
    try:
        if conn is not None and not conn.closed:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            conn.rollback()          # ping 트랜잭션 종료 (idle in transaction 방지)
            return conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        pass
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass
    print("  [DB 재연결] 커넥션 끊김 감지 — 재연결", flush=True)
    return _conn()


def fetch_etfs(conn) -> list[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(DOMESTIC_ETF_SQL)
        return cur.fetchall()


def fetch_listing_dates(conn, codes: list[str]) -> dict[str, date]:
    """ETF별 상장일. 상장 전 날짜는 애초에 호출 대상에서 제외하기 위한 것.

    상장 전 조회 시 PDF는 빈 응답이지만 **마스터 API는 날짜를 무시하고 현재 값을
    반환**한다 → 상장일보다 앞선 snapshot_date로 가짜 마스터 행이 쌓인다
    (발견 시점 1,719행/71종목). snapshot_date=실측 원칙 위반이라 원천 차단.
    덤으로 100% 빈 응답이 확정인 콜(잔여의 8.2%)도 아낀다."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT stock_code, listing_date FROM stocks "
            "WHERE stock_code = ANY(%s) AND listing_date IS NOT NULL", (codes,))
        return dict(cur.fetchall())


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


def process_etf_day(client, conn, etf_code, target_date,
                    fetch_master: bool = True) -> tuple[int, int, str]:
    """1 ETF × 1 day → PDF (+ 옵션 master) 적재. 반환: (pdf_rows, master_rows, status).

    fetch_master=False 면 master API 호출 자체를 skip (월1회 샘플링용 — 인포맥스 콜 절감).
    마스터(creation_unit/listed_shares 등)는 거의 안 변해 월1회로 충분(사용자 결정, C)."""
    try:
        rows = client.get_etf_portfolio(etf_code, target_date)
        m = client.get_etf_master(etf_code, target_date) if fetch_master else None
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
    parser.add_argument("--codes", default=None,
                        help="특정 ETF 코드만 (쉼표구분, 예: 396500,364980,462010). 워치리스트 우선 백필용")
    parser.add_argument("--desc", action="store_true",
                        help="최신 일자부터 옛 일자 순으로 (점진 분석용)")
    parser.add_argument("--max-calls", type=int, default=None,
                        help="일별 최대 API 호출 수 self-limit (옵션). 미지정 시 인포맥스가 한도 초과 응답할 때까지 호출 — 반복 잡 남은 한도 풀로 활용.")
    parser.add_argument("--master-every-day", action="store_true",
                        help="마스터를 매 거래일 수집 (기본: 월 첫 거래일만 = 월1회 샘플, 콜 절감)")
    parser.add_argument("--no-wait", action="store_true",
                        help="안전윈도우(00:00~10:00 sleep) 무시하고 즉시 가동 — 소량 워치리스트 우선 백필용. "
                             "daily_update 진행중 PAUSE 가드는 유지.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y%m%d").date()
    end   = datetime.strptime(args.end, "%Y%m%d").date() if args.end else (date.today() - timedelta(days=1))

    client = InfomaxClient()
    conn = _conn()
    try:
        if args.codes:
            # 명시 코드는 DOMESTIC_ETF_SQL 국내필터 우회 — stocks에서 활성 ETF 직접 선택
            # (해외필터 오탐 '메타버스'→'메타' 등, 사용자 명시 의도 우선)
            want = list(dict.fromkeys(c.strip() for c in args.codes.split(",") if c.strip()))
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT stock_code, stock_name FROM stocks "
                    "WHERE stock_code = ANY(%s) AND market='ETF' AND is_active=TRUE "
                    "ORDER BY stock_code", (want,))
                etfs = cur.fetchall()
            found = {c for c, _ in etfs}
            missing = [c for c in want if c not in found]
            print(f"  [--codes] 지정 {len(want)}개 → 활성 ETF {len(etfs)}개 직접 선택"
                  + (f" / 미매칭(비활성/비ETF): {missing}" if missing else ""))
        else:
            etfs = fetch_etfs(conn)
        if args.limit:
            etfs = etfs[:args.limit]
        biz_days = fetch_biz_days(conn, start, end)

        # 마스터 수집 날짜 = 각 (연,월)의 첫 거래일 (월1회 샘플). --master-every-day면 전체.
        master_dates: set[date] = set()
        if args.master_every_day:
            master_dates = set(biz_days)
        else:
            seen_month: set[tuple[int, int]] = set()
            for d in sorted(biz_days):
                ym = (d.year, d.month)
                if ym not in seen_month:
                    seen_month.add(ym)
                    master_dates.add(d)
        print(f"  [마스터] {'매 거래일' if args.master_every_day else '월1회(월 첫 거래일)'} "
              f"— 마스터 수집일 {len(master_dates)}일")

        if args.desc:
            biz_days = list(reversed(biz_days))

        # 이미 적재된 (etf, date) 쌍 제외 (daily etf_snapshot 커버 구간 중복 호출 방지)
        existing = fetch_existing_pairs(conn, start, end)
        listing = fetch_listing_dates(conn, [c for c, _ in etfs])
        full_total = len(etfs) * len(biz_days)
        not_existing = [(d, etf_code, etf_name)
                        for d in biz_days
                        for etf_code, etf_name in etfs
                        if (etf_code, d) not in existing]
        work_list = [(d, etf_code, etf_name)
                     for d, etf_code, etf_name in not_existing
                     if not (etf_code in listing and d < listing[etf_code])]
        total = len(work_list)
        skipped_existing = full_total - len(not_existing)
        skipped_prelist = len(not_existing) - total

        order = "desc (최신→옛)" if args.desc else "asc (옛→최신)"
        print(f"[ETF PDF 백필 {order}] {start}~{end} 거래일 {len(biz_days)}일 × ETF {len(etfs)}개")
        print(f"  전체 {full_total:,} 중 이미 적재 {skipped_existing:,} 건 skip"
              f" + 상장 전 {skipped_prelist:,} 건 skip → 실제 호출 {total:,}")
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
            if not args.no_wait:
                wait_for_safe_window()       # 00:00~10:00 sleep — 반복 잡에 한도 양보
            wait_out_snapshot_window()       # 08:25~09:45 회피 — 08:30 스냅샷(in-process) 보호 (--no-wait도 적용)
            maybe_pause_for_daily_update()   # daily_update/etf_snapshot 진행 중이면 PAUSE (방어적 이중 가드)
            fm = d in master_dates
            # 재시도 루프 — 각 시도 직전에 커넥션을 보장한다.
            # (구버전은 한도 대기 후 재시도를 except 블록 *안*에서 했다. 같은 try의
            #  sibling except는 다른 except 안의 예외를 못 잡으므로, 20h sleep 뒤
            #  죽은 커넥션을 만나면 그대로 프로세스가 종료됐다 — 7/24 크래시 원인.)
            pdf_n = master_n = 0
            status = "err:RetryExhausted"
            for attempt in range(1, 4):
                conn = _ensure_conn(conn)
                try:
                    pdf_n, master_n, status = process_etf_day(client, conn, etf_code, d, fetch_master=fm)
                    break
                except InfomaxDailyLimitError:
                    wait_until_midnight()
                    daily_calls = 1
                except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                    print(f"  [DB 재연결] {type(e).__name__}: {e}", flush=True)
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None      # 다음 시도의 _ensure_conn이 새로 연결
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
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
