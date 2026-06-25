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

from scripts.daily_update import run_etf_daily_snapshot_pipeline, get_conn

KST = ZoneInfo("Asia/Seoul")

# etf_portfolio_daily 보존 정책 — 최근 N 영업일치만 FIFO 유지 (PDF는 최신만 의미).
# etf_master_daily 는 대상 아님(소형·전기간 보존).
PORTFOLIO_RETENTION_DAYS = 5


# 정상 케이스 wait: 60s polling, 최대 4h (daily_update 최장 실측 ~5h 기준 안전선)
WAIT_POLL_TIMEOUT_SEC = 4 * 60 * 60
# Deep retry: 4h 초과 후 2h 간격으로 최대 3회 추가 확인. 최종 abort 전까지 총 10h
DEEP_RETRY_INTERVAL_SEC = 2 * 60 * 60
DEEP_RETRY_MAX = 3


def _daily_update_running() -> bool:
    """pgrep 으로 daily_update.py 프로세스 존재 여부 확인. 오류 시 False(=진행 안 함 가정)."""
    try:
        r = subprocess.run(["pgrep", "-f", "scripts/daily_update.py"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def wait_for_daily_update():
    """daily_update.py가 진행 중이면 끝날 때까지 대기.
    1단계: 60s polling, 최대 4h (정상 케이스).
    2단계: 2h 간격 deep retry 최대 3회 (08:30~18:30 사이 다른 반복 잡 없어 안전).
    그 후에도 진행 중이면 RuntimeError — 강제 진행 시 인포맥스 충돌 우려."""
    start = time.monotonic()

    # 1단계: 60s polling
    while True:
        if not _daily_update_running():
            return
        elapsed = time.monotonic() - start
        if elapsed > WAIT_POLL_TIMEOUT_SEC:
            break
        now = datetime.now(KST)
        print(f"  [WAIT] daily_update 진행 중 ({now:%H:%M}, 누적 {elapsed/60:.0f}분) — 60s 후 재확인", flush=True)
        time.sleep(60)

    # 2단계: 2h deep retry 최대 3회
    for cycle in range(1, DEEP_RETRY_MAX + 1):
        now = datetime.now(KST)
        print(f"  [DEEP-WAIT {cycle}/{DEEP_RETRY_MAX}] 4h 초과 — 2h 후 재확인 ({now:%H:%M})", flush=True)
        time.sleep(DEEP_RETRY_INTERVAL_SEC)
        if not _daily_update_running():
            print(f"  [DEEP-WAIT] daily_update 종료 확인 — snapshot 재개", flush=True)
            return

    total_h = (time.monotonic() - start) / 3600
    raise RuntimeError(
        f"daily_update hang 확정 — {total_h:.1f}h 대기 후 abort "
        f"(60s polling 4h + deep retry {DEEP_RETRY_MAX}회). "
        f"오늘 ETF snapshot 포기 — 다음날 yesterday 2-pass로 부분 회수."
    )


def _prev_biz_day(d: date) -> date:
    """단순 평일 역행. 휴장(공휴일)은 ON CONFLICT가 처리 — 빈 응답이라 적재 0건."""
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:  # 토(5), 일(6) 스킵
        prev -= timedelta(days=1)
    return prev


def prune_portfolio_retention(keep: int = PORTFOLIO_RETENTION_DAYS) -> int:
    """etf_portfolio_daily 를 최근 `keep`개 snapshot_date(=영업일)만 FIFO 유지.
    삭제 행수 반환. distinct 날짜가 keep 이하면 아무것도 안 지움(안전)."""
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM etf_portfolio_daily
                    WHERE snapshot_date < (
                        SELECT MIN(d) FROM (
                            SELECT DISTINCT snapshot_date AS d
                            FROM etf_portfolio_daily
                            ORDER BY d DESC LIMIT %s
                        ) t
                    )
                """, (keep,))
                return cur.rowcount
    finally:
        conn.close()


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

    # 2-pass 후 FIFO 정리 — etf_portfolio_daily 최근 N영업일만 유지
    try:
        deleted = prune_portfolio_retention()
        print(f"[ETF FIFO] etf_portfolio_daily 최근 {PORTFOLIO_RETENTION_DAYS}영업일 유지 — {deleted:,} 행 삭제")
    except Exception as e:
        print(f"⚠️ ETF FIFO prune 오류: {e}")


if __name__ == "__main__":
    main()
