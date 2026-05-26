"""
데이터 수집 + 백업 스케줄러

잡 목록:
    daily_update      — 매일 02:00 KST (월~일)           OHLCV/시가총액/수급/외국인지분율 + 배당 + LENS export
                          + 끝에 분봉 일배치 직렬 호출 (종목/ETF + 지수 + 지수선물 + futures_master export)
                          (LENS 야간 사용 시간 확보 위해 23:00 분봉 일배치 cron을 daily_update 끝으로 통합)
    stockfut_today    — 매일 23:30 KST (월~금)           주식선물 30초봉 (t8406 historical 불가, 당일만, ~10분)
    weekly_backup     — 매주 일요일 03:00 KST             DB 백업 + 7일 보관
    quarterly_sector  — 분기 첫 번째 일요일 03:30 KST     FICS 업종 크롤링 (1/4/7/10월)

새벽 5시 30분 선택 이유:
    - 인포맥스 외인지분율 API 익일 패턴 (새벽~오전 제공) 안전 마진
    - daily_update.py의 갭 backfill 로직이 평일/주말/공휴일 자동 처리 → 매일 돌려도 무해
    - 백업 잡(일 03:00) 및 섹터 잡(03:30)과 충돌 없음

실행법:
    python schedulers/daily_scheduler.py          # 포그라운드 실행 (Ctrl+C로 종료)
    tmux new-session -d -s scheduler "cd /home/una0/projects/Finance_Data/KOREA && source venv/bin/activate && python schedulers/daily_scheduler.py"
"""

import sys
import signal
import logging
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

KST = ZoneInfo("Asia/Seoul")

from schedulers.notifier import notify_job


def _read_report_tail(report_path: Path, max_chars: int = 1500) -> str:
    """daily_update 보고서 마지막 부분 읽기 (실패/이상 시 상세 첨부용)."""
    try:
        text = report_path.read_text(encoding="utf-8")
        if len(text) <= max_chars:
            return text
        return "...\n" + text[-max_chars:]
    except Exception as e:
        return f"(보고서 읽기 실패: {e})"


def _compact_daily_update_summary(report_path: Path) -> tuple[str, bool]:
    """daily_update 보고서에서 성공용 요약 한두 줄 추출.

    반환: (요약 텍스트, 이상 감지 여부)
    이상 감지 시 호출자는 전체 tail로 fallback 권장.
    """
    try:
        text = report_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"(보고서 읽기 실패: {e})", True

    # skip 보고서는 한 줄 (파일명에 _skip 명시된 경우만)
    if "_skip.txt" in str(report_path):
        first = text.strip().splitlines()[0] if text.strip() else ""
        return f"건너뜀: {first[:200]}", False

    # "수집 요약" 섹션의 합계 행 추출
    # 라벨 뒤에 (일봉) (OHLCV와 동일) 등 임의 텍스트 허용
    summary_parts = []
    anomaly = False
    import re
    for label in ("OHLCV", "시가총액", "투자자별 수급", "외국인 지분율"):
        pat = re.compile(rf"^\s*{re.escape(label)}[^\d\n]+([\d,]+)\s+([\d,]+)\s+([\d,]+)", re.MULTILINE)
        m = pat.search(text)
        if not m:
            continue
        ok, fail, total = m.group(1), m.group(2), m.group(3)
        fail_n = int(fail.replace(",", ""))
        if fail_n > 0:
            summary_parts.append(f"{label} {ok}/실패{fail}")
            anomaly = True
        else:
            summary_parts.append(f"{label} {ok}")

    # 특이사항 라인 카운트 (있으면 anomaly)
    if "🚨" in text or "이벤트 의심" in text or "수정계수 확인" in text:
        anomaly = True

    if not summary_parts:
        return "(요약 추출 실패 — 보고서 형식 변경 의심)", True

    return " / ".join(summary_parts), anomaly

# logs 폴더 생성 (logging 설정 전에 먼저 생성)
(project_root / "logs").mkdir(exist_ok=True)

# APScheduler 로그 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            project_root / "logs" / "scheduler.log",
            encoding="utf-8"
        ),
    ]
)
logger = logging.getLogger(__name__)


def job_daily_update():
    """매일 02:00 KST — daily_update 본체(인포맥스/DART) → 분봉 일배치(LS) 직렬.
    LENS 야간 사용 시간 확보 위해 분봉 일배치를 23:00 별도 cron에서 daily_update 끝으로 이전.
    daily_update ~3시간 → 분봉 일배치 ~50분 → 총 02:00~06:00 종료 (느린 날 최대 ~07:30).
    키B(와이프) 사용 — 08:50 이전 종료 보장 (LENS REST 09:00~15:45 키B 충돌 회피).
    """
    from scripts.daily_update import main as run_daily
    started = datetime.now(KST)
    logger.info("="*60)
    logger.info(f"[스케줄러] 일별 업데이트 시작: {started}")
    logger.info("="*60)

    # daily_update 본체
    main_status, main_err = "ok", None
    try:
        run_daily()
    except Exception as e:
        main_status, main_err = "fail", str(e)
        logger.error(f"[스케줄러] daily_update 본체 실패: {e}")

    # 본체 결과를 보고서 파일에서 추출해서 알림
    today_kst = datetime.now(KST).date()
    yesterday = today_kst - __import__("datetime").timedelta(days=1)
    reports_dir = project_root / "reports"
    detail = ""
    status = main_status
    found_report = None
    found_suffix = ""
    # 보고서 후보: 영업일 정식 / 휴장 skip
    for d in (yesterday, today_kst, yesterday - __import__("datetime").timedelta(days=1)):
        for suffix in ("", "_skip"):
            p = reports_dir / f"daily_update_{d:%Y%m%d}{suffix}.txt"
            if p.exists() and (datetime.now().timestamp() - p.stat().st_mtime) < 7200:
                found_report = p
                found_suffix = suffix
                break
        if found_report:
            break

    if found_report:
        if found_suffix == "_skip":
            # 휴장 / 영업일 없음 — 짧은 한 줄 + noop으로 표시
            summary, _ = _compact_daily_update_summary(found_report)
            detail = summary
            if main_status == "ok":
                status = "noop"
        elif main_status == "ok":
            # 성공 — 요약만. 이상 감지되면 tail로 fallback
            summary, anomaly = _compact_daily_update_summary(found_report)
            if anomaly:
                detail = summary + "\n\n--- 보고서 끝부분 ---\n" + _read_report_tail(found_report, 1000)
            else:
                detail = summary
        else:
            # 실패 — 전체 tail 첨부
            detail = _read_report_tail(found_report)

    if main_status == "fail" and main_err:
        detail = (detail + f"\n\n에러: {main_err}")[-1500:]
    notify_job("daily_update", status, started, detail=detail)

    # daily_update 끝나고 분봉 일배치 직렬 호출 (한낮 LS 부하 회피, 사용자 활동 시작 전)
    mb_started = datetime.now(KST)
    logger.info("="*60)
    logger.info(f"[스케줄러] 분봉 일배치 시작 (daily_update 후속): {mb_started}")
    logger.info("="*60)
    try:
        job_minute_bars_daily()
        # 분봉은 알림 별도로 보내지 않음 (daily_update 알림에 묶이는 흐름).
        # 실패 시에만 별도 알림.
    except Exception as e:
        logger.error(f"[스케줄러] 분봉 일배치 실패: {e}")
        notify_job("minute_bars (daily_update 후속)", "fail", mb_started, detail=f"에러: {e}")


def job_weekly_backup():
    """매주 일요일 03:00 실행되는 백업 작업. 알림은 실패 시에만."""
    from scripts.backup_db import run_backup, cleanup_old_backups
    started = datetime.now(KST)
    logger.info("="*60)
    logger.info(f"[스케줄러] 주간 백업 시작: {started}")
    logger.info("="*60)
    try:
        backup_file = run_backup()
        cleanup_old_backups()
        logger.info(f"[스케줄러] 백업 완료: {backup_file.name}")
        # 성공 시 알림 안 보냄 (사용자 정책)
    except Exception as e:
        logger.error(f"[스케줄러] 백업 실패: {e}")
        notify_job("weekly_backup", "fail", started, detail=f"에러: {e}")


def job_minute_bars_daily():
    """매일 23:00 KST — 분봉 일배치 (종목/ETF + 지수 + 지수선물).
    LS 백필 진행 중이면 SIGSTOP → 일배치 → SIGCONT (사용자 정책).
    주식선물(t8406)은 historical 불가 → 별도 cron(job_stockfut_today) 22:30 KST."""
    from datetime import timedelta as _td
    from scripts.daily_update import (
        run_minute_bars_pipeline,
        run_index_minute_bars_pipeline,
        run_futures_minute_bars_pipeline,
        export_futures_master_json,
        _ls_backfill_pause, _ls_backfill_resume,
        get_conn, last_business_day_on_or_before)
    yesterday = datetime.now(KST).date() - _td(days=1)
    conn = get_conn()
    try:
        target = last_business_day_on_or_before(conn, yesterday)
    finally:
        conn.close()
    logger.info("="*60)
    logger.info(f"[스케줄러] 분봉 일배치 시작: {datetime.now(KST)} target_date={target}")
    logger.info("="*60)

    # outer pause: 모든 파이프라인 동안 백필 STOP
    paused = _ls_backfill_pause()
    try:
        for fn, label in [
            (run_minute_bars_pipeline,         "종목/ETF"),
            (run_index_minute_bars_pipeline,   "지수"),
            (run_futures_minute_bars_pipeline, "지수선물"),
        ]:
            try:
                result = fn(target)
                logger.info(f"[스케줄러] {label} 분봉 일배치 완료: {result}")
            except Exception as e:
                logger.error(f"[스케줄러] {label} 분봉 일배치 실패: {e}")

        # LS-using export — 같은 LS 가드 안에서 호출 (5xx 충돌 회피)
        try:
            export_futures_master_json()
            logger.info("[스케줄러] futures_master.json export 완료")
        except Exception as fm_err:
            logger.error(f"[스케줄러] futures_master.json export 실패: {fm_err}")
    finally:
        _ls_backfill_resume(paused)


def _verify_stockfut_loaded(today, result) -> tuple[bool, str]:
    """주식선물 당일 적재 검증.
    1) actives 중 95% 이상의 contract가 DB에 30초봉 row를 가지는가
    2) 직전 영업일 데이터와 100% 동일하지 않은가 (LS t8406 휴장일 fallback 감지)
       — LS는 휴장일 query 시 직전 영업일 데이터를 반환하는 동작이 있음 (5/25 사례).
    """
    if not result or result.get("skipped"):
        return False, f"skip={result}"
    actives = result.get("actives", 0)
    if actives == 0:
        return False, "actives=0"
    try:
        from scripts.daily_update import get_conn
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                # 1) 적재된 distinct code 수 (KST date 기준)
                cur.execute(
                    "SELECT COUNT(DISTINCT futures_code) FROM futures_ohlcv_intraday "
                    "WHERE (time AT TIME ZONE 'Asia/Seoul')::date = %s AND interval_seconds = 30",
                    (today,))
                loaded = cur.fetchone()[0]

                # 2) 직전 영업일 찾기 (futures_ohlcv_intraday에 데이터 있는 날)
                cur.execute(
                    "SELECT MAX((time AT TIME ZONE 'Asia/Seoul')::date) "
                    "FROM futures_ohlcv_intraday "
                    "WHERE (time AT TIME ZONE 'Asia/Seoul')::date < %s AND interval_seconds = 30",
                    (today,))
                prev_biz = cur.fetchone()[0]

                # 3) 직전 영업일과 close/volume 비교
                duplicate_of_prev = False
                if prev_biz is not None:
                    cur.execute("""
                        SELECT COUNT(*) FROM (
                            SELECT t.futures_code,
                                   (t.time AT TIME ZONE 'Asia/Seoul')::time AS tt,
                                   t.close, t.volume
                            FROM futures_ohlcv_intraday t
                            WHERE (t.time AT TIME ZONE 'Asia/Seoul')::date = %s
                              AND t.interval_seconds = 30
                            EXCEPT
                            SELECT p.futures_code,
                                   (p.time AT TIME ZONE 'Asia/Seoul')::time AS tt,
                                   p.close, p.volume
                            FROM futures_ohlcv_intraday p
                            WHERE (p.time AT TIME ZONE 'Asia/Seoul')::date = %s
                              AND p.interval_seconds = 30
                        ) diff
                    """, (today, prev_biz))
                    diff_count = cur.fetchone()[0]
                    duplicate_of_prev = (diff_count == 0)
        finally:
            conn.close()

        threshold = max(1, int(actives * 0.95))
        if loaded < threshold:
            return False, f"actives={actives}, loaded={loaded}, threshold={threshold}"
        if duplicate_of_prev:
            return False, (f"actives={actives}, loaded={loaded}, 직전 영업일({prev_biz})과 "
                           f"100% 동일 — LS 휴장일 fallback 의심")
        return True, f"actives={actives}, loaded={loaded}, threshold={threshold}, prev_biz={prev_biz} OK"
    except Exception as e:
        return False, f"verify error: {e}"


def job_stockfut_today():
    """매일 23:30 KST (평일) — 주식선물 30초봉 당일 적재 (LS t8406).
    historical 불가능 → 매일 받지 않으면 영구 손실.
    휴장일이면 skip (LS t8406이 휴장일에 직전 영업일 데이터를 반환하는 동작 회피).
    실행 후 검증 → 누락 시 5분 간격 최대 3회 재시도 (UPSERT라 안전)."""
    from scripts.daily_update import (run_stockfut_minute_today_pipeline,
                                       _ls_backfill_pause, _ls_backfill_resume,
                                       is_market_closed, get_conn)
    import time as _time
    MAX_ATTEMPTS = 3
    RETRY_WAIT_SEC = 300  # 5분

    started = datetime.now(KST)
    today = started.date()
    logger.info("="*60)
    logger.info(f"[스케줄러] 주식선물 당일 30초봉 시작: {started}")
    logger.info("="*60)

    # 휴장일 체크 — krx_holidays 테이블 기반 (mon-fri cron이라 주말은 안 들어옴)
    conn = get_conn()
    try:
        closed = is_market_closed(conn, today)
    finally:
        conn.close()
    if closed:
        logger.info(f"[스케줄러] {today} 휴장일 — stockfut skip (LS 휴장일 fallback 회피)")
        notify_job("stockfut_today", "noop", started, detail=f"{today} 휴장일")
        return

    paused = _ls_backfill_pause()
    last_result = None
    last_err = None
    verify_msg = ""
    ok = False
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            logger.info(f"[스케줄러] stockfut attempt {attempt}/{MAX_ATTEMPTS}")
            try:
                last_result = run_stockfut_minute_today_pipeline(today)
                last_err = None
            except Exception as e:
                last_err = e
                last_result = None
                logger.error(f"[스케줄러] stockfut attempt {attempt} 예외: {e}")

            ok, verify_msg = _verify_stockfut_loaded(today, last_result)
            logger.info(f"[스케줄러] stockfut 검증: ok={ok}, {verify_msg}")
            if ok:
                break
            if attempt < MAX_ATTEMPTS:
                logger.warning(f"[스케줄러] stockfut 검증 실패 — {RETRY_WAIT_SEC}s 후 재시도")
                _time.sleep(RETRY_WAIT_SEC)
    finally:
        _ls_backfill_resume(paused)

    if ok:
        status = "ok"
        # 성공 — 핵심 한 줄
        actives = last_result.get("actives", 0) if last_result else 0
        rows = last_result.get("rows", 0) if last_result else 0
        detail = f"{actives} 계약 / 적재 {rows:,} row"
    else:
        status = "fail"
        # 실패 — 자세히
        detail = (f"날짜: {today}\n"
                  f"시도: {MAX_ATTEMPTS}회 모두 검증 실패\n"
                  f"검증 결과: {verify_msg}")
        if last_err:
            detail += f"\n마지막 에러: {last_err}"
        elif last_result:
            detail += f"\n마지막 결과: {last_result}"
    notify_job("stockfut_today", status, started, detail=detail)


def job_etf_snapshot():
    """매일 08:30 KST — ETF PDF/마스터 스냅샷 (today + yesterday 2-pass).
    daily_update에서 분리 — 04:30엔 인포맥스 ingest 미완 (당일 PDF 빈 응답)."""
    from scripts.etf_snapshot import main as etf_main
    started = datetime.now(KST)
    logger.info("="*60)
    logger.info(f"[스케줄러] ETF 스냅샷 시작: {started}")
    logger.info("="*60)
    status, detail = "ok", ""
    try:
        etf_main()
        logger.info(f"[스케줄러] ETF 스냅샷 완료: {datetime.now(KST)}")
        # DB에서 적재 결과 조회 → 알림에 포함
        detail = _etf_snapshot_summary(started.date())
    except Exception as e:
        logger.error(f"[스케줄러] ETF 스냅샷 실패: {e}")
        status, detail = "fail", f"에러: {e}"
    notify_job("etf_snapshot", status, started, detail=detail)


def _etf_snapshot_summary(today_date) -> str:
    """오늘+직전 영업일의 ETF PDF / 마스터 적재 row 수 조회."""
    try:
        from scripts.daily_update import get_conn
        import datetime as _dt
        # 오늘 + 직전 영업일(가장 최근 weekday)
        yest = today_date - _dt.timedelta(days=1)
        while yest.weekday() >= 5:
            yest -= _dt.timedelta(days=1)
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT snapshot_date, COUNT(DISTINCT etf_code) AS etfs, COUNT(*) AS rows "
                    "FROM etf_portfolio_daily WHERE snapshot_date IN (%s, %s) "
                    "GROUP BY snapshot_date ORDER BY snapshot_date",
                    (yest, today_date))
                pdf_rows = cur.fetchall()
                cur.execute(
                    "SELECT snapshot_date, COUNT(*) FROM etf_master_daily "
                    "WHERE snapshot_date IN (%s, %s) GROUP BY snapshot_date ORDER BY snapshot_date",
                    (yest, today_date))
                master_rows = dict(cur.fetchall())
        finally:
            conn.close()
        lines = []
        for d, etfs, rows in pdf_rows:
            m = master_rows.get(d, 0)
            lines.append(f"{d}: PDF {etfs}개 ETF / {rows:,} row, 마스터 {m}")
        return "\n".join(lines) if lines else "적재 결과 없음"
    except Exception as e:
        return f"요약 조회 실패: {e}"


def job_quarterly_financials():
    """분기 재무지표 수집 (FnGuide XML) — 각 분기 제출 마감 직후 일요일 04:00
    타이밍: 6/1~7 (Q1 5/31 마감), 9/1~7 (H1 8/31), 12/1~7 (Q3 11/30), 4/1~7 (연간 3/31)
    """
    from scripts.collect_financials import run as run_financials
    started = datetime.now(KST)
    logger.info("=" * 60)
    logger.info(f"[스케줄러] 분기 재무지표 수집 시작: {started}")
    logger.info("=" * 60)
    status, detail = "ok", ""
    try:
        run_financials(delay=0.3)
        logger.info("[스케줄러] 분기 재무지표 수집 완료")
    except Exception as e:
        logger.error(f"[스케줄러] 분기 재무지표 수집 실패: {e}")
        status, detail = "fail", f"에러: {e}"
    notify_job("quarterly_financials", status, started, detail=detail)


def job_quarterly_sector():
    """분기 첫 번째 일요일 03:30 실행되는 FICS 업종 크롤링"""
    from scripts.crawl_sector import main as run_crawl
    started = datetime.now(KST)
    logger.info("="*60)
    logger.info(f"[스케줄러] 분기별 FICS 업종 크롤링 시작: {started}")
    logger.info("="*60)
    status, detail = "ok", ""
    try:
        run_crawl(missing_only=False)
        logger.info("[스케줄러] FICS 업종 크롤링 완료")
    except Exception as e:
        logger.error(f"[스케줄러] FICS 업종 크롤링 실패: {e}")
        status, detail = "fail", f"에러: {e}"
    notify_job("quarterly_sector", status, started, detail=detail)


def job_update_listed_shares():
    """매주 일요일 03:30 KST — LS t1102로 전종목 상장주식수 갱신 → floating_shares 테이블
    daily_update의 market_cap 계산 (close × total_shares) 데이터 소스."""
    from scripts.update_listed_shares import main as run_update_shares
    started = datetime.now(KST)
    logger.info("="*60)
    logger.info(f"[스케줄러] 상장주식수 갱신 시작: {started}")
    logger.info("="*60)
    status, detail = "ok", ""
    try:
        run_update_shares()
        logger.info("[스케줄러] 상장주식수 갱신 완료")
        # 갱신 row 수 조회 — created_at 기준 (방금 INSERT/UPDATE된 row)
        try:
            from scripts.daily_update import get_conn
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM floating_shares "
                    "WHERE created_at >= NOW() - INTERVAL '6 hours'")
                n = cur.fetchone()[0]
                cur.execute("SELECT MAX(base_date) FROM floating_shares")
                base_date = cur.fetchone()[0]
            conn.close()
            detail = f"floating_shares {n:,}개 갱신 (기준일 {base_date})"
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[스케줄러] 상장주식수 갱신 실패: {e}")
        status, detail = "fail", f"에러: {e}"
    notify_job("update_listed_shares", status, started, detail=detail)


def on_job_executed(event):
    logger.info(f"[스케줄러] 작업 완료: {event.job_id} "
                f"(실행시각: {event.scheduled_run_time})")


def on_job_error(event):
    logger.error(f"[스케줄러] 작업 오류: {event.job_id} → {event.exception}")


def startup_catchup():
    """scheduler 시작 시 누락된 주간 잡 즉시 보충.
    APScheduler misfire_grace_time이 시작 시점에 항상 잡아주진 않으므로 명시적 catch-up.

    대상:
    - weekly_backup: 최신 백업 파일 mtime > 8일 → 즉시 실행
    - update_listed_shares: floating_shares max(updated_at) > 8일 → 즉시 실행
    - daily_update: 자체 갭 backfill 로직 있어 제외
    - etf_snapshot: today+yesterday 2-pass로 자체 회수 → 제외
    - stockfut_today: historical 불가 → catch-up 불가
    - quarterly_*: 분기 빈도, 우선순위 낮음 → 제외
    """
    import threading
    now = datetime.now(KST)

    # weekly_backup 체크
    try:
        backup_dir = project_root / "backups"
        dumps = sorted(backup_dir.glob("backup_*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not dumps:
            stale_days = float("inf")
        else:
            stale_days = (now.timestamp() - dumps[0].stat().st_mtime) / 86400
        if stale_days > 8:
            logger.warning(f"[catch-up] 최신 백업 {stale_days:.1f}일 경과 — weekly_backup 보충 실행")
            threading.Thread(target=job_weekly_backup, daemon=True, name="catchup-backup").start()
        else:
            logger.info(f"[catch-up] 최신 백업 {stale_days:.1f}일 — 정상 (8일 이내)")
    except Exception as e:
        logger.error(f"[catch-up] weekly_backup 체크 실패: {e}")

    # update_listed_shares 체크 — floating_shares.base_date 기준
    try:
        from scripts.daily_update import get_conn
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(base_date) FROM floating_shares")
                last_base_date = cur.fetchone()[0]
        finally:
            conn.close()
        if last_base_date is None:
            stale_days = float("inf")
        else:
            stale_days = (now.date() - last_base_date).days
        if stale_days > 8:
            logger.warning(f"[catch-up] 상장주식수 {stale_days:.1f}일 경과 — update_listed_shares 보충 실행")
            threading.Thread(target=job_update_listed_shares, daemon=True, name="catchup-shares").start()
        else:
            logger.info(f"[catch-up] 상장주식수 {stale_days:.1f}일 — 정상 (8일 이내)")
    except Exception as e:
        logger.error(f"[catch-up] update_listed_shares 체크 실패: {e}")


def main():
    scheduler = BlockingScheduler(timezone=KST)

    # 이벤트 리스너
    scheduler.add_listener(on_job_executed, EVENT_JOB_EXECUTED)
    scheduler.add_listener(on_job_error,    EVENT_JOB_ERROR)

    # graceful shutdown — SIGTERM/SIGINT 수신 시 진행 중 job 끝날 때까지 대기 후 종료.
    # tmux send-keys C-c 또는 kill로 재시작해도 자식 daily_update 안 죽임.
    # (5/15 사고 재발 방지 — 그 때 5/14 일별 후속 단계 silent kill 됐었음)
    def _graceful_shutdown(signum, _frame):
        logger.warning(f"[스케줄러] 신호 {signum} 수신 → 진행 중 job 완료 대기 후 종료")
        scheduler.shutdown(wait=True)
        sys.exit(0)
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT,  _graceful_shutdown)

    # 잡 1: 매일 02:00 KST — 데이터 수집 + 배당 + LENS export
    scheduler.add_job(
        job_daily_update,
        trigger=CronTrigger(
            hour=2,
            minute=0,
            timezone=KST,
        ),
        id="daily_update",
        name="일별 데이터 수집 (OHLCV/수급/외인 + 배당 + LENS export)",
        misfire_grace_time=3600,   # 1시간 내 놓쳐도 재실행
        coalesce=True,             # 누적 실행 방지
        max_instances=1,
    )

    # 잡 2: 매주 일요일 03:00 KST — DB 백업
    scheduler.add_job(
        job_weekly_backup,
        trigger=CronTrigger(
            day_of_week="sun",
            hour=3,
            minute=0,
            timezone=KST,
        ),
        id="weekly_backup",
        name="주간 DB 백업",
        misfire_grace_time=7200,   # 2시간 내 놓쳐도 재실행
        coalesce=True,
        max_instances=1,
    )

    # 분봉 일배치 cron 제거 — daily_update 끝(job_daily_update 안)에 직렬 호출로 통합 (LENS 야간 사용 시간 확보)

    # 잡 6: 매일 08:30 KST — ETF PDF/마스터 스냅샷 (daily_update에서 분리, ingest 보장)
    scheduler.add_job(
        job_etf_snapshot,
        trigger=CronTrigger(hour=8, minute=30, timezone=KST),
        id="etf_snapshot",
        name="ETF PDF/마스터 스냅샷 (today + yesterday)",
        misfire_grace_time=1800,
        coalesce=True,
        max_instances=1,
    )

    # 잡 5: 매일 23:30 KST (월~금) — 주식선물 30초봉 당일 적재
    # 작업 시간 약 10분 → 23:30~23:40. 사용자 LENS는 23:40 ~ 다음날 04:30 자유.
    scheduler.add_job(
        job_stockfut_today,
        trigger=CronTrigger(day_of_week="mon-fri", hour=23, minute=30, timezone=KST),
        id="stockfut_today",
        name="주식선물 30초봉 당일 (LS t8406, historical 불가)",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    # 잡 4: 분기 재무지표 수집 — 각 분기 마감 후 첫 번째 일요일 04:00 (4/6/9/12월)
    scheduler.add_job(
        job_quarterly_financials,
        trigger=CronTrigger(
            month="4,6,9,12",
            day="1-7",
            day_of_week="sun",
            hour=4,
            minute=0,
            timezone=KST,
        ),
        id="quarterly_financials",
        name="분기 재무지표 수집 (FnGuide)",
        misfire_grace_time=7200,
        coalesce=True,
        max_instances=1,
    )

    # 잡 5: 분기 첫 번째 일요일 03:30 KST (1/4/7/10월) — FICS 업종 크롤링
    scheduler.add_job(
        job_quarterly_sector,
        trigger=CronTrigger(
            month="1,4,7,10",
            day="1-7",
            day_of_week="sun",
            hour=3,
            minute=30,
            timezone=KST,
        ),
        id="quarterly_sector",
        name="분기별 FICS 업종 크롤링",
        misfire_grace_time=7200,
        coalesce=True,
        max_instances=1,
    )

    # 잡 6: 매주 일요일 03:30 KST — 상장주식수 갱신 (LS t1102 → floating_shares)
    scheduler.add_job(
        job_update_listed_shares,
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=30, timezone=KST),
        id="update_listed_shares",
        name="상장주식수 갱신 (LS t1102)",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    now = datetime.now(KST)

    trigger_daily   = CronTrigger(hour=2, minute=0, timezone=KST)
    trigger_backup  = CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=KST)
    trigger_sector     = CronTrigger(month="1,4,7,10", day="1-7", day_of_week="sun", hour=3, minute=30, timezone=KST)
    trigger_financials = CronTrigger(month="4,6,9,12", day="1-7", day_of_week="sun", hour=4, minute=0, timezone=KST)
    next_daily      = trigger_daily.get_next_fire_time(None, now)
    next_backup     = trigger_backup.get_next_fire_time(None, now)
    next_sector     = trigger_sector.get_next_fire_time(None, now)
    next_financials = trigger_financials.get_next_fire_time(None, now)

    logger.info("="*60)
    logger.info("  한국 주식 데이터 수집 + 백업 스케줄러")
    logger.info("="*60)
    logger.info(f"  현재 시각    : {now.strftime('%Y-%m-%d %H:%M:%S KST')}")
    logger.info(f"  다음 수집    : {next_daily.strftime('%Y-%m-%d %H:%M KST') if next_daily else '미정'}")
    logger.info(f"  다음 백업    : {next_backup.strftime('%Y-%m-%d %H:%M KST') if next_backup else '미정'}")
    logger.info(f"  다음 섹터    : {next_sector.strftime('%Y-%m-%d %H:%M KST') if next_sector else '미정'}")
    logger.info(f"  수집 주기    : 매일 02:00 — OHLCV(LS t8451)/수급/외인 + 배당 + LENS export")
    logger.info(f"  백업 주기    : 매주 일요일 03:00  (7일 보관)")
    logger.info(f"  상장주식수   : 매주 일요일 03:30 — LS t1102 → floating_shares")
    logger.info(f"  섹터 주기    : 분기 첫 번째 일요일 03:30 (1/4/7/10월)")
    logger.info(f"  재무지표     : 분기 마감 후 첫 번째 일요일 04:00 (4/6/9/12월) — 다음: {next_financials.strftime('%Y-%m-%d %H:%M KST') if next_financials else '미정'}")
    logger.info(f"  보고서 저장  : reports/daily_update_YYYYMMDD.txt")
    logger.info("  종료: Ctrl+C")
    logger.info("="*60)

    # 시작 시 누락된 주간 잡 보충 (재시작 시점이 잡 fire 시각에 가까웠던 경우 대비)
    startup_catchup()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("\n[스케줄러] 종료됨")


if __name__ == "__main__":
    main()
