"""
데이터 수집 + 백업 스케줄러

잡 목록:
    daily_update      — 매일 05:30 KST (월~일)           OHLCV/시가총액/수급/외국인지분율 + 배당 + LENS export
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
    """매일 05:30 KST 실행 — daily_update.main()이 dividend pipeline + LENS export까지 자동 호출"""
    from scripts.daily_update import main as run_daily
    logger.info("="*60)
    logger.info(f"[스케줄러] 일별 업데이트 시작: {datetime.now(KST)}")
    logger.info("="*60)
    run_daily()


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

    # 잡 1: 매일 05:30 KST — 데이터 수집 + 배당 + LENS export
    scheduler.add_job(
        job_daily_update,
        trigger=CronTrigger(
            hour=5,
            minute=30,
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

    # 잡 4: 분기 첫 번째 일요일 03:30 KST (1/4/7/10월) — FICS 업종 크롤링
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

    now = datetime.now(KST)

    trigger_daily   = CronTrigger(hour=5, minute=30, timezone=KST)
    trigger_backup  = CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=KST)
    trigger_sector  = CronTrigger(month="1,4,7,10", day="1-7", day_of_week="sun", hour=3, minute=30, timezone=KST)
    next_daily  = trigger_daily.get_next_fire_time(None, now)
    next_backup = trigger_backup.get_next_fire_time(None, now)
    next_sector = trigger_sector.get_next_fire_time(None, now)

    logger.info("="*60)
    logger.info("  한국 주식 데이터 수집 + 백업 스케줄러")
    logger.info("="*60)
    logger.info(f"  현재 시각    : {now.strftime('%Y-%m-%d %H:%M:%S KST')}")
    logger.info(f"  다음 수집    : {next_daily.strftime('%Y-%m-%d %H:%M KST') if next_daily else '미정'}")
    logger.info(f"  다음 백업    : {next_backup.strftime('%Y-%m-%d %H:%M KST') if next_backup else '미정'}")
    logger.info(f"  다음 섹터    : {next_sector.strftime('%Y-%m-%d %H:%M KST') if next_sector else '미정'}")
    logger.info(f"  수집 주기    : 매일 05:30 — OHLCV/수급/외인 + 배당 + LENS export")
    logger.info(f"  백업 주기    : 매주 일요일 03:00  (7일 보관)")
    logger.info(f"  섹터 주기    : 분기 첫 번째 일요일 03:30 (1/4/7/10월)")
    logger.info(f"  보고서 저장  : reports/daily_update_YYYYMMDD.txt")
    logger.info("  종료: Ctrl+C")
    logger.info("="*60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("\n[스케줄러] 종료됨")


if __name__ == "__main__":
    main()
