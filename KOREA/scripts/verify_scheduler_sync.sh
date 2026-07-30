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

# ── scheduler 프로세스 찾기 ──────────────────────────────────────────────
# `pgrep -f` 단독은 위험하다: 명령줄에 그 문자열이 들어간 **아무 프로세스나** 잡는다.
# 특히 재시작+검증을 한 줄에서 돌리면 래퍼 셸의 cmdline에
#   `python schedulers/daily_scheduler.py 2>&1 | tee ...`
# 가 통째로 들어가 셸이 매칭되고, 셸이 python보다 먼저 떠서 PID가 작으므로
# `head -1`이 그걸 집는다 → 시작 시각이 엉뚱해져 "죽었다/반영됐다"를 오판한다.
# (2026-07-30 실제 오판: 스크립트는 PID 3853175를 봤지만 진짜 python은 3853260)
# → argv[0]이 python이고, 인자에 스크립트 경로가 있는 프로세스만 인정한다.
is_scheduler_proc() {
  local p="$1" args argv0 a
  [ -r "/proc/$p/cmdline" ] || return 1
  mapfile -d '' -t args < "/proc/$p/cmdline" 2>/dev/null || return 1
  argv0="${args[0]:-}"
  case "$argv0" in *python*) ;; *) return 1 ;; esac
  for a in "${args[@]:1}"; do
    case "$a" in *schedulers/daily_scheduler.py) return 0 ;; esac
  done
  return 1
}

PIDS=()
while IFS= read -r p; do
  [ "$p" = "$$" ] && continue
  is_scheduler_proc "$p" && PIDS+=("$p")
done < <(pgrep -f "schedulers/daily_scheduler\.py" 2>/dev/null || true)

if [ "${#PIDS[@]}" -eq 0 ]; then
  echo "⛔ daily_scheduler 프로세스 없음 — scheduler 미가동"
  exit 2
fi
if [ "${#PIDS[@]}" -gt 1 ]; then
  echo "⚠️  daily_scheduler 프로세스가 ${#PIDS[@]}개 — 중복 가동 의심 (가장 오래된 것 기준으로 검사)"
  for p in "${PIDS[@]}"; do
    echo "     PID $p  시작=$(ps -o lstart= --pid "$p" 2>/dev/null | sed 's/^ *//')"
  done
fi

# 여러 개면 가장 오래된 것 = 상주 데몬
PID=""
START_EPOCH=""
for p in "${PIDS[@]}"; do
  e=$(date -d "$(ps -o lstart= --pid "$p")" +%s)
  if [ -z "$START_EPOCH" ] || [ "$e" -lt "$START_EPOCH" ]; then
    START_EPOCH="$e"; PID="$p"
  fi
done
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
