"""인포맥스 한도를 쓰는 잡의 가동 상태를 프로세스 간에 공유.

배경 (2026-07-30):
  08:30 아침 종합 보충 / 02:00 daily_update 는 스케줄러 안에서 **in-process 함수**로
  돈다 → `pgrep -f scripts/etf_snapshot.py` 류로는 절대 안 잡힌다 (CLAUDE.md 경고).
  그래서 백필은 하드코딩 시간창(SNAPSHOT_WINDOW = 08:25~10:05)으로 회피해 왔는데,
  ETF 수 증가 + 보충 작업이 붙으면서 잡 종료가 10:45~10:50으로 밀려 창이 어긋났다.
  → 시간 추측 대신 **스케줄러가 직접 알리고 소비자가 읽는다.**

설계:
  - **잡 하나당 마커 파일 하나** (logs/infomax_busy.d/<token>.json).
    단일 파일이면 잡이 겹칠 때 나중 잡이 앞 잡의 마커를 덮어쓰고, 자기가 끝날 때
    지워버려 아직 도는 잡이 없는 것처럼 보인다. 디렉터리 방식은 그 문제가 없다.
  - **PID를 함께 기록한다.** 스케줄러가 kill -9 등으로 죽어 마커가 남더라도
    PID가 살아있지 않으면 stale로 보고 무시(+청소) → 백필이 영원히 멈추는 사고 방지.

이 모듈은 의존성이 표준 라이브러리뿐이다 (APScheduler 등을 끌어오지 않음) —
백필 같은 외부 스크립트가 가볍게 import 할 수 있어야 하므로.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

BUSY_DIR = Path(__file__).parent.parent / "logs" / "infomax_busy.d"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True          # 존재하지만 권한 없음 = 살아있음


def active_jobs() -> list[dict]:
    """지금 인포맥스를 쓰는 잡 목록. stale(PID 죽음/깨진 파일) 마커는 청소하고 제외."""
    try:
        entries = sorted(BUSY_DIR.glob("*.json"))
    except OSError:
        return []
    alive = []
    for p in entries:
        try:
            info = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue          # 쓰는 도중일 수 있음 — 지우지 말고 다음 폴링에서 재확인
        pid = info.get("pid")
        if isinstance(pid, int) and _pid_alive(pid):
            alive.append(info)
        else:
            try:
                p.unlink()    # 죽은 프로세스의 잔재 청소
            except OSError:
                pass
    return alive


def active_job() -> dict | None:
    """가동 중인 잡이 있으면 그중 하나, 없으면 None."""
    jobs = active_jobs()
    return jobs[0] if jobs else None


@contextmanager
def infomax_busy(job_name: str):
    """인포맥스 한도를 쓰는 구간을 마커로 표시. 예외가 나도 반드시 정리한다."""
    token = uuid.uuid4().hex
    path = BUSY_DIR / f"{token}.json"
    info = {"job": job_name, "pid": os.getpid(), "token": token,
            "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        BUSY_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)      # 원자적 교체 — 읽는 쪽이 반쪽 파일을 못 봄
    except OSError:
        pass                       # 마커 실패로 잡 자체를 막지는 않는다
    try:
        yield
    finally:
        try:
            path.unlink()          # 내 파일만 지움 — 겹친 다른 잡에 영향 없음
        except OSError:
            pass
