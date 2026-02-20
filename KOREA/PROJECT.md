# 🏢 한국 주식시장 데이터 중앙 관리 시스템

> **마지막 업데이트**: 2026-02-20 (수급거래량 적재, FnGuide 크롤링 완료)
> **프로젝트 상태**: Phase 1 완료 ✅ → Phase 2 API 연동 준비 중
> **Repository**: https://github.com/unanimous0/Finance_Data/tree/main/KOREA

---

## 📌 30초 요약

**목적**: 한국 주식 데이터를 중앙에서 수집/관리하여 여러 프로젝트에서 공유 사용

**핵심 결정**:
- ✅ DB: PostgreSQL 17 + TimescaleDB 2.25 (시계열 최적화)
- ✅ 언어: Python 3.14
- ✅ 아키텍처: 수집 → 검증 → DB 저장
- ✅ 우선순위: 종목 마스터 → 일봉 OHLCV → 투자자별 수급

**현재 위치**:
- Phase 1 완료 ✅ (DB 구축, 핵심 모듈, 데이터 적재 및 검증)
  - **20.6M+ 레코드** 적재 완료 (2022-2026, 4년치)
  - 3,726개 종목 × OHLCV/시가총액/투자자수급(거래대금+거래량)/유동주식수 데이터
  - 데이터 품질 검증 완료 (정합성, 연속성, 스팟체크)
  - 스키마 정리 완료 (불필요 컬럼 삭제)
- 다음: Phase 2 (94개 ETF 데이터 보충, API 연동)

---

## 📊 현재 데이터 현황 (2026-02-20 기준)

### 적재 완료 데이터

| 테이블 | 레코드 수 | 기간 | 종목 수 |
|--------|----------|------|---------|
| stocks | 3,820건 | - | 3,820개 |
| ohlcv_daily | 3,257,951건 | 2022-01-03 ~ 2026-02-13 | 3,726개 |
| market_cap_daily | 3,257,951건 | 2022-01-03 ~ 2026-02-13 | 3,726개 |
| investor_trading | 13,031,804건 | 2022-01-03 ~ 2026-02-13 | 3,726개 |
| floating_shares | ~1,054,680건 | 2022-01-03 ~ 2026-02-19 | ~2,635개 |
| **합계** | **~20.6M건** | **4년치** | - |

### 투자자 타입별 데이터

| 투자자 타입 | 레코드 수 | net_buy_value | net_buy_volume | 비고 |
|-----------|----------|:---:|:---:|------|
| FOREIGN (외국인) | 3,257,951건 | ✅ | ✅ | |
| INSTITUTION (기관계) | 3,257,951건 | ✅ | ✅ | |
| PENSION (연기금) | 3,257,951건 | ✅ | ✅ | API에서 불가능한 데이터! |
| RETAIL (개인) | 3,257,951건 | ✅ | ✅ | |

### 스키마 정리 이력

- `market_cap_daily.shares_outstanding` 삭제 (2026-02-19, 전부 NULL)
- `investor_trading.buy_volume` 삭제 (2026-02-19, 전부 NULL)
- `investor_trading.sell_volume` 삭제 (2026-02-19, 전부 NULL)
- `investor_trading.buy_value` 삭제 (2026-02-19, 전부 NULL)
- `investor_trading.sell_value` 삭제 (2026-02-19, 전부 NULL)
- `investor_trading.net_buy_volume` 적재 완료 (2026-02-20, 13~16번 CSV)
- investor_trading 최종 구성: `net_buy_value`(순매수거래대금) + `net_buy_volume`(순매수거래량)

### 유동주식수 업데이트 정책

| 데이터 | 소스 | 업데이트 주기 |
|--------|------|-------------|
| 발행주식수(보통주), 유동주식수, 유동비율 | FnGuide 웹 크롤링 | **월 1~2회** |
| (스크립트) | `scripts/crawl_floating_shares.py` | 수동 실행 |

- floating_ratio 기준: FnGuide 사이트 값 우선 (지수산정주식수 기준)
- 2022-01-03 ~ 2026-02-18: 191개 종목 floating_ratio=NULL (유동주식수>발행주식수 이상, 레거시)
- 2026-02-19~: FnGuide 크롤링 데이터로 정상화

### 데이터 출처

- **원본**: raw_data/temp/ 폴더의 14개 파일 (CSV 11개 + xlsx 3개)
- **형식**: Pivot (행=날짜, 열=종목명)
- **변환**: `scripts/load_all_data_from_csv.py`로 정규화 후 적재

### 미매칭/누락 종목

- **23개**: CSV에 있지만 stocks에 없음 (상장폐지 종목)
- **94개**: stocks에 있지만 CSV에 없음 (신규 상장 ETF, 데이터 보충 필요)

---

## 🎯 1. 프로젝트 배경

### 왜 이 프로젝트를 시작했나?

**기존 문제**:
1. 수급 분석, 실전 매매, LP/MM 등 **여러 프로젝트 동시 진행** 중
2. 각 프로젝트마다 **개별 DB 관리** → 데이터 중복, 정합성 문제
3. 한국 주식시장 데이터 관리가 **감당 불가능**한 수준으로 증가

**솔루션**:
- 데이터 관리를 **독립 프로젝트로 분리**
- **Single Source of Truth** 역할
- 다른 프로젝트들은 이 DB를 **읽기 전용으로 참조**

### 프로젝트 가치

1. **일관성**: 모든 프로젝트가 동일한 데이터 사용
2. **효율성**: 데이터 수집/관리 작업 일원화
3. **확장성**: 새 프로젝트 추가시 인프라 재사용
4. **품질**: 중앙집중식 데이터 검증 및 품질 관리

---

## 📊 2. 관리 데이터

### 메타데이터 (비정기 업데이트)

| 데이터 | 업데이트 주기 | 레코드 수 | 중요도 |
|--------|-------------|----------|--------|
| 종목 마스터 | 비정기 (상장/폐지시) | ~3,000건 | 🔴 최고 |
| 지수 구성종목 | 분기별 | ~350건 | 🟡 중간 |
| 섹터 분류 | 연간 | ~3,000건 | 🟡 중간 |
| 유동주식수 | 비정기 | ~3,000건 | 🟢 낮음 |
| ETF 포트폴리오 | 주간 | 가변 | 🟢 낮음 |

### 시계열 데이터 (정기 업데이트)

| 데이터 | 주기 | 1일 레코드 | 연간 누적 | 중요도 |
|--------|-----|----------|----------|--------|
| 시가총액 | 일별 | ~3,000건 | ~75만건 | 🟡 중간 |
| OHLCV (일봉) | 일별 | ~3,000건 | ~75만건 | 🔴 최고 |
| OHLCV (분봉) | 분단위 | ~50만건 | ~1.2억건 | 🔴 최고 |
| 투자자별 수급 | 일별 | ~12,000건 | ~300만건 | 🔴 최고 |

**데이터 특성**:
- 전체의 80% 이상이 시계열 데이터
- Write-Once, Read-Many 패턴
- 분봉 전환시 연간 ~1.8억 레코드 예상
- 복잡한 JOIN 쿼리 빈번 (종목 마스터 + 시계열)

### 특수 처리 사항

**연기금 데이터**:
- 문제: 연기금은 기관에 포함되지만 별도 추적 필요
- 해결: `investor_trading` 테이블에 4개 유형 저장
  - `FOREIGN` (외국인)
  - `INSTITUTION` (기관 - 연기금 제외)
  - `RETAIL` (개인)
  - `PENSION` (연기금)
- 주의: 기관(전체) = INSTITUTION + PENSION

---

## 🏗️ 3. 시스템 아키텍처

### 데이터 플로우

```
[데이터 소스]
  ├─ 인포맥스 API (유료)
  ├─ 증권사 HTS API
  └─ 웹 크롤링
         ↓
[데이터 수집 - Collectors]
  - API 호출, 에러 핸들링
  - 스케줄러 (APScheduler)
         ↓
[데이터 검증 - Validators]
  - Pydantic 스키마 검증
  - 비즈니스 규칙 체크
  - 이상치 탐지
         ↓
[프로덕션 DB - PostgreSQL + TimescaleDB]
  - 메타데이터 (일반 테이블)
  - 시계열 데이터 (Hypertable)
  - 모니터링 (수집 로그, 품질 체크)
         ↓
[인터페이스]
  ├─ Python 라이브러리 (SQLAlchemy ORM)
  ├─ FastAPI (REST API) - 선택사항
  └─ Direct DB Access (Read-only 계정)
```

### 프로젝트 구조

```
KOREA/
├── config/                 # 설정 관리
│   └── settings.py        # Pydantic Settings
│
├── database/              # 데이터베이스 관련
│   ├── schema/
│   │   └── init_schema.sql    # 초기 스키마 정의
│   ├── connection.py      # SQLAlchemy 연결 관리
│   └── models.py          # ORM 모델 (10개)
│
├── collectors/            # 데이터 수집기
│   └── infomax.py        # 인포맥스 API 수집기 (예정)
│
├── validators/            # 데이터 검증
│   └── schemas.py        # Pydantic 스키마 (10개)
│
├── etl/                   # ETL 파이프라인
│   ├── extract.py        # 데이터 추출
│   ├── transform.py      # 데이터 변환
│   └── load.py           # DB 적재
│
├── schedulers/            # 스케줄링
│   └── jobs.py           # APScheduler 작업
│
├── utils/                 # 유틸리티
│   ├── logger.py         # Loguru 로깅
│   └── exceptions.py     # 커스텀 예외
│
├── scripts/               # 실행 스크립트
│   ├── test_api_format.py       # API 형식 테스트
│   ├── collect_historical_data.py  # 2년치 데이터 수집
│   └── verify_data.py    # 데이터 검증
│
├── tests/                 # 테스트 코드
│   ├── conftest.py       # pytest 설정
│   ├── test_validators/
│   └── test_models/
│
└── logs/                  # 로그 파일
```

---

## 🛠️ 4. 기술 스택

### 데이터베이스

**선택**: PostgreSQL 17 + TimescaleDB 2.25

**선택 이유**:
1. **시계열 데이터 최적화**
   - TimescaleDB의 자동 파티셔닝 (Hypertable)
   - 시간 기반 쿼리 성능 10-100배 향상
   - 데이터 압축 (스토리지 80% 절감)
   - Continuous Aggregates (실시간 집계)

2. **PostgreSQL 강점 유지**
   - 복잡한 JOIN, 트랜잭션 지원
   - JSONB로 유연한 스키마
   - 강력한 인덱스 지원
   - 성숙한 생태계

3. **검토했지만 제외한 대안**
   - ❌ SQLite: 동시 쓰기 제한, 시계열 약함
   - ❌ MySQL: 시계열 기능 부족
   - ❌ ClickHouse: JOIN 약함, 복잡도 높음
   - ❌ InfluxDB: 메타데이터 JOIN 불가

### 핵심 라이브러리

| 라이브러리 | 버전 | 용도 |
|-----------|------|------|
| SQLAlchemy | 2.0.46 | ORM, DB 연결 관리 |
| psycopg2-binary | 2.9.11 | PostgreSQL 드라이버 |
| Pydantic | 2.12.5 | 데이터 검증 |
| Loguru | 0.7.3 | 구조화된 로깅 |
| APScheduler | 3.11.0 | 작업 스케줄링 |
| pandas | 3.0.0 | 데이터 분석 |
| requests | 2.32.3 | HTTP 클라이언트 |
| python-dotenv | 1.0.1 | 환경변수 관리 |
| pytest | 8.3.4 | 테스트 프레임워크 |

---

## 📐 5. 데이터베이스 설계

### 테이블 구조 (10개)

#### 메타데이터 (5개)

**1. stocks** (종목 마스터)
```sql
PRIMARY KEY: stock_code
FOREIGN KEY: sector_id → sectors
컬럼: stock_code, stock_name, market, listing_date, delisting_date, is_active
```

**2. sectors** (섹터 분류)
```sql
PRIMARY KEY: sector_id
FOREIGN KEY: parent_sector_id → sectors (자기 참조)
컬럼: sector_id, sector_name, sector_code, parent_sector_id
특징: 계층 구조 (IT → 반도체 → 메모리)
```

**3. index_components** (지수 구성종목)
```sql
PRIMARY KEY: id
FOREIGN KEY: stock_code → stocks
컬럼: index_name, stock_code, effective_date, end_date
예시: KOSPI200, KOSDAQ150
```

**4. floating_shares** (유동주식)
```sql
PRIMARY KEY: id
FOREIGN KEY: stock_code → stocks
컬럼: stock_code, base_date, total_shares, floating_shares, floating_ratio
```

**5. etf_portfolios** (ETF 구성)
```sql
PRIMARY KEY: id
FOREIGN KEY: etf_code → stocks, component_code → stocks
컬럼: etf_code, component_code, base_date, weight, shares
특징: stocks 테이블을 2번 참조 (ETF 자체 + 구성종목)
```

#### 시계열 데이터 - Hypertable (3개)

**6. market_cap_daily** (시가총액)
```sql
PRIMARY KEY: (time, stock_code)  -- 복합키
Hypertable: time 기준 자동 파티셔닝
컬럼: time, stock_code, market_cap, shares_outstanding
```

**7. ohlcv_daily** (일봉)
```sql
PRIMARY KEY: (time, stock_code)  -- 복합키
Hypertable: time 기준 자동 파티셔닝
컬럼: time, stock_code, open_price, high_price, low_price, close_price, volume, trading_value
```

**8. investor_trading** (투자자별 수급)
```sql
PRIMARY KEY: (time, stock_code, investor_type)  -- 3개 복합키
Hypertable: time 기준 자동 파티셔닝
컬럼: time, stock_code, investor_type, net_buy_value, net_buy_volume
-- net_buy_value: 순매수거래대금 (원), net_buy_volume: 순매수거래량 (주)
-- buy/sell 개별 컬럼 삭제됨 (순(net)값만 사용)
```

#### 모니터링 (2개)

**9. data_collection_logs** (수집 이력)
```sql
PRIMARY KEY: id
컬럼: data_type, collection_date, source, status, records_count, error_message, started_at, completed_at
```

**10. data_quality_checks** (품질 체크)
```sql
PRIMARY KEY: id
컬럼: table_name, check_date, check_type, issue_count, details
```

### 인덱스 전략

**메타데이터**:
- stock_code (모든 테이블)
- market (stocks)
- sector_id (stocks)

**시계열 데이터**:
- (time, stock_code) - Hypertable 자동 인덱스
- time DESC - 최근 데이터 빠른 조회
- stock_code - 종목별 조회

### 파티셔닝 전략

**일봉 데이터**:
```sql
-- 7일 단위 chunk (TimescaleDB 기본값)
SELECT create_hypertable('ohlcv_daily', 'time', chunk_time_interval => INTERVAL '7 days');
```

**분봉 데이터** (향후):
```sql
-- 1일 단위 chunk
SELECT create_hypertable('ohlcv_minute', 'time', chunk_time_interval => INTERVAL '1 day');
```

### 압축 정책

```sql
-- 30일 이후 데이터 자동 압축 (스토리지 80% 절감)
ALTER TABLE ohlcv_daily SET (timescaledb.compress);
SELECT add_compression_policy('ohlcv_daily', INTERVAL '30 days');
```

---

## 🚀 6. 개발 로드맵

### Phase 1: 기반 구축 ✅ 완료

- [x] 프로젝트 분석 및 설계
- [x] 기술 스택 결정
- [x] 문서화 (5개 MD 파일)
- [x] PostgreSQL 17 + TimescaleDB 2.25 설치
- [x] DB 생성 및 스키마 적용 (10개 테이블)
- [x] Python 환경 설정 (37개 패키지)
- [x] 핵심 모듈 개발 (settings, connection, logger, exceptions)

**완료 기준 달성**: ✅

### Phase 2: 데이터 수집기 개발 (진행 중)

**완료**:
- [x] SQLAlchemy ORM 모델 (10개)
- [x] Pydantic 검증 스키마 (10개)
- [x] pytest 테스트 코드 (기본 구조)

**대기 중** (Windows 환경 필요):
- [ ] 인포맥스 API 데이터 형식 확인
- [ ] API 연동 (collectors/infomax.py)
- [ ] ETL 파이프라인 구축
- [ ] 2년치 데이터 수집

**완료 기준**: 수동으로 데이터 수집 → DB 저장 성공

### Phase 3: 스케줄링 및 자동화 (예정)

- [ ] APScheduler 설정
- [ ] 일별 수집 작업 (18:00 실행)
- [ ] 모니터링 및 알림
- [ ] 장애 대응 매뉴얼

**완료 기준**: 1주일 이상 무인 자동 수집 성공

### Phase 4: 데이터 품질 및 백업 (예정)

- [ ] 일별 품질 리포트 생성
- [ ] PostgreSQL 자동 백업
- [ ] 과거 1년치 데이터 백필

**완료 기준**: 백업 자동화, 1년치 데이터 확보

### Phase 5: 인터페이스 개발 (예정)

- [ ] Python 라이브러리 (SQLAlchemy export)
- [ ] FastAPI (선택)
- [ ] 접근 권한 관리
- [ ] 사용 가이드

**완료 기준**: 수급 분석 프로젝트에서 사용 성공

### Phase 6: 확장 및 최적화 (예정)

- [ ] 분봉 데이터 추가
- [ ] 추가 데이터 소스 (HTS, 크롤링)
- [ ] 성능 최적화 (인덱스 튜닝, 쿼리 최적화)
- [ ] 캐싱 (Redis)

**완료 기준**: 분봉 데이터 안정적 수집

---

## 📌 7. 주요 결정사항 및 근거

### PostgreSQL 15 → 17로 변경

**문제**: TimescaleDB Homebrew 빌드가 PostgreSQL@17용만 제공

**결정**: PostgreSQL 17 사용

**이유**:
- PostgreSQL 17이 더 최신, 성능 개선
- 기능상 차이 없음 (SQL 호환성 유지)
- TimescaleDB 2.25.0 정상 동작

### 스키마를 먼저 생성 (데이터 형식 확인 전)

**배경**: 인포맥스 API는 Windows 전용, 현재 Mac 환경

**결정**: 일반적인 주식 데이터 구조로 스키마 먼저 생성

**향후**: Windows 환경에서 실제 데이터 확인 후 조정

**근거**: ORM과 Pydantic 코드는 수정이 쉬움

### 환경 분리: 맥 & 윈도우

**상황**:
- 집(맥): 개발 환경
- 회사(윈도우): 개발 환경 + 인포맥스 API 접근

**전략**:
- 두 환경 모두 동일한 개발 환경 구축
- Git으로 코드 동기화
- 데이터 수집은 Windows에서만 실행

---

## 🔒 8. 보안 및 주의사항

### 보안

- ⚠️ `.env` 파일 절대 Git 커밋 금지
- ⚠️ API 키 노출 주의
- ✅ `.gitignore`에 민감 정보 추가
- ✅ Read-only DB 계정 별도 생성 (Phase 5)

### 데이터 라이선스

- ⚠️ 인포맥스 API 재배포 금지 조항 확인 필요
- ⚠️ 웹 크롤링시 robots.txt 준수

### 스토리지 관리

- 예상: 일봉 기준 연간 ~1GB
- 분봉 전환시: 연간 ~50GB (압축 적용시 ~10GB)
- ✅ TimescaleDB 압축 정책 필수 적용

---

## 📚 9. 참고 문서

- **SETUP.md**: 환경 설정 가이드 (macOS, Windows, 데이터 수집)
- **TODO.md**: Phase별 작업 목록
- **DEVELOPMENT_LOG.md**: 개발 이력 및 결정사항 기록
- **README.md**: 프로젝트 간단 소개

---

## 🤝 10. 연관 프로젝트

이 데이터 인프라를 사용하는 프로젝트:

1. **수급 분석 시스템** (개발 예정)
2. **실전 매매 시스템** (개발 예정)
3. **LP/MM 시스템** (개발 예정)

---

**Last Updated**: 2026-02-18
**Contact**: (작성자 정보)
