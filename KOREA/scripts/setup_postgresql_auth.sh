#!/bin/bash
# PostgreSQL 인증 설정 스크립트 (로컬 개발 환경)

set -e

echo "================================================================================"
echo "🔐 PostgreSQL 인증 설정"
echo "================================================================================"

# PostgreSQL 설정 파일 경로 찾기
PG_HBA_CONF=$(sudo -u postgres psql -t -P format=unaligned -c 'SHOW hba_file;')
echo "📄 설정 파일: $PG_HBA_CONF"

# 백업 생성
echo ""
echo "💾 설정 파일 백업 중..."
sudo cp "$PG_HBA_CONF" "$PG_HBA_CONF.backup.$(date +%Y%m%d_%H%M%S)"
echo "✅ 백업 완료: $PG_HBA_CONF.backup.*"

# 로컬 연결을 trust로 변경
echo ""
echo "⚙️  인증 방식을 trust로 변경 중..."
sudo sed -i 's/^local\s\+all\s\+postgres\s\+peer/local   all             postgres                                trust/' "$PG_HBA_CONF"
sudo sed -i 's/^local\s\+all\s\+all\s\+peer/local   all             all                                     trust/' "$PG_HBA_CONF"
sudo sed -i 's/^host\s\+all\s\+all\s\+127\.0\.0\.1\/32\s\+scram-sha-256/host    all             all             127.0.0.1\/32            trust/' "$PG_HBA_CONF"
sudo sed -i 's/^host\s\+all\s\+all\s\+::1\/128\s\+scram-sha-256/host    all             all             ::1\/128                 trust/' "$PG_HBA_CONF"

echo "✅ 인증 방식 변경 완료"

# PostgreSQL 재시작
echo ""
echo "🔄 PostgreSQL 재시작 중..."
sudo service postgresql restart
sleep 2
echo "✅ PostgreSQL 재시작 완료"

# 연결 테스트
echo ""
echo "🧪 연결 테스트..."
psql -U postgres -d korea_stock_data -c "SELECT version();" | head -3

echo ""
echo "================================================================================"
echo "✅ 설정 완료!"
echo "================================================================================"
echo ""
echo "⚠️  주의: trust 인증은 로컬 개발 환경에만 사용하세요."
echo "   프로덕션 환경에서는 비밀번호 인증을 사용해야 합니다."
echo ""
