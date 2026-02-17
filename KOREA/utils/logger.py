"""
로깅 설정 모듈

Loguru를 사용한 구조화된 로깅
"""

import sys
from pathlib import Path

from loguru import logger

from config.settings import settings


def setup_logger():
    """로거 설정 초기화"""

    # 기본 로거 제거
    logger.remove()

    # 로그 포맷
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    # 콘솔 출력 (개발 환경)
    if settings.is_development:
        logger.add(
            sys.stdout,
            format=log_format,
            level=settings.LOG_LEVEL,
            colorize=True,
        )

    # 파일 출력 (모든 환경)
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    # 일반 로그 (rotation: 500MB마다, retention: 30일)
    logger.add(
        logs_dir / "app_{time:YYYY-MM-DD}.log",
        format=log_format,
        level=settings.LOG_LEVEL,
        rotation="500 MB",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
    )

    # 에러 로그 (별도 파일)
    logger.add(
        logs_dir / "error_{time:YYYY-MM-DD}.log",
        format=log_format,
        level="ERROR",
        rotation="100 MB",
        retention="90 days",
        compression="zip",
        encoding="utf-8",
    )

    logger.info("Logger initialized successfully")
    logger.debug(f"Log level: {settings.LOG_LEVEL}")
    logger.debug(f"Environment: {settings.APP_ENV}")


# 애플리케이션 시작시 자동 초기화
setup_logger()


if __name__ == "__main__":
    # 로거 테스트
    logger.debug("This is a DEBUG message")
    logger.info("This is an INFO message")
    logger.warning("This is a WARNING message")
    logger.error("This is an ERROR message")

    try:
        1 / 0
    except Exception as e:
        logger.exception("Exception caught")
