# 🛠️ 환경 설정 및 데이터 수집 가이드

> **목적**: macOS/Windows 양쪽 환경에서 프로젝트 설정 및 데이터 수집 방법
>
> **참고**: 두 환경 모두 동일한 개발 가능, 인포맥스 API는 Windows에서만 접근

---

## 📋 목차

1. [macOS 환경 설정](#1-macos-환경-설정)
2. [Windows 환경 설정](#2-windows-환경-설정)
3. [데이터 수집 가이드](#3-데이터-수집-가이드-windows-전용)
4. [트러블슈팅](#4-트러블슈팅)

---

## 1. macOS 환경 설정

### 1-1. Homebrew 설치 (없는 경우)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 1-2. PostgreSQL + TimescaleDB 설치

```bash
# PostgreSQL 17 설치
brew install postgresql@17

# TimescaleDB 설치
brew install timescaledb

# PostgreSQL PATH 추가
echo 'export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# 확인
postgres --version  # PostgreSQL 17.x
```

### 1-3. PostgreSQL 설정 및 시작

```bash
# postgresql.conf 수정
code /opt/homebrew/var/postgresql@17/postgresql.conf
# 또는
nano /opt/homebrew/var/postgresql@17/postgresql.conf

# 다음 줄 추가/수정:
# shared_preload_libraries = 'timescaledb'

# PostgreSQL 시작
brew services start postgresql@17

# 연결 확인
psql postgres
```

### 1-4. TimescaleDB 튜닝

```bash
timescaledb-tune --quiet --yes

# PostgreSQL 재시작
brew services restart postgresql@17
```

### 1-5. 데이터베이스 생성

```bash
# psql 접속
psql postgres

# DB 생성
CREATE DATABASE korea_stock_data
    ENCODING 'UTF8'
    LC_COLLATE = 'C'
    LC_CTYPE = 'C';

# DB 연결
\c korea_stock_data

# TimescaleDB 확장 활성화
CREATE EXTENSION IF NOT EXISTS timescaledb;

# 확인
SELECT default_version, installed_version
FROM pg_available_extensions
WHERE name = 'timescaledb';

# 종료
\q
```

### 1-6. 프로젝트 클론 및 Python 환경

```bash
# 프로젝트 클론
cd ~/Dev  # 원하는 경로
git clone https://github.com/unanimous0/Finance_Data.git
cd Finance_Data/KOREA

# Python 가상환경 생성
python3 -m venv venv

# 가상환경 활성화
source venv/bin/activate

# 패키지 설치
pip install --upgrade pip
pip install -r requirements.txt
```

### 1-7. 환경변수 설정

```bash
# .env 파일 생성
cp .env.example .env

# .env 파일 수정
code .env  # 또는 nano .env
```

```.env
# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=korea_stock_data
DB_USER=사용자명
DB_PASSWORD=비밀번호

# Logging
LOG_LEVEL=INFO

# Environment
ENVIRONMENT=development
```

### 1-8. 스키마 적용

```bash
# SQL 파일 실행
psql -d korea_stock_data -f database/schema/init_schema.sql

# 확인
psql -d korea_stock_data -c "\dt"  # 테이블 목록
psql -d korea_stock_data -c "SELECT hypertable_name FROM timescaledb_information.hypertables;"  # Hypertable 확인
```

### 1-9. 동작 확인

```bash
# 가상환경 활성화 상태에서
python database/connection.py  # DB 연결 테스트
python config/settings.py      # 설정 확인
python database/models.py      # ORM 모델 테스트
python validators/schemas.py   # Pydantic 스키마 테스트

# pytest 실행
pytest tests/ -v
```

---

## 2. Windows 환경 설정

### 2-1. Git 설치

**다운로드**: https://git-scm.com/download/win

**설치 옵션**:
- ✅ Use Git from the Windows Command Prompt
- ✅ Checkout as-is, commit Unix-style line endings

**확인**:
```cmd
git --version
```

### 2-2. Python 설치

**다운로드**: https://www.python.org/downloads/ (Python 3.11+)

**⚠️ 중요**: 설치 시 **"Add Python to PATH"** 체크!

**확인**:
```cmd
python --version
```

### 2-3. PostgreSQL 17 설치

**다운로드**: https://www.postgresql.org/download/windows/ (EDB 인스톨러)

**설치 과정**:
1. PostgreSQL 17.x 선택
2. 포트: `5432` (기본값)
3. 슈퍼유저 비밀번호 설정 (기억하기!)
4. Locale: Korean, Korea

**PATH 추가** (명령어 인식 안 될 때):
```
C:\Program Files\PostgreSQL\17\bin
```

### 2-4. TimescaleDB 설치

**다운로드**: https://docs.timescale.com/self-hosted/latest/install/installation-windows/

**설치**:
1. TimescaleDB Windows 인스톨러 다운로드
2. PostgreSQL 17 경로 선택
3. 설치 완료

**설정**:
```cmd
# postgresql.conf 수정
notepad "C:\Program Files\PostgreSQL\17\data\postgresql.conf"

# 다음 줄 추가:
# shared_preload_libraries = 'timescaledb'

# PostgreSQL 재시작
net stop postgresql-x64-17
net start postgresql-x64-17
```

### 2-5. 데이터베이스 생성

```cmd
# psql 접속
psql -U postgres

# DB 생성
CREATE DATABASE korea_stock_data
    ENCODING 'UTF8'
    LC_COLLATE = 'Korean_Korea.949'
    LC_CTYPE = 'Korean_Korea.949';

# DB 연결
\c korea_stock_data

# TimescaleDB 확장
CREATE EXTENSION IF NOT EXISTS timescaledb;

# 확인
SELECT default_version, installed_version
FROM pg_available_extensions
WHERE name = 'timescaledb';

\q
```

### 2-6. 프로젝트 클론 및 Python 환경

```cmd
# 프로젝트 클론
cd C:\Dev
git clone https://github.com/unanimous0/Finance_Data.git
cd Finance_Data\KOREA

# Python 가상환경 생성
python -m venv venv

# 가상환경 활성화 (PowerShell)
venv\Scripts\Activate.ps1

# 가상환경 활성화 (CMD)
venv\Scripts\activate.bat

# 패키지 설치
pip install --upgrade pip
pip install -r requirements.txt
```

**PowerShell 실행 정책 오류 시**:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 2-7. 환경변수 설정

```cmd
# .env 파일 생성
copy .env.example .env

# .env 파일 수정
notepad .env
```

```.env
# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=korea_stock_data
DB_USER=postgres
DB_PASSWORD=실제_비밀번호

# Infomax API (Windows 전용)
INFOMAX_API_KEY=실제_API_키
INFOMAX_API_SECRET=실제_시크릿
INFOMAX_BASE_URL=실제_API_URL

# Logging
LOG_LEVEL=INFO

# Environment
ENVIRONMENT=development
```

### 2-8. 스키마 적용

```cmd
# SQL 파일 실행
psql -U postgres -d korea_stock_data -f database\schema\init_schema.sql

# 확인
psql -U postgres -d korea_stock_data -c "\dt"
```

### 2-9. 동작 확인

```cmd
# 가상환경 활성화 상태에서
python database\connection.py
python config\settings.py
python database\models.py
python validators\schemas.py

# pytest 실행
pytest tests\ -v
```

---

## 3. 데이터 수집 가이드 (Windows 전용)

> 인포맥스 API는 Windows에서만 접근 가능

### 3-1. 데이터 수집 워크플로우

```
[1단계] 소량 테스트 (30분)
   → 10종목 30일치 샘플 수집
   → 데이터 형식 확인
   → Git push
         ↓
[2단계] 스키마 조정 (1-2시간)
   → Git pull (맥/윈도우 어디서든)
   → 필요시 스키마/모델 수정
   → Git push
         ↓
[3단계] 2년치 전체 수집 (3-4시간)
   → Git pull (Windows)
   → 전체 데이터 수집 및 DB 적재
   → 검증
```

### 3-2. 1단계: 소량 테스트 (30분)

#### API 형식 테스트 스크립트

`scripts/test_api_format.py` 생성:

```python
"""
인포맥스 API 데이터 형식 확인용 테스트 스크립트
소량 데이터만 수집해서 실제 형식을 확인합니다.
"""

import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests
from tqdm import tqdm

load_dotenv()

API_KEY = os.getenv("INFOMAX_API_KEY")
BASE_URL = os.getenv("INFOMAX_BASE_URL")

# 테스트용 10개 종목
TEST_STOCKS = [
    "005930", "000660", "035720", "035420", "207940",
    "005380", "051910", "006400", "068270", "028260"
]

def collect_stocks_sample():
    """종목 마스터 샘플 (10건)"""
    print("📊 종목 마스터 샘플 수집 중...")
    # TODO: 실제 API 엔드포인트로 변경
    response = requests.get(f"{BASE_URL}/stocks", headers={"Authorization": f"Bearer {API_KEY}"})
    data = response.json()

    with open("test_stocks_master.json", "w", encoding="utf-8") as f:
        json.dump(data[:10], f, ensure_ascii=False, indent=2)

    print(f"✅ {len(data[:10])}건 저장 완료")
    print(json.dumps(data[0], ensure_ascii=False, indent=2))  # 구조 출력

def collect_ohlcv_sample():
    """OHLCV 샘플 (10종목 × 30일)"""
    print("\n📈 OHLCV 샘플 수집 중...")
    start_date = datetime.now() - timedelta(days=30)
    end_date = datetime.now()

    all_data = []
    for stock_code in tqdm(TEST_STOCKS):
        # TODO: 실제 API 엔드포인트로 변경
        params = {
            "stock_code": stock_code,
            "start_date": start_date.strftime("%Y%m%d"),
            "end_date": end_date.strftime("%Y%m%d")
        }
        response = requests.get(f"{BASE_URL}/ohlcv", headers={"Authorization": f"Bearer {API_KEY}"}, params=params)
        if response.status_code == 200:
            all_data.extend(response.json())

    with open("test_ohlcv_daily.json", "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print(f"✅ {len(all_data)}건 저장 완료")

def collect_investor_sample():
    """투자자별 수급 샘플"""
    # (위와 유사)
    pass

if __name__ == "__main__":
    collect_stocks_sample()
    collect_ohlcv_sample()
    collect_investor_sample()
```

**실행**:
```cmd
cd C:\Dev\Finance_Data\KOREA
venv\Scripts\activate
python scripts\test_api_format.py
```

#### 데이터 형식 비교 문서 작성

생성된 파일 확인:
- `test_stocks_master.json`
- `test_ohlcv_daily.json`
- `test_investor_trading.json`

`DATA_FORMAT_COMPARISON.md` 작성:

```markdown
# 데이터 형식 비교

## 1. 종목 마스터

| 항목 | 인포맥스 실제 | 현재 스키마 | 조치 |
|------|--------------|------------|------|
| 종목코드 컬럼 | (실제 확인) | stock_code | 매핑 필요 여부 |
| 종목명 컬럼 | (실제 확인) | stock_name | 매핑 필요 여부 |
| 시장구분 값 | (실제 확인) | KOSPI/KOSDAQ | 변환 필요 여부 |

## 2. 일봉 OHLCV

| 항목 | 인포맥스 실제 | 현재 스키마 | 조치 |
|------|--------------|------------|------|
| 날짜 형식 | (실제 확인) | DATE | 변환 필요 여부 |
| 가격 타입 | (실제 확인) | INTEGER | 타입 변경 여부 |

## 3. 투자자별 수급

| 항목 | 인포맥스 실제 | 현재 스키마 | 조치 |
|------|--------------|------------|------|
| 투자자 유형 | (실제 확인) | FOREIGN/INSTITUTION/RETAIL/PENSION | 매핑 딕셔너리 |
| 연기금 처리 | (포함/별도) | 별도 | 계산 로직 |
```

**Git push**:
```cmd
git add scripts\test_api_format.py test_*.json DATA_FORMAT_COMPARISON.md
git commit -m "Add API format test data"
git push origin main
```

### 3-3. 2단계: 스키마 조정 (맥/윈도우 어디서든)

```bash
# Git pull
git pull origin main

# 샘플 데이터 확인 후 필요시 수정
# - database/schema/init_schema.sql
# - database/models.py
# - validators/schemas.py
# - etl/transform.py (신규 생성)

# Git push
git add .
git commit -m "Adjust schema based on actual API format"
git push origin main
```

**데이터 변환 로직 예시** (`etl/transform.py`):

```python
# 투자자 유형 매핑
INVESTOR_TYPE_MAP = {
    "FOR": "FOREIGN",
    "INS": "INSTITUTION",
    "RET": "RETAIL",
    "PEN": "PENSION",
}

def transform_investor_type(raw_type: str) -> str:
    return INVESTOR_TYPE_MAP.get(raw_type, raw_type)

# 날짜 형식 변환
def transform_date(date_str: str) -> date:
    if len(date_str) == 8:  # "20260218"
        return datetime.strptime(date_str, "%Y%m%d").date()
    return date_str
```

### 3-4. 3단계: 2년치 전체 수집 (Windows, 3-4시간)

```cmd
# Git pull
git pull origin main
venv\Scripts\activate

# 전체 수집 스크립트 실행
python scripts\collect_historical_data.py
```

**스크립트 예시** (`scripts/collect_historical_data.py`):

```python
"""
2년치 전체 데이터 수집 및 DB 적재
"""

import os
from datetime import datetime, timedelta
from tqdm import tqdm
from database.connection import get_session
from database.models import Stock, OHLCVDaily, InvestorTrading
from validators.schemas import StockSchema, OHLCVDailySchema, InvestorTradingSchema

START_DATE = datetime.now() - timedelta(days=730)
END_DATE = datetime.now()

def collect_and_load_stocks():
    """1. 종목 마스터 수집 및 적재"""
    # API 호출
    response = requests.get(f"{BASE_URL}/stocks", headers={"Authorization": f"Bearer {API_KEY}"})
    stocks_raw = response.json()

    # DB 적재
    with get_session() as session:
        for raw_data in tqdm(stocks_raw):
            stock = StockSchema(**raw_data)  # Pydantic 검증
            db_stock = Stock(**stock.model_dump())
            session.merge(db_stock)
        session.commit()

def collect_and_load_ohlcv():
    """2. OHLCV 2년치 수집 (배치 처리)"""
    with get_session() as session:
        stocks = session.query(Stock).filter_by(is_active=True).all()

        batch = []
        for stock in tqdm(stocks):
            # API 호출
            params = {"stock_code": stock.stock_code, "start_date": START_DATE.strftime("%Y%m%d")}
            response = requests.get(f"{BASE_URL}/ohlcv", params=params)

            for raw_data in response.json():
                ohlcv = OHLCVDailySchema(**raw_data)
                batch.append(OHLCVDaily(**ohlcv.model_dump()))

                # 1000건마다 저장
                if len(batch) >= 1000:
                    session.bulk_save_objects(batch)
                    session.commit()
                    batch = []

        # 남은 배치 저장
        if batch:
            session.bulk_save_objects(batch)
            session.commit()

if __name__ == "__main__":
    collect_and_load_stocks()
    collect_and_load_ohlcv()
    collect_and_load_investor()  # 투자자별 수급
```

### 3-5. 데이터 검증

```cmd
python scripts\verify_data.py
```

```python
# scripts/verify_data.py
from database.connection import get_session
from database.models import Stock, OHLCVDaily, InvestorTrading

with get_session() as session:
    print(f"종목: {session.query(Stock).count():,}건")
    print(f"OHLCV: {session.query(OHLCVDaily).count():,}건")
    print(f"투자자별 수급: {session.query(InvestorTrading).count():,}건")
```

**SQL 쿼리 테스트**:
```sql
-- 삼성전자 최근 10일
SELECT time, close_price FROM ohlcv_daily
WHERE stock_code = '005930'
ORDER BY time DESC LIMIT 10;
```

---

## 4. 트러블슈팅

### macOS

**TimescaleDB 라이브러리 없음**:
```bash
# 확인
brew list | grep timescaledb

# 재설치
brew reinstall timescaledb
```

**PostgreSQL 시작 안 됨**:
```bash
# 로그 확인
tail -f /opt/homebrew/var/log/postgresql@17.log

# 재시작
brew services restart postgresql@17
```

**Python 모듈 없음**:
```bash
# 가상환경 활성화 확인
which python  # venv/bin/python이어야 함

# 재설치
pip install -r requirements.txt
```

### Windows

**PostgreSQL 서비스 시작 안 됨**:
```cmd
# 서비스 확인
sc query postgresql-x64-17

# 로그 확인
type "C:\Program Files\PostgreSQL\17\data\log\postgresql-*.log"

# 재시작
net stop postgresql-x64-17
net start postgresql-x64-17
```

**psql 명령어 인식 안 됨**:
```
시스템 환경 변수 → Path 편집 → 새로 만들기
C:\Program Files\PostgreSQL\17\bin
CMD 재시작
```

**PowerShell 실행 정책**:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 공통

**DB 연결 실패**:
```bash
# .env 파일 확인
cat .env  # (Windows: type .env)

# PostgreSQL 실행 여부 확인
psql postgres -c "SELECT version();"

# 방화벽 확인 (5432 포트)
```

**Git 동기화 문제**:
```bash
# 최신 상태 확인
git status
git pull origin main

# 충돌 해결
git merge --abort  # 취소
# 또는 수동 해결 후
git add .
git commit -m "Resolve merge conflict"
```

---

## ✅ 설정 완료 체크리스트

### macOS
- [ ] PostgreSQL 17 + TimescaleDB 설치
- [ ] DB 생성 및 스키마 적용 (10개 테이블)
- [ ] Python 가상환경 및 패키지 설치
- [ ] .env 파일 설정
- [ ] DB 연결 테스트 성공

### Windows
- [ ] PostgreSQL 17 + TimescaleDB 설치
- [ ] DB 생성 및 스키마 적용
- [ ] Python 가상환경 및 패키지 설치
- [ ] .env 파일 설정 (인포맥스 API 키 포함)
- [ ] DB 연결 테스트 성공

### 데이터 수집 (Windows)
- [ ] 1단계: 소량 테스트 완료
- [ ] 2단계: 스키마 조정 완료
- [ ] 3단계: 2년치 데이터 수집 완료
- [ ] 데이터 검증 완료

---

## 🔄 환경 간 동기화

**맥에서 작업 후**:
```bash
git add .
git commit -m "작업 내용"
git push origin main
```

**윈도우에서 이어서**:
```cmd
git pull origin main
```

**주의사항**:
- `.env` 파일은 Git에 미포함 (각 환경마다 별도 설정)
- `venv/` 폴더도 Git에 미포함 (각 환경마다 별도 생성)

---

**다음 단계**: TODO.md 참조
