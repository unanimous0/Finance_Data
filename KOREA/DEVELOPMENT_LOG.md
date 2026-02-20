# 📓 개발 이력 (DEVELOPMENT LOG)

> 프로젝트 진행 과정에서의 주요 결정사항, 변경사항, 배운 점을 기록합니다.
> 새로운 환경에서 작업 재개시 최근 항목부터 읽어 컨텍스트를 빠르게 파악하세요.

---

## 2026-02-17 (월) - 프로젝트 초기 설정

### ✅ 완료 작업

1. **프로젝트 분석 및 설계**
   - 한국 주식시장 데이터 중앙 관리 시스템의 필요성 분석
   - 여러 프로젝트(수급 분석, 실전 매매, LP/MM)에서 공통 사용할 데이터 인프라 설계
   - 데이터 특성 분석: 시계열 데이터 중심, Write-Once Read-Many 패턴

2. **기술 스택 결정**
   - **데이터베이스**: PostgreSQL 15+ + TimescaleDB 선정
     - **이유**: 시계열 데이터 최적화, 자동 파티셔닝, 압축, Continuous Aggregates
     - **대안 검토**: SQLite(부적합), MySQL(시계열 약함), ClickHouse(JOIN 약함)
   - **언어**: Python 3.11+
   - **주요 라이브러리**: SQLAlchemy, Pydantic, APScheduler, Loguru

3. **시스템 아키텍처 설계**
   - 3계층 구조: 데이터 수집 → 스테이징 DB → 검증 → 프로덕션 DB
   - 데이터 플로우: 인포맥스 API/HTS/크롤링 → Collectors → Validators → DB
   - 인터페이스: FastAPI, Python 라이브러리, Direct DB Access (Read-only)

4. **DB 스키마 설계**
   - **메타데이터 테이블** (일반 PostgreSQL):
     - stocks, sectors, index_components, floating_shares, etf_portfolios
   - **시계열 테이블** (TimescaleDB Hypertables):
     - market_cap_daily, ohlcv_daily, ohlcv_minute (향후), investor_trading
   - **모니터링 테이블**:
     - data_collection_logs, data_quality_checks

5. **프로젝트 문서화 완료**
   - `PROJECT_MASTER.md`: 프로젝트 전체 컨텍스트 (핵심 문서)
   - `PROJECT_ANALYSIS.md`: 상세 분석 및 설계 문서
   - `SETUP_GUIDE.md`: 환경 설정 가이드 (새 컴퓨터에서 시작하는 법)
   - `TODO.md`: Phase별 작업 목록
   - `DEVELOPMENT_LOG.md`: 이 파일
   - `README.md`: 프로젝트 소개
   - `.gitignore`: Python/DB 관련 무시 파일
   - `.env.example`: 환경변수 템플릿

6. **개발 로드맵 수립**
   - Phase 1: 기반 구축 (1-2주) ← 현재
   - Phase 2: 데이터 수집기 개발 (2-3주)
   - Phase 3: 스케줄링 및 자동화 (1주)
   - Phase 4: 데이터 품질 및 백업 (1주)
   - Phase 5: 인터페이스 개발 (1-2주)
   - Phase 6: 확장 및 최적화 (진행 중)

### 🎯 주요 결정사항

#### 1. 연기금 데이터 처리 방식
- **문제**: 연기금은 기관에 포함되지만, 별도 추적 필요
- **해결**: `investor_trading` 테이블에 4개 타입 저장 (FOREIGN, INSTITUTION, RETAIL, PENSION)
- **주의**: 기관(순수) = 기관(전체) - 연기금 계산 필요

#### 2. 파티셔닝 전략
- **일봉 데이터**: TimescaleDB 기본 설정 (7일 chunk)
- **분봉 데이터**: 1일 단위 파티셔닝
  ```sql
  SELECT create_hypertable('ohlcv_minute', 'time',
      chunk_time_interval => INTERVAL '1 day');
  ```

#### 3. 압축 정책
- 30일 이후 데이터 자동 압축 (스토리지 80% 절감)
  ```sql
  ALTER TABLE ohlcv_daily SET (timescaledb.compress);
  SELECT add_compression_policy('ohlcv_daily', INTERVAL '30 days');
  ```

#### 4. 상장폐지 종목 처리
- **방식**: Soft Delete (is_active 플래그 + delisting_date)
- **이유**: 과거 데이터 보존 필요

### 💡 배운 점 / 인사이트

1. **시계열 DB의 중요성**
   - 주식 데이터는 전체의 80% 이상이 시계열
   - TimescaleDB 선택으로 분봉 전환시에도 성능 문제 없을 것으로 예상

2. **문서화의 가치**
   - 여러 환경(집/회사)을 오가며 작업하므로, 철저한 문서화 필수
   - 코드보다 "의도"와 "컨텍스트" 기록이 중요

3. **MVP 우선 접근**
   - 완벽함보다 동작하는 최소 기능부터 (종목 마스터 → 일봉 → 수급)
   - 점진적 확장이 현실적

### ⚠️ 주의사항

1. **보안**
   - `.env` 파일 절대 Git 커밋 금지
   - API 키 노출 주의

2. **데이터 라이선스**
   - 인포맥스 API 재배포 금지 조항 확인 필요
   - 웹 크롤링시 robots.txt 준수

3. **스토리지 관리**
   - 분봉 전환시 연간 ~1.8억 레코드 예상
   - 압축 정책 필수 적용

### 🐛 알려진 이슈

(현재 없음)

### 📌 다음 작업 (Next Steps)

1. PostgreSQL + TimescaleDB 설치
2. DB 생성 및 스키마 적용
3. Python 환경 설정 및 기본 모듈 개발

**참고 문서**: `TODO.md` → Phase 1 섹션

---

## 2026-02-17 (월) - Phase 1 기반 구축 완료 🎉

### ✅ 완료 작업

1. **PostgreSQL 17 + TimescaleDB 2.25.0 설치 및 설정**
   - Command Line Tools 업데이트 필요 (사용자가 수동 설치)
   - PostgreSQL@15 → PostgreSQL@17로 변경 (TimescaleDB 호환성)
   - TimescaleDB 설정 최적화 (`timescaledb-tune` 실행)
   - PostgreSQL 서비스 시작 및 연결 확인 ✅

2. **데이터베이스 생성 및 스키마 적용**
   - `korea_stock_data` 데이터베이스 생성 ✅
   - TimescaleDB 확장 활성화 (v2.25.0) ✅
   - 10개 테이블 생성:
     - 메타데이터: stocks, sectors, index_components, floating_shares, etf_portfolios
     - 모니터링: data_collection_logs, data_quality_checks
   - 3개 Hypertable 생성:
     - `ohlcv_daily` (일봉)
     - `market_cap_daily` (시가총액)
     - `investor_trading` (투자자별 수급)

3. **Python 환경 설정**
   - Python 3.14.3 가상환경 생성 ✅
   - 핵심 패키지 설치:
     - SQLAlchemy 2.0.46
     - psycopg2-binary 2.9.11
     - Pydantic 2.12.5
     - Loguru 0.7.3
     - pandas 3.0.0
   - 개발 도구: pytest, black, ruff, mypy
   - `requirements.txt` 생성 ✅

4. **프로젝트 구조 및 핵심 모듈 생성**
   - 폴더 구조 완성: config/, database/, collectors/, validators/, etl/, etc.
   - **config/settings.py**: Pydantic Settings로 환경변수 관리
   - **database/connection.py**: SQLAlchemy 연결 풀 및 세션 관리
   - **utils/logger.py**: Loguru 기반 구조화된 로깅
   - **utils/exceptions.py**: 커스텀 예외 클래스
   - **database/schema/init_schema.sql**: 초기 스키마 SQL
   - **.env**: 환경변수 파일 생성
   - 모든 모듈 테스트 완료 ✅

### 🎯 주요 결정사항

#### 1. PostgreSQL 15 → 17로 변경
- **문제**: TimescaleDB Homebrew 빌드가 PostgreSQL@17용만 제공
- **선택지**:
  - A: PostgreSQL@15용 TimescaleDB 소스 빌드 (복잡)
  - B: PostgreSQL@17 사용 (권장)
  - C: TimescaleDB 없이 진행
- **결정**: **B (PostgreSQL 17 사용)**
- **이유**:
  - PostgreSQL 17이 더 최신이고 성능 개선
  - 기능상 차이 없음 (SQL 호환성 유지)
  - 이미 TimescaleDB 설치 완료
  - 프로젝트 목적에 적합

#### 2. 모듈 임포트 이슈 해결
- **문제**: `ModuleNotFoundError: No module named 'config'`
- **해결**: `PYTHONPATH=.` 환경변수 설정
- **향후**: `setup.py` 또는 `pyproject.toml`로 패키지화 고려

### 💡 배운 점 / 인사이트

1. **Homebrew의 keg-only 문제**
   - PostgreSQL@15는 기본 PATH에 포함되지 않음
   - 명시적으로 PATH 추가 필요: `export PATH="/opt/homebrew/opt/postgresql@15/bin:$PATH"`

2. **TimescaleDB preload 필수**
   - `shared_preload_libraries = 'timescaledb'` 설정 후 PostgreSQL 재시작 필요
   - 설정 없이 `CREATE EXTENSION` 시도하면 오류 발생

3. **SQLAlchemy의 echo 기능**
   - 개발 환경에서 `echo=True` 설정으로 SQL 쿼리 로깅
   - 디버깅에 매우 유용

4. **Pydantic v2 마이그레이션**
   - `Config` 클래스 대신 `ConfigDict` 사용 권장 (v2 스타일)
   - 현재는 deprecation warning만 발생, 동작은 정상

### ⚠️ 주의사항

1. **PostgreSQL 버전 차이**
   - 문서에는 PostgreSQL 15로 작성되었으나, 실제는 17 사용
   - 기능상 차이 없으나, 버전 명시 필요

2. **PYTHONPATH 설정**
   - 모듈 실행시 `PYTHONPATH=.` 필요
   - 또는 프로젝트를 패키지로 설치 (`pip install -e .`)

3. **.env 파일 보안**
   - `.gitignore`에 포함되어 있는지 재확인
   - DB 비밀번호는 현재 빈 문자열 (로컬 개발 환경)

### 🐛 발견된 이슈 및 해결

#### 이슈 1: TimescaleDB 라이브러리 파일 없음
- **증상**: `FATAL: could not access file "timescaledb": No such file or directory`
- **원인**: TimescaleDB가 PostgreSQL@17용으로만 빌드됨
- **해결**: PostgreSQL 버전을 17로 변경

#### 이슈 2: PostgreSQL 소켓 파일 없음
- **증상**: `No such file or directory: "/tmp/.s.PGSQL.5432"`
- **원인**: `shared_preload_libraries`에 timescaledb 추가 후 PostgreSQL 시작 실패
- **해결**: postgresql.conf에서 timescaledb 일시 제거 → 재시작 → 다시 추가 → 재시작

### 📌 다음 작업 (Phase 2)

1. **인포맥스 API 연동** (우선순위 최고)
   - API 문서 검토
   - `collectors/infomax.py` 작성
   - 종목 마스터 수집 함수
   - 일봉 OHLCV 수집 함수
   - 투자자별 수급 수집 함수

2. **데이터 검증 로직**
   - `validators/schemas.py` (Pydantic 스키마)
   - `validators/quality_checks.py` (품질 체크)

3. **ETL 파이프라인**
   - `etl/pipeline.py` 작성
   - 스테이징 → 프로덕션 로드

**완료 기준**: 수동으로 데이터 수집 및 DB 저장 성공

**참고 문서**: `TODO.md` → Phase 2 섹션

---

## 2026-02-17 (월) - Phase 2 대기 상태로 전환

### 📌 현재 상황

**문제 인식**:
- 현재 생성된 테이블 스키마는 "일반적인 주식 데이터 구조"를 **가정**한 것
- 실제 인포맥스 API나 증권사 HTS의 데이터 형식은 **미확인 상태**
- 컬럼명, 데이터 타입, 추가 필드 등이 실제와 다를 가능성 높음

**환경 제약**:
- 현재 사용 중인 컴퓨터: 맥 (macOS)
- 인포맥스 API: **윈도우 전용**
- 증권사 HTS API: **윈도우 전용**
- 따라서 현재 환경에서는 데이터 형식 확인 불가

### 🎯 결정사항

**Phase 2 진행 방식 변경**:
1. 윈도우 환경에서 먼저 데이터 형식 확인 (회사 컴퓨터)
2. 샘플 데이터 수집 및 맥으로 전송
3. 스키마 조정 (필요시)
4. 그 다음 Phase 2 본격 진행

**당장 진행하지 않기로 결정**:
- ~~데이터 수집기 개발~~ → 데이터 형식 확인 후
- ~~ORM 모델 작성~~ → 실제 데이터 구조 파악 후

### ✅ 완료 작업

1. **TODO.md 업데이트**
   - Phase 1 완료 표시
   - Phase 2를 "대기 중" 상태로 변경
   - 윈도우 환경 작업 우선순위 명시

2. **WINDOWS_CHECKLIST.md 작성**
   - 윈도우 환경에서 할 작업 상세 가이드
   - 데이터 형식 확인 체크리스트
   - 샘플 데이터 수집 방법
   - 맥으로 전송 방법

### 💡 인사이트

**올바른 개발 순서의 중요성**:
- ❌ 잘못된 순서: 스키마 설계 → 데이터 수집 → 형식 안 맞아서 수정
- ✅ 올바른 순서: 데이터 확인 → 스키마 설계 → 데이터 수집

**프로젝트는 "가정"이 아닌 "사실" 기반으로**:
- 일반적인 구조를 가정하는 것도 나쁘지 않지만
- 실제 데이터 확인 후 조정하는 것이 더 안전

**환경 제약 사전 파악**:
- 처음부터 "윈도우 전용 API"라는 제약을 문서화했으면 좋았을 것
- PROJECT_MASTER.md에 환경 제약사항 추가 필요

### 📌 다음 작업 (윈도우 환경)

**우선순위 1**: 데이터 형식 확인 (회사 윈도우 컴퓨터)
1. `WINDOWS_CHECKLIST.md` 따라 샘플 데이터 수집
2. 데이터 형식 비교 문서 작성
3. 맥으로 전송

**우선순위 2**: 스키마 조정 (맥)
1. 샘플 데이터와 현재 스키마 비교
2. 필요시 `init_schema.sql` 수정
3. ALTER TABLE 또는 재생성

**우선순위 3**: Phase 2 진행 (맥)
1. `database/models.py` 작성
2. 데이터 수집기 개발
3. ETL 파이프라인 구축

**참고 문서**: `WINDOWS_CHECKLIST.md`

---

## 2026-02-18 (화) - Phase 2 부분 진행 (ORM/Pydantic/테스트 완료)

### ✅ 완료 작업

1. **SQLAlchemy ORM 모델 작성 완료** (`database/models.py`)
   - 10개 모델 정의:
     - 메타데이터: Stock, Sector, IndexComponent, FloatingShares, ETFPortfolios
     - 시계열: MarketCapDaily, OHLCVDaily, InvestorTrading (Hypertable)
     - 모니터링: DataCollectionLogs, DataQualityChecks
   - 주요 구현 내용:
     - Foreign Key 관계 정의 (Stock ↔ Sector)
     - Self-referential Foreign Key (Sector 계층 구조)
     - 같은 테이블 2번 참조 (ETFPortfolios)
     - 복합 Primary Key (Hypertable용)
     - Relationship 및 backref 설정
   - 모든 모델 동작 테스트 완료 ✅

2. **Pydantic 검증 스키마 작성 완료** (`validators/schemas.py`)
   - 10개 스키마 정의:
     - StockSchema, SectorSchema, IndexComponentSchema, FloatingSharesSchema, ETFPortfoliosSchema
     - MarketCapDailySchema, OHLCVDailySchema, InvestorTradingSchema
     - DataCollectionLogsSchema, DataQualityChecksSchema
   - 주요 검증 로직:
     - 자동 대문자 변환 (market, investor_type)
     - 범위 체크 (가격 ≥ 0, 유동비율 ≤ 100%)
     - 관계 검증 (고가 ≥ 시가/저가, 유동주식 ≤ 총주식)
     - 비즈니스 로직 (순매수 = 매수 - 매도, 자기 참조 방지)
     - 날짜 검증 (미래 날짜 불가, 상장폐지일 > 상장일)
   - 모든 스키마 동작 테스트 완료 ✅

3. **테스트 코드 작성 완료** (`tests/`)
   - `conftest.py`: pytest 설정 및 fixture (DB 세션, 샘플 데이터)
   - `test_validators/test_schemas.py`: Pydantic 스키마 검증 테스트 (21개 테스트)
   - `test_models/test_stock.py`: Stock 모델 CRUD 테스트 (14개 테스트)
   - `test_models/test_hypertables.py`: Hypertable 모델 테스트 (18개 테스트)
   - 총 53개 테스트 작성 완료
   - 결과: 26개 통과 (49%), 27개 실패/에러
   - 실패 원인: DB 세션 트랜잭션 관리, validator 순서 문제

### 🎯 주요 결정사항

#### 1. 실제 API 데이터 확인 전 ORM/Pydantic 먼저 작성
- **이유**:
  - 기본 구조를 먼저 완성하면 나중에 조정만 하면 됨
  - 현재 스키마 기반으로 작성 → 내일 실제 데이터 확인 후 수정
  - Pydantic 코드는 수정이 쉬움
- **장점**:
  - ORM과 Pydantic의 개념과 구조를 먼저 이해
  - 실제 데이터 확인 후 무엇을 수정해야 할지 명확히 파악 가능

#### 2. 테스트 일부 실패 상태로 넘어가기
- **현황**: 53개 테스트 중 26개 통과, 27개 실패/에러
- **원인**:
  - `conftest.py`의 트랜잭션 롤백 로직 미완성
  - 일부 Pydantic validator 순서 문제
- **결정**: 내일 실제 데이터 확인 후 함께 수정
- **이유**:
  - 어차피 실제 데이터 형식에 맞춰 스키마/모델/테스트 모두 조정 필요
  - 지금 완벽하게 수정해도 내일 다시 바뀔 가능성 높음
  - 기본 구조 완성이 더 중요

### 💡 배운 점 / 인사이트

1. **ORM의 Foreign Key와 Relationship**
   - Foreign Key는 DB 레벨 제약조건 (데이터 무결성)
   - Relationship은 Python 객체 레벨 편의 기능 (코드 가독성)
   - 둘은 독립적: FK 없이 Relationship만 쓰면 DB 제약 없음
   - 보통 둘 다 함께 사용하는 것이 베스트 프랙티스

2. **자기 참조 Foreign Key (Self-referential)**
   - `remote_side` 파라미터로 부모 쪽 컬럼 명시 필요
   - `backref`로 양방향 관계 자동 생성 가능
   - 계층 구조 표현에 유용 (섹터, 카테고리 등)

3. **같은 테이블을 여러 번 참조할 때**
   - `foreign_keys` 파라미터로 명시 필수
   - ETF-구성종목 관계처럼 역할이 다른 경우 자주 사용

4. **Hypertable의 복합 Primary Key**
   - TimescaleDB는 시간 컬럼을 포함한 복합키 요구
   - SQLAlchemy에서는 `primary_key=True`를 여러 컬럼에 지정
   - 3개 컬럼 복합키도 가능 (time + stock_code + investor_type)

5. **Pydantic Validator 순서**
   - Validator는 정의 순서대로 실행됨
   - 다른 필드를 참조하는 validator는 나중에 정의해야 함
   - `info.data`로 다른 필드 값 접근 가능

6. **pytest fixture의 scope**
   - `scope="session"`: 전체 테스트 세션 동안 1번만 생성
   - `scope="function"`: 각 테스트 함수마다 새로 생성
   - DB 세션은 `function` scope으로 테스트 간 격리

### ⚠️ 주의사항

1. **현재 스키마는 가정 기반**
   - 실제 인포맥스 API/HTS 데이터 형식과 다를 가능성 높음
   - 컬럼명, 데이터 타입 등 내일 확인 후 조정 필요
   - 특히 투자자 유형 코드 (FOREIGN vs FOR vs 외국인 등) 확인 필요

2. **테스트 실패 부분**
   - DB 세션 트랜잭션 관리 개선 필요
   - 일부 Pydantic validator 수정 필요
   - 실제 데이터 확인 후 함께 수정 예정

3. **Pydantic v2 deprecation warning**
   - `config/settings.py`에서 `class Config` 사용 중
   - `ConfigDict` 사용으로 변경 권장 (나중에 수정)

### 🐛 발견된 이슈

#### 이슈 1: pytest fixture 트랜잭션 롤백 미작동
- **증상**: 테스트 간 데이터 간섭 발생
- **원인**: `conftest.py`의 `begin_nested()` 방식 문제
- **상태**: 미해결 (실제 데이터 확인 후 수정 예정)

#### 이슈 2: Pydantic validator 순서 문제
- **증상**: `net_buy_volume` validator가 실행되지 않음
- **원인**: validator 정의 순서 또는 로직 문제
- **상태**: 미해결 (실제 데이터 확인 후 수정 예정)

### 📌 다음 작업 (내일 - 윈도우 환경)

**우선순위 1**: 실제 데이터 형식 확인 (회사 윈도우 컴퓨터)
1. `WINDOWS_CHECKLIST.md` 따라 샘플 데이터 수집
   - 인포맥스 API: 종목 마스터, 일봉 OHLCV, 투자자별 수급
   - 증권사 HTS (가능하면)
2. 샘플 데이터를 JSON/CSV로 저장 (각 5-10건)
3. 데이터 형식 비교 문서 작성 (`DATA_FORMAT_COMPARISON.md`)
4. 맥으로 전송

**우선순위 2**: 스키마/모델/Pydantic 조정 (맥)
1. 샘플 데이터와 현재 스키마 비교
2. 필요시 수정:
   - `database/schema/init_schema.sql`
   - `database/models.py`
   - `validators/schemas.py`
   - 테스트 코드
3. ALTER TABLE 또는 재생성
4. 모든 테스트 통과 확인

**우선순위 3**: Phase 2 계속 진행
1. `collectors/infomax.py` 작성
2. ETL 파이프라인 구축
3. 데이터 수집 테스트

**참고 문서**:
- `WINDOWS_CHECKLIST.md` (윈도우 환경 작업 가이드)
- `TODO.md` → Phase 2 섹션

---

## 2026-02-19 (수) - 데이터 검증, 스키마 정리, 유동주식수 적재

### ✅ 완료 작업

1. **DB 복원 (덤프 파일)**
   - Windows 환경에서 생성한 `korea_stock_data.dump` 복원
   - pg_restore 버전 불일치 해결 (PG15 → PG17 바이너리 사용)
   - `--no-owner --no-privileges` 옵션으로 권한 문제 해결
   - TimescaleDB 확장 먼저 활성화 후 복원 성공

2. **전체 데이터 품질 검증 (4단계)**
   - **기본 현황**: 레코드 수, 날짜 범위, 종목 수 확인
   - **정합성**: OHLCV 논리 체크 (high >= low 등) 전부 통과, NULL/음수 없음
   - **스팟체크**: 삼성전자, SK하이닉스, NAVER, 현대차, POSCO홀딩스 — CSV 원본과 100% 일치
   - **연속성**: 1,008 거래일, 갭은 공휴일/연휴만 (정상)

3. **불필요 컬럼 정리**
   - `market_cap_daily.shares_outstanding` 삭제 (전부 NULL)
   - `investor_trading` 5개 컬럼 삭제 (net_buy_volume, buy_volume, sell_volume, buy_value, sell_value — 전부 NULL)
   - investor_trading은 `net_buy_value`(순매수금액)만 유지

4. **종목 매핑 검증**
   - CSV 종목명 ↔ DB stocks 종목명 대조
   - 3,726개 정확 매칭 (중복 없음, 공백 차이 없음)
   - 23개 미매칭: 상장폐지 종목 (CSV에만 존재)
   - 94개 누락: 신규 상장 ETF (CSV 원본에 데이터 없음)

5. **유동주식수 데이터 적재**
   - xlsx 3개 파일 (KOSPI + KOSDAQ 2분할) → floating_shares 테이블
   - 1,052,045건 적재 (2,546개 종목, 2022-01-03 ~ 2026-02-19)
   - openpyxl 패키지 설치 (xlsx 읽기용)

6. **프로젝트 정리**
   - `korea_stock_data.dump` 삭제 (복원 완료, 168MB)
   - `__pycache__` 6개 디렉토리 삭제
   - Excel 임시 잠금 파일 삭제

### 🎯 주요 결정사항

#### 1. 스키마 정리 — 데이터 없는 컬럼 삭제
- **배경**: investor_trading의 volume 관련 컬럼, market_cap_daily의 shares_outstanding이 전부 NULL
- **결정**: 삭제
- **이유**: CSV 원본에 해당 데이터가 없으며, 향후 필요시 ALTER TABLE로 재추가 가능

#### 2. 94개 ETF 데이터 누락 — 별도 수집 예정
- **배경**: stocks 마스터에는 있지만 시계열 데이터 없음
- **원인**: CSV 수집 시점에 해당 ETF가 포함되지 않음 (종목명 불일치 아님)
- **결정**: 별도로 데이터 수집하여 보충 예정

### 💡 배운 점 / 인사이트

1. **pg_restore 버전 호환성**
   - 덤프를 만든 PG 버전의 pg_restore를 사용해야 함
   - macOS에서 여러 PG 버전 공존 시 `/opt/homebrew/opt/postgresql@17/bin/` 경로 직접 지정

2. **TimescaleDB 덤프 복원 순서**
   - DB 생성 → `CREATE EXTENSION timescaledb` → pg_restore (이 순서 필수)
   - `--clean` 옵션 사용 시 Hypertable 관련 에러 발생할 수 있음

3. **데이터 검증은 적재 직후 필수**
   - 적재 후 바로 검증하지 않으면 문제를 늦게 발견
   - NULL 비율, 논리 정합성, 원본 대조를 체계적으로 수행

### 📌 다음 작업

1. 94개 ETF 시계열 데이터 수집 및 적재
2. Phase 2 진행 (API 연동, ETL 파이프라인)

---

## 2026-02-19 (수) - 전체 시장 데이터 적재 완료 🎉

### ✅ 완료 작업

1. **종목 마스터 데이터 업데이트**
   - `load_stock_master.py` 수정: 3개 시트 (KOSPI, KOSDAQ, ETF) 읽기
   - `market` 컬럼 추가 (시장 구분)
   - 총 3,820개 종목 적재 완료
     - KOSPI: 946개
     - KOSDAQ: 1,802개
     - ETF: 1,072개

2. **CSV 데이터 검증**
   - `raw_data/temp/` 폴더의 11개 CSV 파일 구조 확인
   - Pivot 형식: 행=날짜(1,009개), 열=종목명(3,749개)
   - 샘플 종목 (동화약품, 삼성전자) 데이터 검증 → 기존 DB 데이터와 완벽 일치 확인

3. **전체 시장 데이터 적재**
   - **기존 테스트 데이터 삭제**: KOSPI only 데이터 (933,544건) 삭제
   - **데이터 로딩 스크립트 작성**: `scripts/load_all_data_from_csv.py`
     - 11개 CSV 파일 → 3개 테이블 매핑
     - Pivot 형식 → 정규화 형식 변환
     - 종목명 → 종목코드 자동 매핑

4. **성능 최적화 (300배 속도 향상!)**
   - **문제**: 첫 시도에서 첫 CSV 파일만 30분+ 소요
   - **원인**: `df.iterrows()` 사용 (3,750 컬럼 × 1,009 행)
   - **해결**: `df.melt()` vectorized 연산으로 변경
   - **결과**: 각 CSV 파일 5-7초로 단축 (300배 이상 빠름)

5. **최종 데이터 적재 완료** (총 소요: 60.8분)
   - **ohlcv_daily**: 3,257,951건
   - **market_cap_daily**: 3,257,951건
   - **investor_trading**: 13,031,804건 (4개 타입)
     - FOREIGN (외국인): 3,257,951건
     - INSTITUTION (기관계): 3,257,951건
     - PENSION (연기금): 3,257,951건 ✨ API에서 불가능한 데이터!
     - RETAIL (개인): 3,257,951건
   - **총 19,547,706건**의 시계열 데이터
   - **기간**: 2022-01-03 ~ 2026-02-13 (약 4년)
   - **종목**: 3,726개 (매칭 성공) / 3,749개 (CSV 전체)

6. **데이터 검증 완료**
   - 모든 테이블 레코드 수 확인
   - 날짜 범위 확인
   - 투자자 타입별 분포 확인
   - DBeaver 설치하여 GUI로 데이터 확인

### 🎯 주요 결정사항

#### 1. PRIMARY KEY 추가 (ON CONFLICT 지원)
- **문제**: 초기 스크립트에서 `ON CONFLICT` 에러 발생
- **원인**: Hypertable에 PRIMARY KEY 설정 안 됨
- **해결**: 3개 테이블에 PRIMARY KEY 추가
  ```sql
  ALTER TABLE ohlcv_daily ADD PRIMARY KEY (time, stock_code);
  ALTER TABLE market_cap_daily ADD PRIMARY KEY (time, stock_code);
  ALTER TABLE investor_trading ADD PRIMARY KEY (time, stock_code, investor_type);
  ```

#### 2. 스크립트 최적화 전략
- **배경**: 첫 실행에서 30분+ 소요 → 완료 불가능 판단
- **선택지**:
  - A: 그대로 두고 1-2시간 기다리기
  - B: 프로세스 중단하고 최적화 후 재실행
- **결정**: **B (즉시 최적화)**
- **이유**:
  - 11개 파일 × 30분 = 5.5시간 예상 (비현실적)
  - `df.iterrows()` → `df.melt()` 변경만으로 300배 개선 가능
  - 최적화 시간 10분 vs 절약 시간 5시간
  - 앞으로도 이 스크립트 재사용 가능

#### 3. 데이터 검증 방식
- **방식**: 전체 로드 전 샘플 데이터로 검증
- **검증 항목**:
  - CSV와 기존 DB 데이터 비교 (동화약품, 삼성전자)
  - 모든 데이터 타입 확인 (OHLCV, 시가총액, 4개 투자자 유형)
  - 값 일치 확인 → 100% 일치
- **효과**: 전체 로드 전 신뢰성 확보

### 💡 배운 점 / 인사이트

1. **Pandas 성능 최적화의 중요성**
   - `df.iterrows()`: O(rows × cols) → 매우 느림
   - `df.melt()`: vectorized 연산 → 300배 빠름
   - Wide DataFrame(3,750 컬럼)에서는 vectorized 연산 필수

2. **Pivot 데이터 → 정규화 변환**
   - Pivot 형식: 저장 효율 좋음, 사람이 보기 편함
   - 정규화 형식: 데이터베이스에 적합, 쿼리 효율 좋음
   - `pd.melt()` 함수로 간단히 변환 가능

3. **종목 매핑 전략**
   - stocks 테이블(3,820개) vs CSV 파일(3,749개) 차이 존재
   - 이유: stocks는 현재 상장 종목, CSV는 과거 데이터 포함
   - 매칭되지 않는 23개 종목은 상장폐지 또는 신규 상장

4. **TimescaleDB INSERT 성능**
   - 3.26M건 삽입: 약 9분 (36만건/분)
   - 13M건 삽입: 약 41분 (32만건/분)
   - Hypertable의 자동 파티셔닝으로 일반 PostgreSQL보다 빠름

5. **데이터 수집 전략의 효율성**
   - API 호출 (실시간): 느림, 비용 발생, 제한 있음
   - CSV 파일 (배치): 빠름, 비용 없음, 대량 처리 가능
   - 초기 데이터는 CSV로, 일별 업데이트는 API로 전략 수립

### ⚠️ 주의사항

1. **미매칭 종목 23개**
   - CSV 파일에 있지만 stocks 테이블에 없는 종목
   - 원인: 상장폐지 또는 최근 IPO
   - 조치: 필요시 stocks 테이블 업데이트 또는 CSV 필터링

2. **데이터 기간**
   - 현재: 2022-01-03 ~ 2026-02-13 (약 4년)
   - 최신 데이터: 2026-02-13 (6일 전)
   - 향후: API 연동으로 최신 데이터 업데이트 필요

3. **스토리지 사용량**
   - 19.5M 레코드 = 약 2-3GB 예상
   - 압축 정책 적용시 80% 절감 가능
   - 분봉 데이터 추가시 대폭 증가 예상

4. **백업 및 복구**
   - 현재 백업 전략 없음
   - pg_dump 또는 TimescaleDB continuous backup 고려 필요

### 🐛 발견된 이슈 및 해결

#### 이슈 1: ON CONFLICT 에러
- **증상**: `there is no unique or exclusion constraint matching the ON CONFLICT specification`
- **원인**: Hypertable에 PRIMARY KEY 미설정
- **해결**: 3개 테이블에 PRIMARY KEY 추가
- **상태**: ✅ 해결 완료

#### 이슈 2: 첫 시도 성능 문제
- **증상**: 첫 CSV 파일 읽기에 30분+ 소요
- **원인**: `df.iterrows()` 사용 (비효율적)
- **해결**: `df.melt()` vectorized 연산으로 변경
- **결과**: 5-7초로 단축 (300배 개선)
- **상태**: ✅ 해결 완료

#### 이슈 3: 투자자 데이터 삽입 시간
- **증상**: 13M건 삽입에 41분 소요
- **원인**: 데이터량이 많음 (정상)
- **해결**: 배치 삽입 유지, 향후 bulk insert 최적화 고려
- **상태**: 정상 (최적화 여지 있음)

### 📌 다음 작업 (집 컴퓨터에서 이어서)

**우선순위 1**: API 연동 준비
1. 인포맥스 API 문서 검토
2. `collectors/infomax.py` 스켈레톤 작성
3. 일별 업데이트 스크립트 설계

**우선순위 2**: 데이터 품질 관리
1. 데이터 검증 스크립트 작성
2. 일별 품질 리포트 생성
3. 이상치 탐지 로직

**우선순위 3**: 백업 전략
1. pg_dump 자동화 스크립트
2. 백업 보관 정책 수립
3. 복구 테스트

**참고 문서**:
- `TODO.md` → Phase 2/3 섹션
- `SETUP.md` → 데이터 수집 가이드

---

## 2026-02-20 (금) - 수급거래량 적재, FnGuide 크롤링, 데이터 정비

### ✅ 완료 작업

1. **investor_trading net_buy_volume 전부 채움**
   - 기존 상태: net_buy_volume 전부 NULL (13,031,804건)
   - 13~16번 CSV (순매수거래량 외인/기관계/연기금/개인) 로드
   - `scripts/load_net_buy_volume.py` 작성 (COPY → temp table → UPDATE 방식)
   - 결과: 4개 투자자 타입 × 3,257,951건 전부 채움 (NULL 0건)
   - investor_trading 테이블 완비: net_buy_value(거래대금) + net_buy_volume(거래량) 모두 완성

2. **FnGuide 웹 크롤링 - 발행주식수/유동주식수/유동비율**
   - Infomax API에 유동주식수/유동비율 데이터 없음 확인 → FnGuide 크롤링으로 대체
   - URL: `https://comp.fnguide.com/SVO2/asp/SVD_Main.asp?pGB=1&gicode=A{code}&NewMenuID=101&stkGb=701`
   - `scripts/crawl_floating_shares.py` 작성 (BeautifulSoup4 + lxml)
   - 2026-02-19 기준 KOSPI/KOSDAQ 2,748개 종목 크롤링
   - 결과: 2,635개 성공 / 113개 데이터없음(우선주 등) / 차단 0회
   - floating_ratio: FnGuide 사이트 값 우선 (지수산정주식수 기준), 없으면 직접 계산
   - floating_shares 테이블에 base_date=2026-02-19 레코드 2,635건 저장 (비율 포함 2,608건)

3. **floating_ratio NULL 원인 파악**
   - 191개 종목, 9,551레코드에서 floating_ratio=NULL (base_date < 2026-02-19)
   - 원인: 기존 xlsx의 유동주식수 > 12_발행주식수.csv의 발행주식수 (유상증자 등 미반영)
   - 예: 코다코(046070) 유동 42M > 발행 1M (40배), 스튜디오산타클로스 30배 등
   - 처리: 과거 데이터는 NULL 그대로 유지, 2026-02-19부터 FnGuide 크롤링 데이터로 정상화

4. **차단 감지 로직 개선 (크롤러)**
   - 초기: 짧은 응답(50자) → 차단으로 오인하여 스크립트 중단 오류 발생
   - 수정: 짧은 응답 = 우선주 등 데이터없는 종목으로 처리, 실제 차단 키워드만 차단 감지
   - 연속 15건 이상 데이터없음 → 90초 대기 후 재시도 로직 추가

### 🎯 주요 결정사항

#### 1. 유동주식수/유동비율 업데이트 주기: 월 1~2회
- **배경**: FnGuide 크롤링은 2,748종목 기준 약 23분 소요
- **이유**: 유동주식수/비율은 자주 변하는 데이터가 아님
- **방식**: `scripts/crawl_floating_shares.py` 수동 실행 (월 1~2회)

#### 2. floating_ratio 값 기준: FnGuide 사이트 값 우선
- **배경**: FnGuide 비율 분모 = 지수산정주식수 (발행주식수보통주와 다름)
- **결정**: 사이트 값이 산업 표준이므로 우선 사용, 없을 경우 직접 계산(유동/발행)
- **비율불일치 104건**: 오류 아님, 분모 차이로 인한 구조적 차이

#### 3. 과거 floating_ratio NULL 데이터 처리
- **결정**: 과거 데이터(~2026-02-18)는 NULL 그대로 유지
- **이유**: 올바른 과거 발행주식수 데이터 없으며, 사이트는 최근값만 제공
- **이후**: 2026-02-19부터 FnGuide 크롤링 데이터로 정상 업데이트 시작

### 💡 배운 점 / 인사이트

1. **FnGuide 유동비율 분모 = 지수산정주식수**
   - 유동주식수 / 지수산정주식수 × 100 (한국거래소 KOSPI 지수 산정 기준)
   - 발행주식수(보통주)와 다름: 우선주 등 포함 여부 차이
   - SK하이닉스: 사이트 73.77% vs 직접계산 75.35% 차이 발생
   - 우선주 없는 종목(삼성전자, NAVER 등)은 두 값이 일치

2. **웹 크롤링 차단 감지 전략**
   - 특정 종목 페이지 없음(50자 응답) ≠ IP 차단
   - 실제 차단은 응답 본문에 키워드("access denied", "captcha" 등) 존재
   - 연속 데이터없음 패턴으로 감지하는 것이 더 신뢰성 높음

3. **COPY + UPDATE 패턴 (대량 UPDATE 최적화)**
   - UPDATE 직접 실행 대신 임시 테이블 COPY 후 JOIN UPDATE
   - 3.27M건 업데이트를 투자자 타입별 수분 내 완료

### ⚠️ 주의사항

1. **우선주 종목 (예: 000087 하이트진로2우B)**
   - FnGuide에 페이지 없음 → 크롤링 결과 없음 (정상)
   - floating_shares에 데이터 없어도 무관 (유동주식수 개념이 우선주에는 부적합)

2. **floating_ratio NULL 191종목 (과거 데이터)**
   - 해결하지 않은 채로 유지 (향후 레거시 데이터로 취급)
   - 2026-02-19 이후 데이터는 정상값

### 📌 다음 작업

1. Infomax API로 일별 업데이트 파이프라인 구축
   - ohlcv_daily, market_cap_daily: `/api/stock/hist`
   - investor_trading: `/api/stock/investor`
   - stocks 마스터: `/api/stock/code`, `/api/stock/expired`
2. 94개 누락 ETF 시계열 데이터 보충 (`/api/stock/hist`)
3. APScheduler 일별 자동화 (Phase 3)

---

## 2026-02-20 (금) - 일별 업데이트 파이프라인 구축 + 멀티스레드 병렬화

### ✅ 완료 작업

1. **Infomax API 수집기 구현** (`collectors/infomax.py`)
   - `InfomaxClient` 클래스 (thread-safe 공유 rate limiter)
   - `get_hist()`: OHLCV + 발행주식수 조회 → ohlcv_daily, market_cap_daily
   - `get_investor()`: 4개 투자자 타입 수급 조회 → investor_trading
   - Rate limit: 1.05초/요청 (60회/분 Lite 플랜)
   - `_rate_lock` + `_rate_last_call` 클래스 변수로 멀티스레드 공유 rate limiter 구현

2. **일별 업데이트 스크립트 구현** (`scripts/daily_update.py`, 624줄)
   - DB 마지막 날짜 기준 자동 범위 계산 (start: MAX(time)+1, end: 어제)
   - STEP 1: OHLCV + 시가총액 수집 (전 종목 3,820개)
   - STEP 2: 투자자별 수급 수집 (KOSPI+KOSDAQ 2,748개)
   - 특이사항 자동 감지: 거래정지, OHLCV오류, 가격급등락(±29.5%), 대규모순매수도(500억↑)
   - 보고서 자동 생성: `reports/daily_update_YYYYMMDD.txt`
   - 단독 실행: `python scripts/daily_update.py [YYYYMMDD]`

3. **일별 스케줄러 구현** (`schedulers/daily_scheduler.py`)
   - APScheduler 기반 매일 16:30 KST 자동 실행
   - 로그: `logs/scheduler.log`
   - ⚠️ `next_run_time` AttributeError 버그 있음 → 수정 필요

4. **일별 업데이트 속도 분석**
   - 총 API 호출: OHLCV 3,820번 + 수급 2,748번 = **6,568번**
   - Rate Limit 60회/분 → 이론 최소 소요: **약 1시간 50분** (실제 약 2시간)
   - 병목: API Rate Limit (코드 개선으로 뚫을 수 없는 한계)

5. **멀티스레드 병렬화 적용**
   - `ThreadPoolExecutor(max_workers=4)` — STEP1, STEP2 각각 적용
   - DB 쓰기는 메인 스레드에서만 처리 (psycopg2 thread-safety 고려)
   - 실제 효과: API latency 0.1초로 짧아 개선 폭 미미 (~10%)
   - 공유 rate limiter 덕분에 rate limit 초과 없음 (독립 rate limiter는 access_limit 에러 확인됨)

6. **Bulk API 탐색** (근본적 속도 개선 가능성 조사)
   - `/api/market/*`, `/api/date/*`, `/api/stock/hist?market=KOSPI` 등 탐색
   - 결과: **날짜별 전 종목 bulk API 없음** (종목별 호출만 지원)

### 🎯 주요 결정사항

#### 1. 일별 업데이트 2시간 수용
- Rate Limit이 근본 한계, 코드로 해결 불가
- 16:30 실행 → 18:30 완료 → 사용에 지장 없음
- 속도 개선: Infomax 플랜 업그레이드만이 해결책

#### 2. 멀티스레드 구조 유지
- 현재 효과는 미미하지만 플랜 업그레이드 시 즉시 활용 가능
- DB 쓰기를 메인 스레드에서만 처리하여 thread-safe 보장

### ⚠️ 주의사항

1. **독립 rate limiter 사용 금지**: 스레드별 독립 throttle 사용 시 access_limit 에러 발생 확인됨
2. **APScheduler next_run_time 버그**: `daily_scheduler.py` 실행 시 AttributeError 발생, 수정 필요

### 📌 다음 작업

1. `daily_scheduler.py` next_run_time 버그 수정 후 실제 실행 테스트
2. 94개 ETF 시계열 데이터 보충
3. 서버 구축 (맥미니 구매 후 설정)

---

## 템플릿 (작업 완료시 아래 형식으로 추가)

```markdown
## YYYY-MM-DD (요일) - 작업 제목

### ✅ 완료 작업
- 항목 1
- 항목 2

### 🎯 주요 결정사항
- 결정 내용 및 이유

### 💡 배운 점 / 인사이트
- 배운 내용

### ⚠️ 주의사항
- 주의할 점

### 🐛 발견된 이슈
- 이슈 설명 및 해결 방법 (또는 미해결)

### 📌 다음 작업
- 다음 할 일
```

---

## 작성 가이드

- **매일 작업 종료시 업데이트**하여 다음 날 빠른 컨텍스트 복원
- **중요한 결정사항**은 반드시 이유와 함께 기록
- **막혔던 문제와 해결 방법** 상세 기록 (미래의 자신을 위해)
- **코드 변경보다 "왜"에 집중**
- 새 환경에서 작업 시작시 **최근 3개 항목만 읽어도 충분**하도록 작성
