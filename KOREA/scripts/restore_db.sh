#!/bin/bash
# DB 복원 스크립트
# 사용법: bash scripts/restore_db.sh <dump_file>
#
# pg_restore 후 TimescaleDB hypertable 특성으로 유니크 인덱스가 소실됨
# → 이 스크립트에서 복원 + 인덱스 재생성을 한 번에 처리

set -e

PGBIN="/opt/homebrew/opt/postgresql@17/bin"
DB_USER="unanimous0"
DB_NAME="korea_stock_data"
DUMP_FILE="$1"

if [ -z "$DUMP_FILE" ]; then
    echo "사용법: bash scripts/restore_db.sh <dump_file>"
    exit 1
fi

if [ ! -f "$DUMP_FILE" ]; then
    echo "❌ 파일을 찾을 수 없음: $DUMP_FILE"
    exit 1
fi

echo "=============================="
echo " Korea Stock DB 복원 시작"
echo " 파일: $DUMP_FILE"
echo "=============================="

# 1. 기존 DB 삭제 후 재생성
echo "[1/3] DB 재생성..."
$PGBIN/psql -U $DB_USER -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();" \
    > /dev/null 2>&1 || true
$PGBIN/dropdb -U $DB_USER --if-exists $DB_NAME
$PGBIN/createdb -U $DB_USER $DB_NAME
echo "  ✅ DB 재생성 완료"

# 2. pg_restore
echo "[2/3] pg_restore 실행 중... (수 분 소요)"
$PGBIN/pg_restore -U $DB_USER -d $DB_NAME "$DUMP_FILE" 2>&1 | grep -v "경고" || true
echo "  ✅ pg_restore 완료"

# 3. 유니크 인덱스 재생성 (TimescaleDB pg_restore 시 항상 소실됨)
echo "[3/3] 유니크 인덱스 재생성..."
$PGBIN/psql -U $DB_USER -d $DB_NAME -c "
CREATE UNIQUE INDEX IF NOT EXISTS uq_ohlcv_time_stock
    ON ohlcv_daily (time, stock_code);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mktcap_time_stock
    ON market_cap_daily (time, stock_code);
CREATE UNIQUE INDEX IF NOT EXISTS uq_investor_time_stock_type
    ON investor_trading (time, stock_code, investor_type);
"
echo "  ✅ 인덱스 재생성 완료"

echo ""
echo "=============================="
echo " 복원 완료!"
echo "=============================="
