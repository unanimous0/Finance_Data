# 한국 주식시장 데이터 중앙 관리 시스템 - 프로젝트 분석 및 제안

## 📋 프로젝트 개요

**목적**: 한국 주식시장 데이터를 중앙집중식으로 수집, 저장, 관리하여 여러 프로젝트(수급 분석, 실전 매매, LP/MM 시스템)에서 공통으로 활용할 수 있는 데이터 인프라 구축

**현재 문제점**:
- 각 프로젝트마다 개별 DB 관리로 인한 중복 작업
- 데이터 정합성 문제 발생 가능성
- 유지보수 복잡도 증가

---

## 🎯 핵심 요구사항 분석

### 데이터 특성 분석

| 데이터 유형 | 업데이트 주기 | 데이터 크기 | 쿼리 패턴 |
|------------|-------------|-----------|----------|
| 종목 마스터 (코드/명) | 비정기적 | 소형 (~3,000건) | READ 위주 |
| 지수 구성종목 | 비정기적 | 소형 (350건) | READ 위주 |
| 섹터 분류 | 비정기적 | 소형 (~3,000건) | JOIN 빈번 |
| 유동주식수/비율 | 비정기적 | 소형 (~3,000건) | READ/계산 |
| ETF PDF | 주간 | 중형 | READ 위주 |
| 시가총액 | 일별 | 중형 (1일 ~3,000건) | 시계열 분석 |
| OHLCV | 일별→분봉 | **대형** (1일 ~3,000건 → 분봉시 ~50만건/일) | 시계열 분석, 집계 |
| 투자자별 수급 | 일별 | 중형 (1일 ~12,000건) | 시계열 분석, 집계 |

**특성 정리**:
- **시계열 데이터 중심**: 전체 데이터의 80% 이상이 시계열
- **Write-Once, Read-Many**: 과거 데이터는 변경 없음
- **빠른 증가율**: 분봉 전환시 연간 ~1.8억 레코드
- **복잡한 조인**: 종목 마스터, 섹터 등 메타데이터와 자주 결합

---

## 💡 데이터베이스 선택 및 추천

### 추천: **PostgreSQL + TimescaleDB**

#### 이유:
1. **시계열 데이터 최적화**
   - TimescaleDB는 PostgreSQL의 확장으로 시계열 데이터에 특화
   - 자동 파티셔닝 (Hypertable)
   - 시간 기반 쿼리 성능 대폭 향상
   - 데이터 압축 기능 (스토리지 80% 절감 가능)

2. **PostgreSQL의 강력함 유지**
   - 복잡한 JOIN, 트랜잭션 지원
   - JSONB로 유연한 스키마 (ETF PDF 등)
   - 풍부한 인덱스 타입 (B-tree, GiST, GIN 등)
   - 성숙한 생태계 (연동 라이브러리, 툴)

3. **확장성**
   - 수십억 행까지 처리 가능
   - Continuous Aggregates (실시간 집계 뷰)
   - 데이터 보관 정책 자동화

4. **운영 편의성**
   - SQL 표준 준수
   - 우수한 문서화
   - 무료 오픈소스

#### 대안 비교:

| DB | 장점 | 단점 | 적합도 |
|----|------|------|--------|
| **PostgreSQL + TimescaleDB** | 시계열 최적화, 안정성, 확장성 | 초대규모엔 ClickHouse보다 느림 | ⭐⭐⭐⭐⭐ |
| MySQL | 익숙함, 간단함 | 시계열 처리 약함, 파티셔닝 제한 | ⭐⭐ |
| ClickHouse | 초고속 분석 쿼리, 압축 | 복잡한 JOIN 약함, 학습 곡선 | ⭐⭐⭐⭐ |
| MongoDB | 스키마 유연성 | 시계열 분석 약함, 트랜잭션 제한 | ⭐⭐ |

**결론**: 현재 요구사항에는 **PostgreSQL + TimescaleDB**가 최적

---

## 🏗️ 시스템 아키텍처 제안

### 전체 구조도

```
┌─────────────────────────────────────────────────────────────┐
│                     데이터 소스                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │인포맥스API│  │증권사HTS │  │웹크롤링  │                  │
│  └──────────┘  └──────────┘  └──────────┘                  │
└───────────┬──────────┬──────────┬──────────────────────────┘
            │          │          │
            ▼          ▼          ▼
┌─────────────────────────────────────────────────────────────┐
│               데이터 수집 레이어 (ETL)                         │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Scheduler (Airflow / Prefect / APScheduler)         │  │
│  │  - 비정기: 종목마스터, 섹터, 지수구성                   │  │
│  │  - 주간: ETF PDF                                      │  │
│  │  - 일별: OHLCV, 수급, 시가총액                         │  │
│  │  - 향후 분봉: 실시간 수집기                            │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Data Validators (검증 레이어)                         │  │
│  │  - 데이터 무결성 체크 (NULL, 중복, 이상치)              │  │
│  │  - 스키마 검증                                         │  │
│  │  - 비즈니스 규칙 검증 (가격 범위, 거래량 등)             │  │
│  └──────────────────────────────────────────────────────┘  │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│               스테이징 데이터베이스 (Staging)                   │
│  - 원본 데이터 임시 저장                                       │
│  - 검증 전 버퍼 역할                                          │
└───────────────────────┬─────────────────────────────────────┘
                        │ (검증 통과)
                        ▼
┌─────────────────────────────────────────────────────────────┐
│          프로덕션 데이터베이스 (PostgreSQL + TimescaleDB)       │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  메타데이터 테이블 (일반 PostgreSQL)                    │  │
│  │  - stocks (종목 마스터)                                │  │
│  │  - sectors (섹터 분류)                                 │  │
│  │  - index_components (지수 구성종목)                     │  │
│  │  - floating_shares (유동주식)                          │  │
│  │  - etf_portfolios (ETF 구성)                           │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  시계열 테이블 (TimescaleDB Hypertables)                │  │
│  │  - market_cap_daily (일별 시가총액)                     │  │
│  │  - ohlcv_daily (일봉) → ohlcv_minute (분봉)            │  │
│  │  - investor_trading (투자자별 수급)                     │  │
│  └──────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Continuous Aggregates (사전 집계 뷰)                   │  │
│  │  - 주간/월간 집계                                       │  │
│  │  - 투자자별 누적 수급                                   │  │
│  └──────────────────────────────────────────────────────┘  │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                  캐싱 레이어 (Redis - 선택사항)                │
│  - 자주 조회되는 종목 마스터 캐싱                              │
│  - 최근 N일 데이터 캐싱                                       │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                   API / 인터페이스 레이어                       │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
│  │  REST API    │  │ Python 라이브러리 │  │ Direct DB Access│  │
│  │  (FastAPI)   │  │  (SQLAlchemy) │  │  (Read-Only)    │  │
│  └──────────────┘  └──────────────┘  └─────────────────┘  │
└───────────┬──────────┬──────────┬──────────────────────────┘
            │          │          │
            ▼          ▼          ▼
┌─────────────────────────────────────────────────────────────┐
│                      클라이언트 프로젝트                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │수급분석   │  │실전매매   │  │ LP/MM    │                  │
│  └──────────┘  └──────────┘  └──────────┘                  │
└─────────────────────────────────────────────────────────────┘
```

### 레이어별 역할

1. **데이터 수집 레이어**
   - 다양한 소스에서 데이터 수집
   - 스케줄링 및 재시도 로직
   - 에러 핸들링 및 알림

2. **스테이징 DB**
   - 검증 전 임시 저장
   - 롤백 가능성 확보
   - 프로덕션 DB 보호

3. **프로덕션 DB**
   - 검증된 데이터만 저장
   - 최적화된 스키마 및 인덱스
   - 백업 및 복구 대상

4. **캐싱 레이어 (선택)**
   - 읽기 성능 향상
   - DB 부하 감소
   - 초기엔 불필요, 트래픽 증가시 도입

5. **API/인터페이스**
   - 표준화된 데이터 접근
   - 권한 관리
   - 사용 추적

---

## 📊 데이터베이스 스키마 설계 (초안)

### 1. 메타데이터 테이블 (일반 PostgreSQL)

```sql
-- 종목 마스터
CREATE TABLE stocks (
    stock_code VARCHAR(10) PRIMARY KEY,
    stock_name VARCHAR(100) NOT NULL,
    market VARCHAR(10) NOT NULL,  -- KOSPI, KOSDAQ
    sector_id INTEGER REFERENCES sectors(sector_id),
    listing_date DATE,
    delisting_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_stocks_market ON stocks(market);
CREATE INDEX idx_stocks_sector ON stocks(sector_id);

-- 섹터 분류
CREATE TABLE sectors (
    sector_id SERIAL PRIMARY KEY,
    sector_name VARCHAR(100) NOT NULL,
    sector_code VARCHAR(20),
    parent_sector_id INTEGER REFERENCES sectors(sector_id),
    created_at TIMESTAMP DEFAULT NOW()
);

-- 지수 구성종목
CREATE TABLE index_components (
    id SERIAL PRIMARY KEY,
    index_name VARCHAR(50) NOT NULL,  -- KOSPI200, KOSDAQ150
    stock_code VARCHAR(10) REFERENCES stocks(stock_code),
    effective_date DATE NOT NULL,
    end_date DATE,  -- NULL이면 현재 편입 중
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(index_name, stock_code, effective_date)
);
CREATE INDEX idx_index_components_stock ON index_components(stock_code);
CREATE INDEX idx_index_components_date ON index_components(effective_date);

-- 유동주식
CREATE TABLE floating_shares (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) REFERENCES stocks(stock_code),
    base_date DATE NOT NULL,
    total_shares BIGINT,
    floating_shares BIGINT,
    floating_ratio DECIMAL(5,2),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(stock_code, base_date)
);
CREATE INDEX idx_floating_shares_date ON floating_shares(base_date DESC);

-- ETF 포트폴리오
CREATE TABLE etf_portfolios (
    id SERIAL PRIMARY KEY,
    etf_code VARCHAR(10) REFERENCES stocks(stock_code),
    component_code VARCHAR(10) REFERENCES stocks(stock_code),
    base_date DATE NOT NULL,
    weight DECIMAL(7,4),  -- 비중 (%)
    shares BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(etf_code, component_code, base_date)
);
CREATE INDEX idx_etf_portfolios_etf ON etf_portfolios(etf_code, base_date DESC);
```

### 2. 시계열 테이블 (TimescaleDB Hypertables)

```sql
-- 일별 시가총액
CREATE TABLE market_cap_daily (
    time DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    market_cap BIGINT,  -- 시가총액 (원)
    shares_outstanding BIGINT,  -- 상장주식수
    created_at TIMESTAMP DEFAULT NOW()
);
SELECT create_hypertable('market_cap_daily', 'time');
CREATE INDEX idx_market_cap_stock ON market_cap_daily(stock_code, time DESC);

-- 일별 OHLCV (향후 분봉으로 확장)
CREATE TABLE ohlcv_daily (
    time DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    open_price INTEGER,
    high_price INTEGER,
    low_price INTEGER,
    close_price INTEGER,
    volume BIGINT,
    trading_value BIGINT,  -- 거래대금
    created_at TIMESTAMP DEFAULT NOW()
);
SELECT create_hypertable('ohlcv_daily', 'time');
CREATE INDEX idx_ohlcv_stock ON ohlcv_daily(stock_code, time DESC);

-- 분봉 OHLCV (향후 추가)
CREATE TABLE ohlcv_minute (
    time TIMESTAMPTZ NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    open_price INTEGER,
    high_price INTEGER,
    low_price INTEGER,
    close_price INTEGER,
    volume BIGINT,
    trading_value BIGINT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
-- 파티셔닝: 1일 단위
SELECT create_hypertable('ohlcv_minute', 'time', chunk_time_interval => INTERVAL '1 day');
CREATE INDEX idx_ohlcv_minute_stock ON ohlcv_minute(stock_code, time DESC);

-- 투자자별 수급
CREATE TABLE investor_trading (
    time DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    investor_type VARCHAR(20) NOT NULL,  -- FOREIGN, INSTITUTION, RETAIL, PENSION
    net_buy_volume BIGINT,  -- 순매수 수량
    net_buy_value BIGINT,   -- 순매수 금액
    buy_volume BIGINT,      -- 매수 수량
    sell_volume BIGINT,     -- 매도 수량
    buy_value BIGINT,       -- 매수 금액
    sell_value BIGINT,      -- 매도 금액
    created_at TIMESTAMP DEFAULT NOW()
);
SELECT create_hypertable('investor_trading', 'time');
CREATE INDEX idx_investor_stock ON investor_trading(stock_code, time DESC);
CREATE INDEX idx_investor_type ON investor_trading(investor_type, time DESC);

-- Continuous Aggregate: 주간 집계
CREATE MATERIALIZED VIEW investor_trading_weekly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('7 days', time) AS week,
    stock_code,
    investor_type,
    SUM(net_buy_volume) AS net_buy_volume_weekly,
    SUM(net_buy_value) AS net_buy_value_weekly
FROM investor_trading
GROUP BY week, stock_code, investor_type;
```

### 3. 메타데이터 및 모니터링 테이블

```sql
-- 데이터 수집 이력
CREATE TABLE data_collection_logs (
    id SERIAL PRIMARY KEY,
    data_type VARCHAR(50) NOT NULL,  -- OHLCV, INVESTOR, MARKET_CAP 등
    collection_date DATE NOT NULL,
    source VARCHAR(50),  -- INFOMAX, HTS, CRAWLING
    status VARCHAR(20),  -- SUCCESS, FAILED, PARTIAL
    records_count INTEGER,
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_collection_logs_date ON data_collection_logs(collection_date DESC);
CREATE INDEX idx_collection_logs_type ON data_collection_logs(data_type, status);

-- 데이터 품질 모니터링
CREATE TABLE data_quality_checks (
    id SERIAL PRIMARY KEY,
    table_name VARCHAR(50) NOT NULL,
    check_date DATE NOT NULL,
    check_type VARCHAR(50),  -- NULL_CHECK, DUPLICATE_CHECK, RANGE_CHECK
    issue_count INTEGER,
    details JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 🔧 기술 스택 제안

### 핵심 스택

| 레이어 | 기술 | 이유 |
|--------|------|------|
| **데이터베이스** | PostgreSQL 15+ + TimescaleDB 2.11+ | 시계열 최적화, 안정성 |
| **언어** | Python 3.11+ | 데이터 처리, 풍부한 라이브러리 |
| **ORM/DB 라이브러리** | SQLAlchemy 2.0+ | 강력한 ORM, Raw SQL 지원 |
| **스케줄러** | APScheduler (초기) → Airflow (확장) | 가벼움 → 강력함 |
| **데이터 검증** | Pydantic, Great Expectations | 스키마 검증, 데이터 품질 |
| **API (선택)** | FastAPI | 고성능, 자동 문서화 |
| **로깅** | structlog, Loguru | 구조화된 로깅 |
| **모니터링** | Prometheus + Grafana (향후) | 시계열 메트릭 수집 |

### 개발 환경

```
- Python 가상환경: Poetry 또는 venv
- 코드 포맷: Black, isort
- 린팅: Ruff (빠른 린터)
- 타입 체크: mypy
- 테스팅: pytest
- 버전 관리: Git
- 설정 관리: pydantic-settings (.env 파일)
```

---

## 📁 프로젝트 구조 제안

```
KOREA/
├── README.md
├── pyproject.toml (또는 requirements.txt)
├── .env.example
├── .gitignore
│
├── config/
│   ├── __init__.py
│   ├── settings.py          # 설정 관리 (DB 연결, API 키 등)
│   └── logging_config.py    # 로깅 설정
│
├── database/
│   ├── __init__.py
│   ├── connection.py        # DB 연결 풀 관리
│   ├── models.py            # SQLAlchemy 모델
│   ├── migrations/          # Alembic 마이그레이션
│   └── schema/
│       ├── init_schema.sql  # 초기 스키마
│       └── indexes.sql      # 인덱스 정의
│
├── collectors/              # 데이터 수집기
│   ├── __init__.py
│   ├── base.py             # 베이스 Collector 클래스
│   ├── infomax.py          # 인포맥스 API 수집기
│   ├── hts.py              # 증권사 HTS 수집기
│   ├── crawler.py          # 웹 크롤러
│   └── utils.py
│
├── validators/              # 데이터 검증
│   ├── __init__.py
│   ├── schemas.py          # Pydantic 스키마
│   ├── quality_checks.py   # 데이터 품질 체크
│   └── business_rules.py   # 비즈니스 규칙 검증
│
├── etl/                     # ETL 파이프라인
│   ├── __init__.py
│   ├── extract.py          # 데이터 추출
│   ├── transform.py        # 데이터 변환
│   ├── load.py             # 데이터 적재
│   └── pipeline.py         # 전체 파이프라인 오케스트레이션
│
├── schedulers/              # 스케줄링
│   ├── __init__.py
│   ├── jobs.py             # 스케줄 작업 정의
│   └── scheduler.py        # 스케줄러 실행
│
├── api/                     # API 레이어 (선택사항)
│   ├── __init__.py
│   ├── main.py             # FastAPI 앱
│   ├── routers/
│   │   ├── stocks.py
│   │   ├── market_data.py
│   │   └── investor_trading.py
│   └── dependencies.py
│
├── utils/
│   ├── __init__.py
│   ├── logger.py
│   ├── exceptions.py
│   └── helpers.py
│
├── scripts/                 # 유틸리티 스크립트
│   ├── init_database.py    # DB 초기화
│   ├── backfill_data.py    # 과거 데이터 채우기
│   └── data_quality_report.py
│
├── tests/
│   ├── __init__.py
│   ├── test_collectors/
│   ├── test_validators/
│   └── test_etl/
│
└── notebooks/               # 데이터 분석용 Jupyter 노트북
    └── exploration.ipynb
```

---

## 🚀 개발 로드맵 제안

### Phase 1: 기반 구축 (1-2주)
- [x] 프로젝트 구조 설정
- [ ] PostgreSQL + TimescaleDB 설치 및 설정
- [ ] 기본 스키마 생성 (종목 마스터, 일봉, 수급)
- [ ] DB 연결 모듈 개발
- [ ] 로깅 시스템 구축
- [ ] 설정 관리 시스템 구축

### Phase 2: 데이터 수집기 개발 (2-3주)
- [ ] 인포맥스 API 연동 (종목 마스터, 일봉, 수급)
- [ ] 데이터 검증 로직 개발
- [ ] 스테이징 → 프로덕션 로드 파이프라인
- [ ] 에러 핸들링 및 재시도 로직
- [ ] 단위 테스트 작성

### Phase 3: 스케줄링 및 자동화 (1주)
- [ ] APScheduler 설정
- [ ] 일별 수집 작업 자동화
- [ ] 비정기 데이터 업데이트 스크립트
- [ ] 데이터 수집 모니터링 대시보드 (간단한 버전)

### Phase 4: 데이터 품질 및 백업 (1주)
- [ ] 데이터 품질 체크 자동화
- [ ] 이상 탐지 알림 시스템
- [ ] 백업 전략 구현
- [ ] 과거 데이터 백필 스크립트

### Phase 5: 인터페이스 개발 (1-2주)
- [ ] Python 라이브러리 개발 (다른 프로젝트용)
- [ ] FastAPI 기본 엔드포인트 (선택)
- [ ] Read-only 사용자 생성
- [ ] 사용 가이드 문서

### Phase 6: 확장 및 최적화 (진행 중)
- [ ] 분봉 데이터 수집 준비
- [ ] 증권사 HTS API 연동
- [ ] 웹 크롤링 추가
- [ ] 캐싱 레이어 도입
- [ ] 성능 최적화 (인덱스 튜닝, 쿼리 최적화)
- [ ] Airflow 마이그레이션 (필요시)

---

## ⚠️ 주의사항 및 개선 제안

### 1. 데이터 라이선스 및 법적 이슈
- **인포맥스 API**: 재배포 금지 조항 확인 필요
- **웹 크롤링**: robots.txt 준수, 법적 문제 없는지 확인
- **데이터 저장**: 약관상 저장 기간 제한 확인

### 2. 보안
- **API 키 관리**: 환경 변수 또는 비밀 관리 도구 사용 (.env 파일은 .gitignore)
- **DB 접근 권한**: Read-only 사용자 분리
- **네트워크**: DB는 외부 접근 차단, VPN 또는 SSH 터널

### 3. 성능 고려사항
- **분봉 전환시**: 1일 ~50만 레코드 → 파티셔닝 필수
  - TimescaleDB는 자동 파티셔닝 지원하므로 큰 문제 없음
  - 압축 정책 설정 (오래된 데이터 압축)
- **인덱스 전략**:
  - (stock_code, time DESC) 복합 인덱스 필수
  - 자주 필터링하는 컬럼(investor_type, market) 인덱스
- **쿼리 최적화**:
  - 대량 조회시 배치 처리
  - Continuous Aggregates 활용

### 4. 데이터 정합성
- **중복 방지**: UNIQUE 제약조건 활용
- **결측치 처리**:
  - 공휴일, 시장 휴장일 구분
  - 상장폐지 종목 처리 (soft delete)
- **연기금 데이터**: 기관 합계에서 연기금 중복 제거 로직 필요
  ```
  기관(순수) = 기관(전체) - 연기금
  ```

### 5. 모니터링 및 알림
- **필수 모니터링 항목**:
  - 데이터 수집 실패/지연
  - 데이터 품질 이슈 (NULL 비율, 이상치)
  - DB 디스크 사용량
  - 쿼리 성능 (slow query log)
- **알림 채널**: 이메일, Slack, Discord 등

### 6. 백업 전략
- **PostgreSQL 백업**:
  - 일별 풀 백업 (pg_dump)
  - WAL 아카이빙 (PITR - Point-In-Time Recovery)
  - 최소 7일치 백업 보관
- **재해 복구 계획**: 복구 시간 목표(RTO), 복구 시점 목표(RPO) 설정

### 7. 확장성 대비
- **수평 확장**: TimescaleDB는 분산 하이퍼테이블 지원 (필요시)
- **읽기 부하 분산**: Read Replica 구성
- **아카이빙**: 오래된 데이터(예: 5년 이상) 별도 저장소 이관

---

## 💰 비용 추정 (인프라)

### 초기 단계 (일봉 데이터)
- **로컬 서버**: 무료 (개인 컴퓨터)
- **클라우드 (AWS RDS PostgreSQL)**:
  - db.t3.medium (2vCPU, 4GB RAM): ~$70/월
  - 스토리지 100GB: ~$11.5/월
  - **합계**: ~$82/월

### 확장 단계 (분봉 데이터)
- **클라우드 (AWS RDS)**:
  - db.r5.large (2vCPU, 16GB RAM): ~$186/월
  - 스토리지 500GB (SSD): ~$57.5/월
  - **합계**: ~$244/월

**권장**: 초기엔 로컬 서버로 시작, 안정화 후 클라우드 마이그레이션

---

## 📝 추가 제안 사항

### 1. 데이터 버전 관리
- 스키마 변경시 마이그레이션 이력 관리 (Alembic)
- 데이터 수정 이력 추적 (Audit Log)

### 2. 데이터 품질 리포트
- 주간 데이터 품질 리포트 자동 생성
- 결측치, 이상치 현황
- 수집 성공률 대시보드

### 3. 문서화
- API 문서 (FastAPI Swagger)
- 데이터 스키마 문서 (ERD)
- 사용 가이드 (다른 프로젝트 개발자용)

### 4. 커뮤니티 도구 활용
- **DBeaver**: DB 관리 GUI
- **pgAdmin**: PostgreSQL 전용 GUI
- **Metabase/Superset**: 데이터 시각화 (무료)

### 5. 점진적 개선
- 처음부터 완벽하게 만들려 하지 말 것
- MVP(Minimum Viable Product)부터 시작
- 피드백 기반 반복 개선

---

## 🎯 즉시 시작할 수 있는 다음 단계

### 1. 환경 설정
```bash
# PostgreSQL + TimescaleDB 설치 (macOS)
brew install postgresql@15
brew install timescaledb

# Python 가상환경
python -m venv venv
source venv/bin/activate
pip install sqlalchemy psycopg2-binary alembic pydantic python-dotenv
```

### 2. 프로젝트 구조 생성
```bash
mkdir -p config database collectors validators etl schedulers api utils scripts tests
touch config/__init__.py database/__init__.py collectors/__init__.py
```

### 3. 기본 설정 파일
- `.env.example`: DB 연결 정보, API 키 템플릿
- `config/settings.py`: 설정 로드
- `database/connection.py`: DB 연결 풀

### 4. 스키마 생성
- `database/schema/init_schema.sql`: 위의 스키마 적용

---

## 결론

이 프로젝트는 **데이터 중심 아키텍처**로 설계되어야 하며, 다음 원칙을 따르는 것을 권장합니다:

1. **단순함에서 시작**: 과도한 엔지니어링 방지, MVP 우선
2. **확장 가능한 설계**: 분봉, 호가 데이터 등 향후 확장 고려
3. **데이터 품질 우선**: 수집보다 정확성과 정합성이 중요
4. **자동화**: 수동 작업 최소화, 모니터링 및 알림 필수
5. **문서화**: 현재와 미래의 자신을 위한 투자

**PostgreSQL + TimescaleDB** 조합은 현재 요구사항에 최적이며, 수년간 안정적으로 사용할 수 있는 기반이 될 것입니다.

추가 질문이나 특정 부분에 대한 상세 설명이 필요하면 언제든 요청해주세요!
