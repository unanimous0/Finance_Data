# 🏢 한국 주식시장 데이터 중앙 관리 시스템 - 프로젝트 마스터 문서

> **마지막 업데이트**: 2026-02-17
> **프로젝트 상태**: 초기 설정 단계
> **현재 Phase**: Phase 1 - 기반 구축 (진행 중)

---

## 📌 프로젝트 핵심 요약 (30초 컨텍스트)

**목적**: 한국 주식 데이터를 중앙에서 수집/관리하여 여러 프로젝트(수급 분석, 실전 매매, LP/MM)에서 공유 사용

**핵심 결정사항**:
- ✅ **DB**: PostgreSQL 15+ + TimescaleDB (시계열 최적화)
- ✅ **언어**: Python 3.11+
- ✅ **아키텍처**: 데이터 수집 → 스테이징 → 검증 → 프로덕션 DB
- ✅ **우선순위**: 종목 마스터 → 일봉 OHLCV → 투자자별 수급 (MVP)

**현재 위치**: 프로젝트 구조 및 문서화 완료, 코드 개발 시작 전

---

## 🎯 프로젝트 배경 및 목적

### 왜 이 프로젝트를 시작했나?

**기존 문제**:
1. 수급 분석, 실전 매매, LP/MM 등 **여러 프로젝트를 동시 진행** 중
2. 각 프로젝트마다 **개별 DB를 관리**하다 보니:
   - 데이터 중복 저장
   - 정합성 문제 발생
   - 유지보수 복잡도 증가
3. 한국 주식시장 데이터 관리가 점점 **감당 불가능**한 수준으로 커짐

**솔루션**:
- 데이터 관리를 **독립된 프로젝트로 분리**
- 이 프로젝트가 **단일 진실 공급원(Single Source of Truth)** 역할
- 다른 프로젝트들은 이 DB를 **참조만** 함

### 프로젝트의 가치

1. **일관성**: 모든 프로젝트가 동일한 데이터 사용
2. **효율성**: 데이터 수집/관리 작업 일원화
3. **확장성**: 새 프로젝트 추가시 데이터 인프라 재사용
4. **품질**: 중앙에서 데이터 검증 및 품질 관리

---

## 📊 관리할 데이터 상세

### 1. 메타데이터 (비정기 업데이트)

| 데이터명 | 업데이트 주기 | 레코드 수 | 중요도 | 비고 |
|---------|-------------|----------|--------|------|
| 종목 마스터 | 비정기 (상장/폐지시) | ~3,000건 | 🔴 최고 | 모든 데이터의 기준 |
| 지수 구성종목 | 분기별 | 350건 | 🟡 중간 | KOSPI200, KOSDAQ150 |
| 섹터 분류 | 연간 | ~3,000건 | 🟡 중간 | 업종 분석용 |
| 유동주식수/비율 | 비정기 | ~3,000건 | 🟢 낮음 | 시가총액 계산용 |
| ETF PDF | 주간 | 가변 | 🟢 낮음 | ETF 구성 종목 |

### 2. 시계열 데이터 (정기 업데이트)

| 데이터명 | 업데이트 주기 | 1일 레코드 수 | 연간 누적 | 중요도 | 비고 |
|---------|-------------|-------------|----------|--------|------|
| 시가총액 | 일별 | ~3,000건 | ~75만건 | 🟡 중간 | |
| OHLCV (일봉) | 일별 | ~3,000건 | ~75만건 | 🔴 최고 | 향후 분봉 전환 |
| OHLCV (분봉) | 분단위 | ~50만건 | ~1.2억건 | 🔴 최고 | Phase 6에서 추가 |
| 투자자별 수급 | 일별 | ~12,000건 | ~300만건 | 🔴 최고 | 외국인/기관/개인/연기금 |

**데이터 특성 분석**:
- 전체의 **80% 이상이 시계열 데이터**
- **Write-Once, Read-Many** 패턴 (과거 데이터는 변경 없음)
- 분봉 전환시 **연간 ~1.8억 레코드** 예상
- 복잡한 JOIN 쿼리 빈번 (종목 마스터 + 시계열 데이터)

---

## 🏗️ 시스템 아키텍처

### 전체 데이터 플로우

```
[데이터 소스]
    ├─ 인포맥스 API (유료) ─────────┐
    ├─ 증권사 HTS API ──────────────┤
    └─ 웹 크롤링 ────────────────────┤
                                    ▼
            [데이터 수집 레이어 - Collectors]
            - API 호출, 에러 핸들링, 재시도
            - 스케줄러 (APScheduler)
                    ▼
            [데이터 검증 레이어 - Validators]
            - 스키마 검증 (Pydantic)
            - 비즈니스 규칙 체크
            - 이상치 탐지
                    ▼
            [스테이징 DB - Staging]
            - 임시 저장 (검증 전)
            - 롤백 가능
                    ▼ (검증 통과)
    [프로덕션 DB - PostgreSQL + TimescaleDB]
    ├─ 메타데이터 테이블 (일반 PostgreSQL)
    │   ├─ stocks (종목 마스터)
    │   ├─ sectors (섹터)
    │   ├─ index_components (지수 구성)
    │   ├─ floating_shares (유동주식)
    │   └─ etf_portfolios (ETF)
    │
    └─ 시계열 테이블 (TimescaleDB Hypertables)
        ├─ market_cap_daily (시가총액)
        ├─ ohlcv_daily (일봉)
        ├─ ohlcv_minute (분봉 - 향후)
        └─ investor_trading (투자자별 수급)
                    ▼
            [API / 인터페이스 레이어]
            ├─ FastAPI (REST API)
            ├─ Python 라이브러리 (SQLAlchemy)
            └─ Direct DB Access (Read-Only)
                    ▼
            [클라이언트 프로젝트들]
            ├─ 수급 분석
            ├─ 실전 매매
            └─ LP/MM 시스템
```

### 핵심 설계 원칙

1. **계층 분리**: 수집 → 검증 → 저장 → 제공 (각 단계 독립)
2. **실패 격리**: 스테이징 DB로 프로덕션 보호
3. **확장 가능**: 새 데이터 소스 추가 용이
4. **자동화 우선**: 수동 개입 최소화

---

## 🛠️ 기술 스택 및 선택 이유

### 데이터베이스: PostgreSQL + TimescaleDB

**왜 이 조합인가?**
1. **시계열 데이터 최적화**
   - TimescaleDB = PostgreSQL 확장 (호환성 100%)
   - 자동 파티셔닝 (Hypertable)
   - 압축으로 스토리지 80% 절감
   - Continuous Aggregates (실시간 집계 뷰)

2. **PostgreSQL의 강력함 유지**
   - 복잡한 JOIN, 트랜잭션 완벽 지원
   - JSONB로 유연한 스키마 (ETF 데이터 등)
   - 성숙한 생태계, 풍부한 도구

3. **확장성**
   - 수십억 행 처리 가능
   - 분봉 전환해도 성능 문제 없음

**다른 후보들**:
- ❌ **SQLite**: 동시성, 확장성 한계 (프로젝트 규모에 부적합)
- ⚠️ **ClickHouse**: 초고속 분석은 장점이나 복잡한 JOIN 약함, 학습 곡선
- ⚠️ **MySQL**: 시계열 처리 약함, 파티셔닝 제한

### 언어 및 프레임워크

| 컴포넌트 | 기술 | 버전 | 선택 이유 |
|---------|------|------|----------|
| 언어 | Python | 3.11+ | 데이터 처리 생태계, 증권사 API 라이브러리 |
| ORM | SQLAlchemy | 2.0+ | 강력한 ORM, Raw SQL 지원 |
| 검증 | Pydantic | 2.0+ | 타입 안전성, 자동 검증 |
| 스케줄러 | APScheduler | 3.10+ | 가볍고 충분 (초기), 향후 Airflow |
| API | FastAPI | 0.100+ | 고성능, 자동 문서화 (선택사항) |
| 로깅 | Loguru | 0.7+ | 사용 편의성 |

---

## 📁 프로젝트 구조

```
KOREA/
├── README.md                    # 프로젝트 소개, 빠른 시작
├── PROJECT_MASTER.md            # 👈 이 파일 (프로젝트 전체 컨텍스트)
├── PROJECT_ANALYSIS.md          # 상세 분석, 아키텍처 설계
├── SETUP_GUIDE.md               # 환경 설정 가이드 (상세)
├── DEVELOPMENT_LOG.md           # 개발 이력 및 주요 결정사항
├── TODO.md                      # 작업 목록 (우선순위별)
│
├── .env.example                 # 환경변수 템플릿
├── .gitignore                   # Git 무시 파일
├── requirements.txt             # Python 의존성
├── pyproject.toml               # 프로젝트 메타데이터 (선택)
│
├── config/                      # 설정 관리
│   ├── __init__.py
│   ├── settings.py             # 환경변수 로드, 설정 클래스
│   └── logging_config.py       # 로깅 설정
│
├── database/                    # 데이터베이스 관련
│   ├── __init__.py
│   ├── connection.py           # DB 연결 풀 관리
│   ├── models.py               # SQLAlchemy ORM 모델
│   ├── schema/
│   │   ├── init_schema.sql    # 초기 스키마 생성 SQL
│   │   └── indexes.sql        # 인덱스 정의
│   └── migrations/             # Alembic 마이그레이션
│
├── collectors/                  # 데이터 수집기
│   ├── __init__.py
│   ├── base.py                 # 베이스 Collector 추상 클래스
│   ├── infomax.py              # 인포맥스 API 수집기
│   ├── hts.py                  # 증권사 HTS 수집기
│   ├── crawler.py              # 웹 크롤러
│   └── utils.py                # 공통 유틸리티
│
├── validators/                  # 데이터 검증
│   ├── __init__.py
│   ├── schemas.py              # Pydantic 스키마 정의
│   ├── quality_checks.py       # 데이터 품질 체크
│   └── business_rules.py       # 비즈니스 규칙 검증
│
├── etl/                         # ETL 파이프라인
│   ├── __init__.py
│   ├── extract.py              # 데이터 추출 로직
│   ├── transform.py            # 데이터 변환 로직
│   ├── load.py                 # 데이터 적재 로직
│   └── pipeline.py             # 전체 파이프라인 오케스트레이션
│
├── schedulers/                  # 스케줄링
│   ├── __init__.py
│   ├── jobs.py                 # 개별 스케줄 작업 정의
│   └── scheduler.py            # 스케줄러 메인 실행
│
├── api/                         # API 레이어 (선택사항 - Phase 5)
│   ├── __init__.py
│   ├── main.py                 # FastAPI 앱
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── stocks.py           # 종목 관련 엔드포인트
│   │   ├── market_data.py      # 시장 데이터 엔드포인트
│   │   └── investor_trading.py # 수급 데이터 엔드포인트
│   └── dependencies.py         # 의존성 주입
│
├── utils/                       # 유틸리티
│   ├── __init__.py
│   ├── logger.py               # 로거 설정
│   ├── exceptions.py           # 커스텀 예외
│   └── helpers.py              # 헬퍼 함수
│
├── scripts/                     # 유틸리티 스크립트
│   ├── init_database.py        # DB 초기화
│   ├── backfill_data.py        # 과거 데이터 채우기
│   ├── collect_stocks.py       # 종목 마스터 수집
│   └── data_quality_report.py  # 품질 리포트 생성
│
├── tests/                       # 테스트
│   ├── __init__.py
│   ├── conftest.py             # pytest 설정
│   ├── test_collectors/
│   ├── test_validators/
│   └── test_etl/
│
└── notebooks/                   # Jupyter 노트북 (분석용)
    └── exploration.ipynb
```

---

## 📅 개발 로드맵 (6 Phases)

### Phase 1: 기반 구축 (1-2주) ⬅️ **현재 여기**

**목표**: 프로젝트 뼈대 완성, DB 설정

- [x] 프로젝트 문서화 완료
  - [x] PROJECT_MASTER.md
  - [x] PROJECT_ANALYSIS.md
  - [x] README.md
  - [x] SETUP_GUIDE.md
  - [x] TODO.md
- [ ] 개발 환경 설정
  - [ ] PostgreSQL + TimescaleDB 설치
  - [ ] Python 가상환경 및 의존성 설치
  - [ ] .env 파일 설정
- [ ] 프로젝트 구조 생성
  - [ ] 폴더 및 기본 파일 생성
  - [ ] requirements.txt 작성
- [ ] 데이터베이스 설정
  - [ ] DB 생성 (korea_stock_data)
  - [ ] 기본 스키마 생성 (종목 마스터, 일봉, 수급)
  - [ ] TimescaleDB Hypertable 설정
- [ ] 핵심 모듈 개발
  - [ ] config/settings.py (환경변수 로드)
  - [ ] database/connection.py (DB 연결 풀)
  - [ ] utils/logger.py (로깅 설정)

**완료 기준**: DB에 테이블 생성 완료, 기본 연결 테스트 성공

---

### Phase 2: 데이터 수집기 개발 (2-3주)

**목표**: 인포맥스 API로 MVP 데이터 수집

- [ ] 인포맥스 API 연동
  - [ ] 종목 마스터 수집기 (collectors/infomax.py)
  - [ ] 일봉 OHLCV 수집기
  - [ ] 투자자별 수급 수집기
- [ ] 데이터 검증 로직
  - [ ] Pydantic 스키마 정의 (validators/schemas.py)
  - [ ] 데이터 품질 체크 (NULL, 중복, 범위)
- [ ] ETL 파이프라인
  - [ ] 스테이징 → 프로덕션 로드 (etl/load.py)
  - [ ] 에러 핸들링 및 재시도 로직
  - [ ] 데이터 수집 로그 기록
- [ ] 테스트
  - [ ] 단위 테스트 작성 (pytest)
  - [ ] 실제 API 호출 테스트

**완료 기준**: 수동 실행으로 데이터 수집 및 DB 저장 성공

---

### Phase 3: 스케줄링 및 자동화 (1주)

**목표**: 일별 자동 수집 시스템 구축

- [ ] APScheduler 설정
  - [ ] 일별 수집 작업 스케줄 (장 마감 후 18:00)
  - [ ] 비정기 작업 트리거 (종목 마스터 등)
- [ ] 모니터링
  - [ ] 수집 성공/실패 알림 (로그)
  - [ ] 간단한 상태 확인 스크립트
- [ ] 문서화
  - [ ] 스케줄러 실행 가이드
  - [ ] 장애 대응 매뉴얼

**완료 기준**: 1주일 이상 무인 자동 수집 성공

---

### Phase 4: 데이터 품질 및 백업 (1주)

**목표**: 데이터 신뢰성 확보

- [ ] 데이터 품질 체크 자동화
  - [ ] 일별 품질 리포트 생성
  - [ ] 이상 탐지 알림 시스템
- [ ] 백업 전략
  - [ ] PostgreSQL 일별 백업 (pg_dump)
  - [ ] 백업 보관 정책 (7일치)
  - [ ] 복구 테스트
- [ ] 과거 데이터 백필
  - [ ] 백필 스크립트 (scripts/backfill_data.py)
  - [ ] 과거 1년치 데이터 수집

**완료 기준**: 백업 자동화 완료, 1년치 과거 데이터 확보

---

### Phase 5: 인터페이스 개발 (1-2주)

**목표**: 다른 프로젝트에서 사용하기 쉽게

- [ ] Python 라이브러리
  - [ ] SQLAlchemy 모델 export
  - [ ] 헬퍼 함수 제공 (자주 쓰는 쿼리)
- [ ] FastAPI (선택사항)
  - [ ] 기본 CRUD 엔드포인트
  - [ ] Swagger 문서 자동 생성
- [ ] 접근 권한 관리
  - [ ] Read-only 사용자 생성
  - [ ] API 키 관리 (FastAPI 사용시)
- [ ] 사용 가이드 문서
  - [ ] 다른 프로젝트 연동 예시
  - [ ] 자주 쓰는 쿼리 패턴

**완료 기준**: 수급 분석 프로젝트에서 실제 사용 성공

---

### Phase 6: 확장 및 최적화 (진행 중)

**목표**: 분봉 데이터 및 추가 소스 통합

- [ ] 분봉 데이터 수집
  - [ ] ohlcv_minute 테이블 생성
  - [ ] 실시간 수집기 개발
  - [ ] 압축 정책 설정
- [ ] 증권사 HTS API 연동
  - [ ] 추가 데이터 소스 통합
- [ ] 웹 크롤링
  - [ ] ETF PDF 등 부가 데이터
- [ ] 성능 최적화
  - [ ] 인덱스 튜닝
  - [ ] 쿼리 최적화
  - [ ] 캐싱 레이어 (Redis - 필요시)
- [ ] Airflow 마이그레이션 (선택)
  - [ ] 복잡한 워크플로우 관리

**완료 기준**: 분봉 데이터 안정적 수집, 모든 소스 통합

---

## ⚠️ 중요 설계 결정 및 주의사항

### 1. 연기금 데이터 처리 ⚠️ 중요!

**문제**: 연기금은 기관 데이터에 포함되지만, 별도 표기 필요

**해결책**:
```python
# 기관(순수) 계산시
institution_pure = institution_total - pension
```

**DB 설계**:
- `investor_trading` 테이블에 4개 타입 저장: FOREIGN, INSTITUTION, RETAIL, PENSION
- 집계시 중복 제거 로직 필요

### 2. 파티셔닝 전략

**일봉 데이터**: TimescaleDB가 자동 파티셔닝 (기본 7일 chunk)
```sql
SELECT create_hypertable('ohlcv_daily', 'time');
```

**분봉 데이터**: 1일 단위 파티셔닝 권장
```sql
SELECT create_hypertable('ohlcv_minute', 'time',
    chunk_time_interval => INTERVAL '1 day');
```

### 3. 압축 정책 (스토리지 절감)

오래된 데이터 자동 압축 (80% 공간 절약)
```sql
ALTER TABLE ohlcv_daily SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'stock_code'
);

SELECT add_compression_policy('ohlcv_daily', INTERVAL '30 days');
```

### 4. 인덱스 전략

**필수 인덱스**:
```sql
-- 종목 + 시간 역순 (가장 자주 쓰임)
CREATE INDEX idx_ohlcv_stock_time ON ohlcv_daily(stock_code, time DESC);

-- 투자자 타입 + 시간
CREATE INDEX idx_investor_type ON investor_trading(investor_type, time DESC);
```

### 5. 데이터 정합성 체크

**필수 검증 항목**:
- ✅ 가격 범위 체크 (음수, 비정상적 급등락)
- ✅ 거래량 일관성 (음수, NULL)
- ✅ 날짜 체크 (미래 날짜, 주말/공휴일)
- ✅ 중복 체크 (UNIQUE 제약조건)

### 6. 상장폐지 종목 처리

**Soft Delete 방식**:
```sql
ALTER TABLE stocks ADD COLUMN is_active BOOLEAN DEFAULT TRUE;
ALTER TABLE stocks ADD COLUMN delisting_date DATE;
```

쿼리시 `WHERE is_active = TRUE` 조건 추가

### 7. 보안 주의사항

- **API 키**: 절대 Git에 커밋 금지 (.env 파일, .gitignore 필수)
- **DB 비밀번호**: 환경변수 사용
- **외부 접근**: PostgreSQL은 localhost만 허용, 필요시 VPN

---

## 🔄 작업 재개시 체크리스트

**새 환경(집/회사)에서 프로젝트 열었을 때**:

1. [ ] 이 파일(PROJECT_MASTER.md)부터 읽기
2. [ ] TODO.md에서 다음 할 작업 확인
3. [ ] DEVELOPMENT_LOG.md에서 최근 변경사항 확인
4. [ ] 환경 설정 필요시 SETUP_GUIDE.md 참조
5. [ ] 가상환경 활성화: `source venv/bin/activate`
6. [ ] DB 연결 확인: `python -c "from database.connection import test_connection; test_connection()"`

**작업 완료 후**:

1. [ ] TODO.md 업데이트 (완료 항목 체크)
2. [ ] DEVELOPMENT_LOG.md에 작업 내용 기록
3. [ ] 중요한 결정사항은 이 파일(PROJECT_MASTER.md)에도 반영
4. [ ] Git 커밋 (`.env` 제외 확인)

---

## 📞 데이터 소스 정보

### 인포맥스 API

**문서 위치**: (인포맥스 API 문서 업로드 예정)

**주요 엔드포인트** (예시):
- 종목 마스터: `/api/v1/stocks/master`
- 일봉 데이터: `/api/v1/market/ohlcv`
- 투자자별 수급: `/api/v1/market/investor_trading`

**인증 방식**: API Key + Secret

**제약사항**:
- 호출 제한: (확인 필요)
- 재배포 금지

### 증권사 HTS API

(Phase 6에서 추가 예정)

### 웹 크롤링

(Phase 6에서 추가 예정)

**주의**: robots.txt 준수, 법적 문제 확인

---

## 🐛 알려진 이슈 및 해결 방법

(개발 진행하면서 업데이트)

**예시**:
- **이슈**: TimescaleDB 설치시 `shared_preload_libraries` 설정 필요
- **해결**: postgresql.conf에 `shared_preload_libraries = 'timescaledb'` 추가 후 재시작

---

## 📚 참고 자료

- [PostgreSQL 공식 문서](https://www.postgresql.org/docs/)
- [TimescaleDB 공식 문서](https://docs.timescale.com/)
- [SQLAlchemy 2.0 문서](https://docs.sqlalchemy.org/)
- [Pydantic 문서](https://docs.pydantic.dev/)
- [FastAPI 문서](https://fastapi.tiangolo.com/)

---

## 💬 프로젝트 철학

1. **완벽보다 진행**: MVP부터 시작, 점진적 개선
2. **문서화 우선**: 코드보다 의도와 컨텍스트가 중요
3. **자동화 집착**: 수동 작업은 언젠가 실수를 만든다
4. **단순함 유지**: 과도한 엔지니어링 지양
5. **데이터 품질**: 많은 데이터보다 정확한 데이터

---

**다음 할 일**: TODO.md 확인 → Phase 1 진행

**막히면**: SETUP_GUIDE.md 또는 PROJECT_ANALYSIS.md 참조
