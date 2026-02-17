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
