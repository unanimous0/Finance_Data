#!/usr/bin/env bash
# scheduler 재시작 후 코드 반영 검증.
#
# 가동 중인 daily_scheduler 프로세스 시작 시각과, scheduler가 직접/간접 참조하는
# 모든 .py 파일의 mtime을 비교. 시작 시각보다 나중에 수정된 파일이 있으면
# "그 변경은 재시작에 반영 안 됨" → 재시작 필요로 경고.
#
# 사용: bash scripts/verify_scheduler_sync.sh
# 종료코드: 0 = 모두 반영됨 / 1 = stale 파일 있음(재시작 필요) / 2 = scheduler 미가동

set -euo pipefail
cd "$(dirname "$0")/.."   # 프로젝트 루트 (KOREA/)

PID=$(pgrep -f "schedulers/daily_scheduler.py" | head -1 || true)
if [ -z "${PID:-}" ]; then
  echo "⛔ daily_scheduler 프로세스 없음 — scheduler 미가동"
  exit 2
fi

# 프로세스 시작 epoch
START_EPOCH=$(date -d "$(ps -o lstart= --pid "$PID")" +%s)
START_HUMAN=$(date -d "@$START_EPOCH" '+%Y-%m-%d %H:%M:%S')
echo "scheduler PID=$PID 시작=$START_HUMAN"
echo "────────────────────────────────────────────"

# scheduler가 직접/간접 import하는 영역 (잡 함수 lazy import 포함)
SCAN_PATHS=(
  schedulers
  collectors
  config
  validators
  scripts/daily_update.py
  scripts/etf_snapshot.py
  scripts/update_listed_shares.py
  scripts/backup_db.py
  scripts/crawl_sector.py
  scripts/collect_financials.py
  scripts/backfill_30sec_bars.py
  scripts/backfill_index_minute_bars.py
  scripts/backfill_futures_minute_bars.py
  scripts/backfill_dividends.py
  scripts/export_dividends.py
  scripts/export_krx_holidays.py
)

stale=0
while IFS= read -r f; do
  mt=$(stat -c '%Y' "$f")
  if [ "$mt" -gt "$START_EPOCH" ]; then
    echo "⛔ 재시작 후 수정됨: $(stat -c '%y' "$f" | cut -d. -f1)  $f"
    stale=$((stale+1))
  fi
done < <(find "${SCAN_PATHS[@]}" -name '*.py' -not -path '*/__pycache__/*' 2>/dev/null)

echo "────────────────────────────────────────────"
if [ "$stale" -eq 0 ]; then
  echo "✅ 모든 참조 .py가 scheduler 시작 이전 — 변경 전부 반영됨"
  exit 0
else
  echo "⛔ $stale 개 파일이 scheduler 시작 후 수정됨 — 재시작 필요!"
  echo "   (이 변경들은 현재 가동 중 프로세스에 반영 안 됨)"
  exit 1
fi
