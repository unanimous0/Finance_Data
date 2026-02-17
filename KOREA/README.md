# 한국 주식시장 데이터 중앙 관리 시스템

여러 주식 트레이딩 프로젝트(수급 분석, 실전 매매, LP/MM 시스템)에서 공통으로 활용할 수 있는 한국 주식시장 데이터 인프라

## 📚 프로젝트 문서

- **[프로젝트 분석 및 제안서](./PROJECT_ANALYSIS.md)**: 상세한 아키텍처, 기술 스택, 로드맵

## 🎯 목적

- 한국 주식시장 데이터의 중앙집중식 수집 및 관리
- 여러 프로젝트 간 데이터 중복 방지
- 데이터 정합성 및 품질 보장
- 확장 가능한 데이터 인프라 구축

## 📊 관리 데이터

### 메타데이터 (비정기 업데이트)
- 종목 마스터 (종목코드, 종목명)
- 지수 구성종목 (KOSPI 200, KOSDAQ 150)
- 섹터 분류
- 유동주식수/비율
- ETF 포트폴리오

### 시계열 데이터 (일별 업데이트)
- 개별 종목 OHLCV (일봉 → 향후 분봉)
- 시가총액
- 투자자별 수급 (외국인, 기관, 개인, 연기금)

## 🛠️ 기술 스택

- **데이터베이스**: PostgreSQL 15+ + TimescaleDB 2.11+
- **언어**: Python 3.11+
- **주요 라이브러리**: SQLAlchemy, Pydantic, APScheduler
- **데이터 소스**: 인포맥스 API, 증권사 HTS, 웹 크롤링

## 🚀 빠른 시작

### 1. 사전 요구사항

```bash
# PostgreSQL + TimescaleDB 설치 (macOS)
brew install postgresql@15 timescaledb

# PostgreSQL 시작
brew services start postgresql@15

# TimescaleDB 확장 활성화
psql -d postgres -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
```

### 2. 프로젝트 설정

```bash
# 저장소 클론 (또는 현재 디렉토리 사용)
cd /Users/unanimous0/Dev/Finance_Data/KOREA

# Python 가상환경 생성 및 활성화
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt

# 환경 변수 설정
cp .env.example .env
# .env 파일을 열어 DB 연결 정보, API 키 입력
```

### 3. 데이터베이스 초기화

```bash
# 데이터베이스 생성
createdb korea_stock_data

# 스키마 생성
python scripts/init_database.py
```

### 4. 데이터 수집 시작

```bash
# 종목 마스터 수집
python scripts/collect_stocks.py

# 일별 데이터 수집 (스케줄러 시작)
python schedulers/scheduler.py
```

## 📁 프로젝트 구조

```
KOREA/
├── config/              # 설정 관리
├── database/            # DB 모델, 스키마, 마이그레이션
├── collectors/          # 데이터 수집기 (인포맥스, HTS, 크롤러)
├── validators/          # 데이터 검증
├── etl/                 # ETL 파이프라인
├── schedulers/          # 스케줄링
├── api/                 # FastAPI (선택사항)
├── utils/               # 유틸리티
├── scripts/             # 스크립트
└── tests/               # 테스트
```

## 🔍 사용 예시

### Python 라이브러리로 사용

```python
from database.connection import get_session
from database.models import Stock, OHLCVDaily, InvestorTrading
from sqlalchemy import select

# 특정 종목의 최근 30일 종가 조회
with get_session() as session:
    stmt = select(OHLCVDaily).where(
        OHLCVDaily.stock_code == "005930"
    ).order_by(OHLCVDaily.time.desc()).limit(30)

    result = session.execute(stmt).scalars().all()
    for row in result:
        print(f"{row.time}: {row.close_price}원")

# 외국인 순매수 상위 종목
with get_session() as session:
    stmt = select(InvestorTrading).where(
        InvestorTrading.time == "2026-02-17",
        InvestorTrading.investor_type == "FOREIGN"
    ).order_by(InvestorTrading.net_buy_value.desc()).limit(10)

    result = session.execute(stmt).scalars().all()
    for row in result:
        print(f"{row.stock_code}: {row.net_buy_value:,}원")
```

## 📅 개발 로드맵

- [x] **Phase 1**: 기반 구축 (프로젝트 구조, DB 설정)
- [ ] **Phase 2**: 데이터 수집기 개발 (인포맥스 API)
- [ ] **Phase 3**: 스케줄링 및 자동화
- [ ] **Phase 4**: 데이터 품질 관리
- [ ] **Phase 5**: API 인터페이스 개발
- [ ] **Phase 6**: 분봉 데이터 확장

## ⚙️ 설정

`.env` 파일에서 다음 항목을 설정하세요:

```env
# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=korea_stock_data
DB_USER=your_username
DB_PASSWORD=your_password

# API Keys
INFOMAX_API_KEY=your_infomax_key
INFOMAX_API_SECRET=your_infomax_secret

# Logging
LOG_LEVEL=INFO
```

## 📊 모니터링

데이터 수집 현황 및 품질은 다음 방법으로 모니터링:

```bash
# 데이터 수집 로그 조회
python scripts/check_collection_status.py

# 데이터 품질 리포트 생성
python scripts/data_quality_report.py
```

## 🤝 다른 프로젝트와 연동

이 프로젝트의 DB를 다른 프로젝트에서 사용하는 방법:

1. **Direct DB Access** (권장: Read-only)
2. **Python 라이브러리**: 이 프로젝트를 패키지로 설치
3. **REST API**: FastAPI 엔드포인트 활용

## 📝 라이선스 및 주의사항

- 인포맥스 API 데이터는 재배포 금지 (약관 확인 필요)
- 웹 크롤링은 robots.txt 준수
- 이 프로젝트는 개인 투자 연구 목적

## 🐛 이슈 및 개선

이슈나 개선 사항은 GitHub Issues에 등록해주세요.

## 📞 연락

프로젝트 관련 문의: [이메일 또는 연락처]
