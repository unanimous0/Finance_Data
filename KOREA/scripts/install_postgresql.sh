#!/bin/bash
# PostgreSQL 17 + TimescaleDB 설치 스크립트 (WSL/Ubuntu)

set -e  # 에러 발생시 중단

echo "================================================================================"
echo "🐘 PostgreSQL 17 + TimescaleDB 설치 시작"
echo "================================================================================"

# 1. PostgreSQL 공식 저장소 추가
echo ""
echo "📦 1단계: PostgreSQL 저장소 추가 중..."
sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
wget -qO- https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo tee /etc/apt/trusted.gpg.d/pgdg.asc &>/dev/null
echo "✅ PostgreSQL 저장소 추가 완료"

# 2. TimescaleDB 저장소 추가
echo ""
echo "📦 2단계: TimescaleDB 저장소 추가 중..."
sudo sh -c "echo 'deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -c -s) main' > /etc/apt/sources.list.d/timescaledb.list"
wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/timescaledb.gpg
echo "✅ TimescaleDB 저장소 추가 완료"

# 3. 패키지 목록 업데이트
echo ""
echo "🔄 3단계: 패키지 목록 업데이트 중..."
sudo apt update
echo "✅ 패키지 목록 업데이트 완료"

# 4. PostgreSQL 17 설치
echo ""
echo "📥 4단계: PostgreSQL 17 설치 중..."
sudo apt install -y postgresql-17 postgresql-contrib-17 postgresql-client-17
echo "✅ PostgreSQL 17 설치 완료"

# 5. TimescaleDB 설치
echo ""
echo "📥 5단계: TimescaleDB 설치 중..."
sudo apt install -y timescaledb-2-postgresql-17
echo "✅ TimescaleDB 설치 완료"

# 6. TimescaleDB 튜닝
echo ""
echo "⚙️  6단계: TimescaleDB 설정 최적화 중..."
sudo timescaledb-tune --quiet --yes
echo "✅ TimescaleDB 설정 완료"

# 7. PostgreSQL 시작
echo ""
echo "🚀 7단계: PostgreSQL 서비스 시작 중..."
sudo service postgresql start
sleep 2
echo "✅ PostgreSQL 서비스 시작 완료"

# 8. PostgreSQL 상태 확인
echo ""
echo "🔍 8단계: PostgreSQL 상태 확인 중..."
sudo service postgresql status
echo ""

# 9. PostgreSQL 버전 확인
echo ""
echo "📌 9단계: 설치된 버전 확인..."
psql --version
echo ""

# 10. 데이터베이스 생성
echo ""
echo "💾 10단계: 데이터베이스 생성 중..."
sudo -u postgres psql -c "SELECT version();" 2>&1 | head -3
sudo -u postgres createdb korea_stock_data 2>/dev/null || echo "⚠️  데이터베이스가 이미 존재합니다"
echo "✅ 데이터베이스 생성 완료"

# 11. TimescaleDB 확장 활성화
echo ""
echo "🔌 11단계: TimescaleDB 확장 활성화 중..."
sudo -u postgres psql -d korea_stock_data -c "CREATE EXTENSION IF NOT EXISTS timescaledb;" 2>&1 | grep -v "NOTICE" || true
echo "✅ TimescaleDB 확장 활성화 완료"

# 12. 연결 테스트
echo ""
echo "🧪 12단계: 데이터베이스 연결 테스트..."
sudo -u postgres psql -d korea_stock_data -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'timescaledb';"

echo ""
echo "================================================================================"
echo "✅ 설치 완료!"
echo "================================================================================"
echo ""
echo "📋 다음 단계:"
echo "  1. PostgreSQL 비밀번호 설정 (선택사항):"
echo "     sudo -u postgres psql -c \"ALTER USER postgres PASSWORD 'your_password';\""
echo ""
echo "  2. .env 파일 확인:"
echo "     cat .env"
echo ""
echo "  3. 데이터베이스 스키마 생성:"
echo "     source venv/bin/activate && python scripts/alter_stocks_schema.py"
echo ""
echo "================================================================================"
