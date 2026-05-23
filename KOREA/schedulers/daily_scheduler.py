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
    logger.info("="*60)
    logger.info(f"[스케줄러] 일별 업데이트 시작: {datetime.now(KST)}")
    logger.info("="*60)
    try:
        run_daily()
    except Exception as e:
        logger.error(f"[스케줄러] daily_update 본체 실패: {e}")

    # daily_update 끝나고 분봉 일배치 직렬 호출 (한낮 LS 부하 회피, 사용자 활동 시작 전)
    logger.info("="*60)
    logger.info(f"[스케줄러] 분봉 일배치 시작 (daily_update 후속): {datetime.now(KST)}")
    logger.info("="*60)
    try:
        job_minute_bars_daily()
    except Exception as e:
        logger.error(f"[스케줄러] 분봉 일배치 실패: {e}")


def job_weekly_backup():
    """매주 일요일 03:00 실행되는 백업 작업"""
    from scripts.backup_db import run_backup, cleanup_old_backups
    logger.info("="*60)
    logger.info(f"[스케줄러] 주간 백업 시작: {datetime.now(KST)}")
    logger.info("="*60)
    try:
        backup_file = run_backup()
        cleanup_old_backups()
        logger.info(f"[스케줄러] 백업 완료: {backup_file.name}")
    except Exception as e:
        logger.error(f"[스케줄러] 백업 실패: {e}")


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


def job_stockfut_today():
    """매일 15:35 KST (장 마감 직후) — 주식선물 30초봉 당일 적재 (LS t8406).
    historical 불가능 → 매일 받지 않으면 영구 손실."""
    from scripts.daily_update import (run_stockfut_minute_today_pipeline,
                                       _ls_backfill_pause, _ls_backfill_resume)
    today = datetime.now(KST).date()
    logger.info("="*60)
    logger.info(f"[스케줄러] 주식선물 당일 30초봉 시작: {datetime.now(KST)}")
    logger.info("="*60)
    paused = _ls_backfill_pause()
    try:
        result = run_stockfut_minute_today_pipeline(today)
        logger.info(f"[스케줄러] 주식선물 당일 30초봉 완료: {result}")
    except Exception as e:
        logger.error(f"[스케줄러] 주식선물 당일 30초봉 실패: {e}")
    finally:
        _ls_backfill_resume(paused)


def job_etf_snapshot():
    """매일 08:30 KST — ETF PDF/마스터 스냅샷 (today + yesterday 2-pass).
    daily_update에서 분리 — 04:30엔 인포맥스 ingest 미완 (당일 PDF 빈 응답)."""
    from scripts.etf_snapshot import main as etf_main
    logger.info("="*60)
    logger.info(f"[스케줄러] ETF 스냅샷 시작: {datetime.now(KST)}")
    logger.info("="*60)
    try:
        etf_main()
        logger.info(f"[스케줄러] ETF 스냅샷 완료: {datetime.now(KST)}")
    except Exception as e:
        logger.error(f"[스케줄러] ETF 스냅샷 실패: {e}")


def job_quarterly_financials():
    """분기 재무지표 수집 (FnGuide XML) — 각 분기 제출 마감 직후 일요일 04:00
    타이밍: 6/1~7 (Q1 5/31 마감), 9/1~7 (H1 8/31), 12/1~7 (Q3 11/30), 4/1~7 (연간 3/31)
    """
    from scripts.collect_financials import run as run_financials
    logger.info("=" * 60)
    logger.info(f"[스케줄러] 분기 재무지표 수집 시작: {datetime.now(KST)}")
    logger.info("=" * 60)
    try:
        run_financials(delay=0.3)
        logger.info("[스케줄러] 분기 재무지표 수집 완료")
    except Exception as e:
        logger.error(f"[스케줄러] 분기 재무지표 수집 실패: {e}")


def job_quarterly_sector():
    """분기 첫 번째 일요일 03:30 실행되는 FICS 업종 크롤링"""
    from scripts.crawl_sector import main as run_crawl
    logger.info("="*60)
    logger.info(f"[스케줄러] 분기별 FICS 업종 크롤링 시작: {datetime.now(KST)}")
    logger.info("="*60)
    try:
        run_crawl(missing_only=False)
        logger.info("[스케줄러] FICS 업종 크롤링 완료")
    except Exception as e:
        logger.error(f"[스케줄러] FICS 업종 크롤링 실패: {e}")


def job_update_listed_shares():
    """매주 일요일 03:30 KST — LS t1102로 전종목 상장주식수 갱신 → floating_shares 테이블
    daily_update의 market_cap 계산 (close × total_shares) 데이터 소스."""
    from scripts.update_listed_shares import main as run_update_shares
    logger.info("="*60)
    logger.info(f"[스케줄러] 상장주식수 갱신 시작: {datetime.now(KST)}")
    logger.info("="*60)
    try:
        run_update_shares()
        logger.info("[스케줄러] 상장주식수 갱신 완료")
    except Exception as e:
        logger.error(f"[스케줄러] 상장주식수 갱신 실패: {e}")


def on_job_executed(event):
    logger.info(f"[스케줄러] 작업 완료: {event.job_id} "
                f"(실행시각: {event.scheduled_run_time})")


def on_job_error(event):
    logger.error(f"[스케줄러] 작업 오류: {event.job_id} → {event.exception}")


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

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("\n[스케줄러] 종료됨")


if __name__ == "__main__":
    main()
