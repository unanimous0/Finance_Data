"""주식선물(L) NEXT 차근월 백필 — 안전윈도우 재개 러너 (장기 가동용).

배경: 2026-06 선물 차근월(NEXT) 계약 오선택 버그 수정 후, 지수선물(F)은 전기간
재수집 완료했으나 주식선물(L, 275종목)은 인포맥스 일일 한도에 걸려 부분 완료.
이 러너가 안전윈도우(10:00~24:00)에서 남은 한도로 며칠에 걸쳐 자동 이어받음.

동작:
  - 안전윈도우(10:00~24:00 KST) 밖이면 10:00까지 대기 (반복잡 + 한도리셋 우선권)
  - in-process 잡(02:00 daily_update / 08:30 종합보충)이 스케줄러 로그상 진행 중이면 대기
    (pgrep로는 안 잡힘 — 별도 프로세스가 아니라 daily_scheduler 안 함수라서)
  - backfill_futures_ohlcv(L) 실행, 완료 종목은 상태파일에 기록 → 재실행 시 skip
  - 일일 한도 도달 시 깨끗이 중단 후 다음날 10:00까지 대기 → 이어받기
  - 전체 완료되면 종료

사용: tmux 세션에서
    python scripts/backfill_futures_L_safewindow.py
"""
from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from scripts.backfill_indices_futures import backfill_futures_ohlcv, _conn
from collectors.infomax import InfomaxClient

KST = ZoneInfo("Asia/Seoul")
SAFE_WINDOW_START_HOUR = 10
START = date(2022, 1, 3)

STATE = project_root / "cache" / "futures_L_backfill_done.txt"
SCHED_LOG = project_root / "logs" / "scheduler.log"


def load_done() -> set:
    if STATE.exists():
        return {ln.strip() for ln in STATE.read_text().splitlines() if ln.strip()}
    return set()


def mark_done(uc: str):
    STATE.parent.mkdir(exist_ok=True)
    with STATE.open("a") as f:
        f.write(uc + "\n")


def wait_for_safe_window():
    now = datetime.now(KST)
    if now.hour >= SAFE_WINDOW_START_HOUR:
        return
    resume = now.replace(hour=SAFE_WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    secs = (resume - now).total_seconds()
    print(f"  [SAFE-WINDOW] {now:%H:%M} — {resume:%H:%M}까지 {secs/3600:.1f}h 대기 (반복잡 우선권)", flush=True)
    time.sleep(secs)


def wait_until_next_window():
    """일일 한도 도달 → 다음 안전윈도우(다음날 10:00)까지 대기."""
    now = datetime.now(KST)
    if now.hour < SAFE_WINDOW_START_HOUR:
        resume = now.replace(hour=SAFE_WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    else:
        resume = (now + timedelta(days=1)).replace(hour=SAFE_WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    secs = (resume - now).total_seconds()
    print(f"  [LIMIT] 일일 한도 — {resume:%m/%d %H:%M}까지 {secs/3600:.1f}h 대기 (한도리셋+반복잡)", flush=True)
    time.sleep(secs)


def _tail(path: Path, n_bytes: int = 16384) -> str:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n_bytes))
            return f.read().decode("utf-8", "replace")
    except Exception:
        return ""


def scheduler_busy() -> bool:
    """스케줄러 in-process 잡(02:00 daily / 08:30 종합보충)이 진행 중인지 로그로 판정.
    마지막 'Running job'/'시작'이 짝 'executed successfully'/'완료'보다 뒤면 진행 중."""
    last_run = last_done = -1
    for idx, ln in enumerate(_tail(SCHED_LOG).splitlines()):
        if "Running job" in ln or "보충 시작" in ln:
            last_run = idx
        if "executed successfully" in ln or "작업 완료" in ln:
            last_done = idx
    return last_run > last_done


def main():
    client = InfomaxClient()
    print(f"=== L NEXT 안전윈도우 백필 시작 {datetime.now(KST):%Y-%m-%d %H:%M} KST ===", flush=True)
    while True:
        wait_for_safe_window()
        # in-process 반복잡 회피 (08:30 보충이 10시 넘겨 길어질 때)
        while scheduler_busy():
            print(f"  [PAUSE] 스케줄러 in-process 잡 진행 중 ({datetime.now(KST):%H:%M}) — 120s 후 재확인", flush=True)
            time.sleep(120)

        done = load_done()
        conn = _conn()
        try:
            end = datetime.now(KST).date()
            res = backfill_futures_ohlcv(conn, client, START, end,
                                         underlying_types=("L",),
                                         skip_codes=done, on_complete=mark_done)
        finally:
            conn.close()

        if not res["stopped_by_limit"]:
            print(f"=== L NEXT 백필 전체 완료 — 누적 {len(load_done())} underlying. 종료 ===", flush=True)
            break
        wait_until_next_window()


if __name__ == "__main__":
    main()
