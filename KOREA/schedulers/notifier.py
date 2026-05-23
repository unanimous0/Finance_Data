"""Telegram 알림 헬퍼.

스케줄러 잡들이 시작/완료/실패 시 호출. .env의 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 사용.
둘 중 하나라도 비어있으면 silent skip — 운영 중단 없이 알림만 끔.
"""
import os
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

KST = ZoneInfo("Asia/Seoul")

# 프로젝트 루트의 .env 로드
load_dotenv(Path(__file__).parent.parent / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(BOT_TOKEN and CHAT_ID)


def send(text: str, silent: bool = False) -> bool:
    """Telegram 메시지 전송. 설정 없거나 실패 시 False 반환 (예외 안 던짐)."""
    if not is_configured():
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": text[:4000],  # Telegram 단일 메시지 한도 4096자
                "disable_notification": silent,
            },
            timeout=10,
        )
        if not r.ok:
            logger.warning(f"Telegram 전송 실패: {r.status_code} {r.text[:200]}")
        return r.ok
    except Exception as e:
        logger.warning(f"Telegram 전송 예외: {e}")
        return False


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}m"
    h, m = divmod(seconds / 60, 60)
    return f"{int(h)}h{int(m)}m"


def notify_job(
    job_name: str,
    status: str,
    started: datetime,
    detail: str = "",
) -> bool:
    """잡 결과 알림.

    status: 'ok' / 'fail' / 'noop'
    detail: 추가 정보 (row 수, 에러 메시지, 특이사항 등)
    """
    ended = datetime.now(KST)
    duration = (ended - started).total_seconds()

    tag = {"ok": "OK", "fail": "FAIL", "noop": "NOOP"}.get(status, status.upper())
    head = f"[{tag}] {job_name}"
    time_line = f"{started:%m-%d %H:%M} - {ended:%H:%M} ({_fmt_duration(duration)})"

    lines = [head, time_line]
    if detail:
        lines.append("")
        lines.append(detail.strip())

    # 실패면 sound 알림, 성공/noop은 silent
    silent = status != "fail"
    return send("\n".join(lines), silent=silent)
