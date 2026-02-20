"""
일별 데이터 업데이트 스케줄러
실행 시간: 매일 16:30 KST

실행법:
    python schedulers/daily_scheduler.py          # 포그라운드 실행 (Ctrl+C로 종료)
    nohup python schedulers/daily_scheduler.py &  # 백그라운드 실행
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

# logs 폴더 생성
(project_root / "logs").mkdir(exist_ok=True)


def job_daily_update():
    """매일 16:30 실행되는 업데이트 작업"""
    from scripts.daily_update import main as run_daily
    logger.info("="*60)
    logger.info(f"[스케줄러] 일별 업데이트 시작: {datetime.now(KST)}")
    logger.info("="*60)
    run_daily()


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

    # 매일 16:30 KST 실행 (월~금)
    scheduler.add_job(
        job_daily_update,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=16,
            minute=30,
            timezone=KST,
        ),
        id="daily_update",
        name="일별 OHLCV/시가총액/수급 업데이트",
        misfire_grace_time=3600,   # 1시간 내 실행 놓쳐도 재실행
        coalesce=True,             # 누적 실행 방지 (중복 실행 X)
        max_instances=1,           # 동시 실행 1개만
    )

    now = datetime.now(KST)
    trigger = CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=KST)
    next_run = trigger.get_next_fire_time(None, now)

    logger.info("="*60)
    logger.info("  한국 주식 일별 데이터 업데이트 스케줄러")
    logger.info("="*60)
    logger.info(f"  현재 시각  : {now.strftime('%Y-%m-%d %H:%M:%S KST')}")
    logger.info(f"  다음 실행  : {next_run.strftime('%Y-%m-%d %H:%M:%S KST') if next_run else '미정'}")
    logger.info(f"  실행 주기  : 매일 16:30 (월~금, KST)")
    logger.info(f"  대상 테이블: ohlcv_daily, market_cap_daily, investor_trading")
    logger.info(f"  보고서 저장: reports/daily_update_YYYYMMDD.txt")
    logger.info("  종료: Ctrl+C")
    logger.info("="*60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("\n[스케줄러] 종료됨")


if __name__ == "__main__":
    main()
