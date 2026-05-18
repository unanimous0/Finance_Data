"""
ETF PDF + 마스터 일별 스냅샷 단독 실행 (08:30 KST cron).

daily_update에서 분리한 이유:
  - 04:30엔 인포맥스가 D 당일 PDF 데이터 미준비 (KODEX 200 등 빈 응답 확인)
  - 09시 이후 정상 반환 → 안전하게 08:30 실행
2-pass:
  - today (D)   : 당일 PDF 새벽 ingest 완료분
  - yesterday(D-1): 어제 partial로 받은 경우 UPSERT로 자동 정정

사용:
    python scripts/etf_snapshot.py            # today + yesterday 모두
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from scripts.daily_update import run_etf_daily_snapshot_pipeline

KST = ZoneInfo("Asia/Seoul")


def wait_for_daily_update():
    """daily_update.py가 진행 중이면 끝날 때까지 대기 (60s 단위 재확인).
    08:30 cron 시점에 04:30 daily_update가 아직 안 끝났을 경우 인포맥스 한도 충돌 회피."""
    while True:
        try:
            r = subprocess.run(["pgrep", "-f", "scripts/daily_update.py"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                return
        except Exception:
            return
        now = datetime.now(KST)
        print(f"  [WAIT] daily_update 진행 중 ({now:%H:%M}) — 60s 후 재확인", flush=True)
        time.sleep(60)


def _prev_biz_day(d: date) -> date:
    """단순 평일 역행. 휴장(공휴일)은 ON CONFLICT가 처리 — 빈 응답이라 적재 0건."""
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:  # 토(5), 일(6) 스킵
        prev -= timedelta(days=1)
    return prev


def main():
    wait_for_daily_update()  # daily_update가 늦게 끝나면 대기
    today_kst = datetime.now(KST).date()
    yesterday = _prev_biz_day(today_kst)
    print(f"[ETF snapshot 08:30] today={today_kst}, yesterday={yesterday}")

    for tag, dt in [("today", today_kst), ("yesterday", yesterday)]:
        try:
            run_etf_daily_snapshot_pipeline(dt)
        except Exception as e:
            print(f"⚠️ ETF snapshot {tag}({dt}) 단계 오류: {e}")


if __name__ == "__main__":
    main()
