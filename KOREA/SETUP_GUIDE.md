# 🚀 환경 설정 가이드 (SETUP GUIDE)

> 새로운 컴퓨터(집/회사)에서 프로젝트를 시작할 때 이 가이드를 따라하세요.

---

## 📋 사전 요구사항

- **OS**: macOS, Linux, Windows (WSL)
- **Python**: 3.11 이상
- **Git**: 버전 관리용
- **PostgreSQL**: 15 이상 권장

---

## 🔧 1단계: PostgreSQL + TimescaleDB 설치

### macOS (Homebrew 사용)

```bash
# Homebrew 업데이트
brew update

# PostgreSQL 설치 (버전 15)
brew install postgresql@15

# TimescaleDB 설치
brew install timescaledb

# PostgreSQL 서비스 시작
brew services start postgresql@15

# TimescaleDB 튜닝 (선택사항, 권장)
timescaledb-tune --quiet --yes
```

### Linux (Ubuntu/Debian)

```bash
# PostgreSQL 설치
sudo apt update
sudo apt install postgresql-15 postgresql-contrib-15

# TimescaleDB 추가
sudo sh -c "echo 'deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -c -s) main' > /etc/apt/sources.list.d/timescaledb.list"
wget --quiet -O - https://packagecloud.io/timescale/timescaledb/gpgkey | sudo apt-key add -
sudo apt update
sudo apt install timescaledb-2-postgresql-15

# TimescaleDB 튜닝
sudo timescaledb-tune --quiet --yes

# PostgreSQL 재시작
sudo systemctl restart postgresql
```

### Windows (WSL 권장)

WSL2 환경에서 위의 Linux 가이드를 따르거나, Docker 사용 권장.

---

## 🐳 (대안) Docker로 PostgreSQL + TimescaleDB 실행

로컬 설치가 번거로우면 Docker 사용:

```bash
# TimescaleDB 공식 이미지 실행
docker run -d \
  --name korea-stock-db \
  -p 5432:5432 \
  -e POSTGRES_PASSWORD=your_password \
  -e POSTGRES_DB=korea_stock_data \
  -v pgdata:/var/lib/postgresql/data \
  timescale/timescaledb:latest-pg15

# 확인
docker ps
```

**.env 파일 설정**:
```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=korea_stock_data
DB_USER=postgres
DB_PASSWORD=your_password
```

---

## 🗄️ 2단계: 데이터베이스 생성

### PostgreSQL에 접속

```bash
# macOS/Linux
psql -U postgres

# Docker 사용시
docker exec -it korea-stock-db psql -U postgres
```

### 데이터베이스 및 사용자 생성

```sql
-- 데이터베이스 생성
CREATE DATABASE korea_stock_data;

-- 사용자 생성 (선택사항, 보안 강화)
CREATE USER stock_admin WITH PASSWORD 'secure_password';
GRANT ALL PRIVILEGES ON DATABASE korea_stock_data TO stock_admin;

-- TimescaleDB 확장 활성화
\c korea_stock_data
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 확인
\dx
-- timescaledb가 리스트에 보이면 성공

-- 종료
\q
```

---

## 🐍 3단계: Python 환경 설정

### Python 버전 확인

```bash
python --version
# Python 3.11.0 이상이어야 함

# 없다면 pyenv 등으로 설치
brew install pyenv
pyenv install 3.11.5
pyenv global 3.11.5
```

### 가상환경 생성

```bash
# 프로젝트 디렉토리로 이동
cd /Users/unanimous0/Dev/Finance_Data/KOREA

# 가상환경 생성
python -m venv venv

# 가상환경 활성화
# macOS/Linux:
source venv/bin/activate

# Windows (WSL):
source venv/bin/activate

# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
```

활성화 확인: 프롬프트 앞에 `(venv)` 표시

### 의존성 설치

```bash
# requirements.txt가 있다면
pip install -r requirements.txt

# 또는 수동으로 설치 (초기)
pip install \
  sqlalchemy==2.0.23 \
  psycopg2-binary==2.9.9 \
  alembic==1.12.1 \
  pydantic==2.5.0 \
  pydantic-settings==2.1.0 \
  python-dotenv==1.0.0 \
  apscheduler==3.10.4 \
  loguru==0.7.2 \
  pandas==2.1.4 \
  requests==2.31.0

# 개발 도구
pip install \
  pytest==7.4.3 \
  black==23.12.1 \
  ruff==0.1.8 \
  mypy==1.7.1

# requirements.txt 생성
pip freeze > requirements.txt
```

---

## ⚙️ 4단계: 환경변수 설정

### .env 파일 생성

```bash
# 템플릿 복사
cp .env.example .env

# 편집기로 열기
nano .env  # 또는 vi, code 등
```

### .env 파일 내용 작성

```env
# Database Configuration
DB_HOST=localhost
DB_PORT=5432
DB_NAME=korea_stock_data
DB_USER=postgres              # 또는 stock_admin
DB_PASSWORD=your_password_here

# Database Pool Settings
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10

# API Keys (아직 없으면 일단 비워두기)
INFOMAX_API_KEY=
INFOMAX_API_SECRET=
INFOMAX_BASE_URL=https://api.infomax.co.kr

# Application Settings
APP_ENV=development
LOG_LEVEL=INFO
TZ=Asia/Seoul

# Scheduler Settings
SCHEDULER_ENABLED=true
DAILY_COLLECTION_TIME=18:00

# Data Collection Settings
COLLECTION_RETRY_COUNT=3
COLLECTION_TIMEOUT_SECONDS=300
BATCH_SIZE=100
```

**중요**: `.env` 파일은 절대 Git에 커밋하지 마세요!

---

## 🏗️ 5단계: 프로젝트 구조 생성 (초기 1회만)

이 단계는 **처음 개발 시작할 때만** 실행. 이미 코드가 있으면 스킵.

```bash
# 프로젝트 디렉토리로 이동
cd /Users/unanimous0/Dev/Finance_Data/KOREA

# 폴더 구조 생성
mkdir -p config database/schema database/migrations
mkdir -p collectors validators etl schedulers
mkdir -p api/routers utils scripts tests
mkdir -p tests/test_collectors tests/test_validators tests/test_etl
mkdir -p notebooks

# __init__.py 파일 생성 (Python 패키지화)
touch config/__init__.py
touch database/__init__.py
touch collectors/__init__.py
touch validators/__init__.py
touch etl/__init__.py
touch schedulers/__init__.py
touch api/__init__.py api/routers/__init__.py
touch utils/__init__.py
touch tests/__init__.py
```

---

## 🗂️ 6단계: 데이터베이스 스키마 초기화

### 스키마 SQL 파일 생성 (초기 1회)

`database/schema/init_schema.sql` 파일을 생성하고 아래 내용 입력:

```sql
-- TimescaleDB 확장 확인
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ==========================================
-- 메타데이터 테이블
-- ==========================================

-- 종목 마스터
CREATE TABLE IF NOT EXISTS stocks (
    stock_code VARCHAR(10) PRIMARY KEY,
    stock_name VARCHAR(100) NOT NULL,
    market VARCHAR(10) NOT NULL,  -- KOSPI, KOSDAQ
    sector_id INTEGER,
    listing_date DATE,
    delisting_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stocks_market ON stocks(market);
CREATE INDEX IF NOT EXISTS idx_stocks_active ON stocks(is_active);

-- 섹터 분류
CREATE TABLE IF NOT EXISTS sectors (
    sector_id SERIAL PRIMARY KEY,
    sector_name VARCHAR(100) NOT NULL,
    sector_code VARCHAR(20),
    parent_sector_id INTEGER REFERENCES sectors(sector_id),
    created_at TIMESTAMP DEFAULT NOW()
);

-- 지수 구성종목
CREATE TABLE IF NOT EXISTS index_components (
    id SERIAL PRIMARY KEY,
    index_name VARCHAR(50) NOT NULL,
    stock_code VARCHAR(10) REFERENCES stocks(stock_code),
    effective_date DATE NOT NULL,
    end_date DATE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(index_name, stock_code, effective_date)
);

CREATE INDEX IF NOT EXISTS idx_index_components_stock ON index_components(stock_code);
CREATE INDEX IF NOT EXISTS idx_index_components_date ON index_components(effective_date);

-- 유동주식
CREATE TABLE IF NOT EXISTS floating_shares (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) REFERENCES stocks(stock_code),
    base_date DATE NOT NULL,
    total_shares BIGINT,
    floating_shares BIGINT,
    floating_ratio DECIMAL(5,2),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(stock_code, base_date)
);

CREATE INDEX IF NOT EXISTS idx_floating_shares_date ON floating_shares(base_date DESC);

-- ETF 포트폴리오
CREATE TABLE IF NOT EXISTS etf_portfolios (
    id SERIAL PRIMARY KEY,
    etf_code VARCHAR(10) REFERENCES stocks(stock_code),
    component_code VARCHAR(10) REFERENCES stocks(stock_code),
    base_date DATE NOT NULL,
    weight DECIMAL(7,4),
    shares BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(etf_code, component_code, base_date)
);

CREATE INDEX IF NOT EXISTS idx_etf_portfolios_etf ON etf_portfolios(etf_code, base_date DESC);

-- ==========================================
-- 시계열 테이블 (TimescaleDB Hypertables)
-- ==========================================

-- 일별 시가총액
CREATE TABLE IF NOT EXISTS market_cap_daily (
    time DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    market_cap BIGINT,
    shares_outstanding BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);

SELECT create_hypertable('market_cap_daily', 'time',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

CREATE INDEX IF NOT EXISTS idx_market_cap_stock ON market_cap_daily(stock_code, time DESC);

-- 일별 OHLCV
CREATE TABLE IF NOT EXISTS ohlcv_daily (
    time DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    open_price INTEGER,
    high_price INTEGER,
    low_price INTEGER,
    close_price INTEGER,
    volume BIGINT,
    trading_value BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);

SELECT create_hypertable('ohlcv_daily', 'time',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_stock ON ohlcv_daily(stock_code, time DESC);

-- 투자자별 수급
CREATE TABLE IF NOT EXISTS investor_trading (
    time DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    investor_type VARCHAR(20) NOT NULL,  -- FOREIGN, INSTITUTION, RETAIL, PENSION
    net_buy_volume BIGINT,
    net_buy_value BIGINT,
    buy_volume BIGINT,
    sell_volume BIGINT,
    buy_value BIGINT,
    sell_value BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);

SELECT create_hypertable('investor_trading', 'time',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

CREATE INDEX IF NOT EXISTS idx_investor_stock ON investor_trading(stock_code, time DESC);
CREATE INDEX IF NOT EXISTS idx_investor_type ON investor_trading(investor_type, time DESC);

-- ==========================================
-- 메타데이터 및 모니터링 테이블
-- ==========================================

-- 데이터 수집 이력
CREATE TABLE IF NOT EXISTS data_collection_logs (
    id SERIAL PRIMARY KEY,
    data_type VARCHAR(50) NOT NULL,
    collection_date DATE NOT NULL,
    source VARCHAR(50),
    status VARCHAR(20),
    records_count INTEGER,
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_collection_logs_date ON data_collection_logs(collection_date DESC);
CREATE INDEX IF NOT EXISTS idx_collection_logs_type ON data_collection_logs(data_type, status);

-- 데이터 품질 체크
CREATE TABLE IF NOT EXISTS data_quality_checks (
    id SERIAL PRIMARY KEY,
    table_name VARCHAR(50) NOT NULL,
    check_date DATE NOT NULL,
    check_type VARCHAR(50),
    issue_count INTEGER,
    details JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### 스키마 적용

```bash
# SQL 파일 실행
psql -U postgres -d korea_stock_data -f database/schema/init_schema.sql

# 확인
psql -U postgres -d korea_stock_data -c "\dt"
# 테이블 리스트가 보이면 성공
```

---

## ✅ 7단계: 설정 확인

### DB 연결 테스트

간단한 Python 스크립트로 확인:

```bash
# 테스트 스크립트 실행
python -c "
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()

db_url = f\"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}\"
engine = create_engine(db_url)

with engine.connect() as conn:
    result = conn.execute(text('SELECT version();'))
    print('✅ DB 연결 성공!')
    print(result.fetchone()[0])
"
```

성공하면 PostgreSQL 버전 정보가 출력됩니다.

---

## 🔄 8단계: Git 설정 (선택사항)

### Git 초기화

```bash
# Git 초기화 (아직 안했다면)
git init

# .gitignore 확인
cat .gitignore  # .env가 포함되어 있는지 확인

# 첫 커밋
git add .
git commit -m "Initial project setup"

# 원격 저장소 연결 (GitHub 등)
git remote add origin <your-repo-url>
git push -u origin main
```

---

## 🎉 완료!

환경 설정이 끝났습니다. 이제 개발을 시작할 수 있습니다.

### 다음 단계

1. **TODO.md** 확인하여 다음 작업 파악
2. **PROJECT_MASTER.md**에서 현재 Phase 확인
3. 코드 개발 시작

---

## 🐛 문제 해결 (Troubleshooting)

### PostgreSQL 연결 실패

**증상**: `psycopg2.OperationalError: could not connect to server`

**해결**:
```bash
# PostgreSQL 서비스 확인
brew services list  # macOS
sudo systemctl status postgresql  # Linux

# 재시작
brew services restart postgresql@15  # macOS
sudo systemctl restart postgresql  # Linux

# 포트 확인
lsof -i :5432  # 5432 포트가 열려있는지
```

### TimescaleDB 확장 오류

**증상**: `CREATE EXTENSION timescaledb` 실패

**해결**:
```bash
# postgresql.conf 편집
# macOS
nano /opt/homebrew/var/postgresql@15/postgresql.conf

# shared_preload_libraries 항목 찾아서:
shared_preload_libraries = 'timescaledb'

# PostgreSQL 재시작
brew services restart postgresql@15
```

### Python 가상환경 활성화 안됨

**증상**: `(venv)` 표시가 없음

**해결**:
```bash
# 가상환경 재생성
rm -rf venv
python -m venv venv
source venv/bin/activate

# 또는 절대 경로로
source /Users/unanimous0/Dev/Finance_Data/KOREA/venv/bin/activate
```

### 의존성 충돌

**증상**: `pip install` 실패

**해결**:
```bash
# pip 업그레이드
pip install --upgrade pip

# 개별 설치
pip install sqlalchemy
pip install psycopg2-binary
# ...

# 캐시 삭제 후 재시도
pip cache purge
pip install -r requirements.txt
```

---

## 📞 추가 도움

막히면:
1. **PROJECT_MASTER.md** "알려진 이슈" 섹션 확인
2. **DEVELOPMENT_LOG.md**에서 비슷한 문제 있었는지 확인
3. 구글 검색: "TimescaleDB <error message>"
