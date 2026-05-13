#!/bin/bash
# 백필 chain: KOSPI200 F → KOSDAQ150 F → 지수 → 1분봉 CONT
# 진행 중 KOSPI200 F 끝날 때까지 대기 후 자동 실행

set -e
cd /home/una0/projects/Finance_Data/KOREA
source venv/bin/activate

LOG="logs/chain_backfills.log"
echo "[$(date '+%H:%M:%S')] chain start" >> $LOG

# 1) KOSPI200 F 백필 끝날 때까지 대기
echo "[$(date '+%H:%M:%S')] wait for backfill_idxfut tmux" >> $LOG
while tmux ls 2>/dev/null | grep -q backfill_idxfut; do
    sleep 30
done
echo "[$(date '+%H:%M:%S')] backfill_idxfut finished" >> $LOG

# 2) KOSDAQ150 F + 다시 KOSPI200 F (idempotent) — backfill_futures_minute_bars.py가 둘 다 master로 받음
echo "[$(date '+%H:%M:%S')] starting KOSDAQ150 F backfill" >> $LOG
python -u scripts/backfill_futures_minute_bars.py --from 20260102 >> logs/backfill_futures_30sec_kqf.log 2>&1
echo "[$(date '+%H:%M:%S')] KOSDAQ150 F backfill done" >> $LOG

# 3) 지수 백필 (KOSPI200=101, KOSDAQ150=301)
echo "[$(date '+%H:%M:%S')] starting 지수 backfill" >> $LOG
python -u scripts/backfill_index_minute_bars.py --from 20260102 --codes 101,301 >> logs/backfill_index_30sec.log 2>&1
echo "[$(date '+%H:%M:%S')] 지수 backfill done" >> $LOG

# 4) 1분봉 backfill CONT (PID 2566327)
PID=$(pgrep -f "backfill_30sec_bars.py --from 20260306")
if [ -n "$PID" ]; then
    kill -CONT $PID
    echo "[$(date '+%H:%M:%S')] 1분봉 backfill CONT (PID $PID)" >> $LOG
else
    echo "[$(date '+%H:%M:%S')] 1분봉 backfill PID not found — skip CONT" >> $LOG
fi

echo "[$(date '+%H:%M:%S')] chain done" >> $LOG
