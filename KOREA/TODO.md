# 📝 TODO - 작업 목록

> **마지막 업데이트**: 2026-02-20
> **현재 Phase**: Phase 1 완료 ✅ → Phase 2 API 연동 준비 중

---

## 🔥 긴급/중요 (최우선)

- [ ] **`daily_scheduler.py` next_run_time 버그 수정 후 실제 실행 테스트**
- [ ] **94개 ETF 시계열 데이터 보충** (API `/api/stock/hist` 활용)
  - 조회 SQL: `SELECT stock_code, stock_name FROM stocks s LEFT JOIN (SELECT DISTINCT stock_code FROM ohlcv_daily) o ON s.stock_code = o.stock_code WHERE o.stock_code IS NULL;`
- [ ] **서버 구축** (맥미니 구매 후 설정)

---

## ✅ Phase 1: 기반 구축 (완료)

### 문서화 ✅ 완료

- [x] PROJECT_MASTER.md 작성
- [x] PROJECT_ANALYSIS.md 작성
- [x] README.md 작성
- [x] SETUP_GUIDE.md 작성
- [x] TODO.md 작성 (이 파일)
- [x] DEVELOPMENT_LOG.md 작성
- [x] .gitignore 작성
- [x] .env.example 작성

### 개발 환경 설정 ✅ 완료

- [x] **PostgreSQL + TimescaleDB 설치**
  - [x] PostgreSQL 17 설치 (17.8)
  - [x] TimescaleDB 설치 (2.25.0)
  - [x] 서비스 시작
  - [x] TimescaleDB 튜닝
  - [x] 연결 확인 ✅

- [x] **데이터베이스 생성**
  - [x] DB 생성: `korea_stock_data`
  - [x] TimescaleDB 확장 활성화
  - [x] 확인 완료 ✅

- [x] **Python 환경 설정**
  - [x] Python 3.14.3 확인
  - [x] 가상환경 생성
  - [x] 가상환경 활성화
  - [x] 핵심 의존성 설치 (37개 패키지)
  - [x] requirements.txt 생성 ✅

- [x] **환경변수 설정**
  - [x] .env 파일 생성
  - [x] DB 연결 정보 입력
  - [x] .gitignore 확인 ✅

- [x] **프로젝트 구조 생성**
  - [x] 전체 폴더 구조 생성
  - [x] `__init__.py` 파일 생성 ✅

### 데이터베이스 스키마 생성 ✅ 완료

- [x] **스키마 SQL 파일 작성**
  - [x] `database/schema/init_schema.sql` 생성
  - [x] 10개 테이블 정의 (메타 5개, 시계열 3개, 모니터링 2개)
  - [x] 인덱스 정의 ✅

- [x] **스키마 적용**
  - [x] SQL 파일 실행 완료
  - [x] 10개 테이블 생성 확인
  - [x] 3개 Hypertable 확인 (ohlcv_daily, market_cap_daily, investor_trading) ✅

⚠️ **중요**: 현재 스키마는 "일반적인 주식 데이터 구조" 가정
- 실제 인포맥스 API/HTS 데이터 확인 후 조정 필요 (윈도우 환경)

### 핵심 모듈 개발 ✅ 완료

- [x] **config/settings.py**
  - [x] Pydantic Settings로 환경변수 로드
  - [x] DB 연결 URL 생성
  - [x] 테스트 완료 ✅

- [x] **database/connection.py**
  - [x] SQLAlchemy Engine 생성
  - [x] Connection Pool 설정
  - [x] Session Factory (컨텍스트 매니저)
  - [x] `test_connection()` 함수
  - [x] 테스트 완료 ✅

- [x] **utils/logger.py**
  - [x] Loguru 설정
  - [x] 파일 로깅 (logs/ 폴더)
  - [x] 콘솔 + 파일 출력
  - [x] 에러 로그 별도 저장 ✅

- [x] **utils/exceptions.py**
  - [x] 커스텀 예외 클래스 정의
  - [x] DataCollectionError, ValidationError 등 ✅

- [x] **database/models.py** ✅ 완료 (2026-02-18)
  - [x] SQLAlchemy ORM 모델 10개 정의 완료
  - [x] Stock, Sector, IndexComponent, FloatingShares, ETFPortfolios
  - [x] MarketCapDaily, OHLCVDaily, InvestorTrading (Hypertable)
  - [x] DataCollectionLogs, DataQualityChecks
  - ⚠️ 실제 API 데이터 확인 후 조정 필요

### 테스트 및 검증 ✅ 완료

- [x] **DB 연결 테스트**
  - [x] PostgreSQL 17.8 연결 성공
  - [x] TimescaleDB 2.25.0 확인
  - [x] 3개 Hypertable 확인 ✅

- [x] **설정 및 로깅 테스트**
  - [x] settings.py 동작 확인
  - [x] logger.py 동작 확인 ✅

**✅ Phase 1 완료 기준 달성!**

---

## 📦 Phase 2: 데이터 수집기 개발 (부분 완료 ✅)

### 🎉 완료된 작업

- [x] **종목 마스터 데이터 적재** ✅ (2026-02-19)
  - [x] stocks 테이블에 market 컬럼 추가
  - [x] 3개 시트 (KOSPI, KOSDAQ, ETF) 읽기
  - [x] 3,820개 종목 적재 완료

- [x] **전체 시장 데이터 적재** ✅ (2026-02-19)
  - [x] CSV 파일 (raw_data/temp/) 구조 확인
  - [x] 샘플 데이터 검증 완료
  - [x] 기존 테스트 데이터 삭제
  - [x] `scripts/load_all_data_from_csv.py` 작성
  - [x] 성능 최적화 (df.melt 사용, 300배 향상)
  - [x] 전체 데이터 적재 완료 (19.5M 레코드, 60.8분)
  - [x] 데이터 검증 완료

- [x] **데이터베이스 스키마 조정** ✅ (2026-02-19)
  - [x] PRIMARY KEY 추가 (3개 Hypertable)
  - [x] ON CONFLICT 지원 확인

- [x] **데이터 검증 및 스키마 정리** ✅ (2026-02-19)
  - [x] 전체 데이터 품질 검증 (NULL, 음수, 논리 정합성)
  - [x] OHLCV 스팟체크: CSV 원본과 DB 값 100% 일치 확인
  - [x] 거래일 연속성 검증 (공휴일/연휴 갭만 확인)
  - [x] 종목명 매핑 정확성 검증 (중복 없음, 3,726개 정확 매칭)
  - [x] 불필요 컬럼 삭제 (shares_outstanding, volume 관련 5개)
  - [x] investor_trading → net_buy_value만 유지

- [x] **유동주식수 데이터 적재** ✅ (2026-02-19)
  - [x] xlsx 3개 파일 (KOSPI + KOSDAQ 2개) 읽기
  - [x] 1,052,045건 적재 (2,546개 종목)
  - [x] 기간: 2022-01-03 ~ 2026-02-19

- [x] **수급 순매수거래량(net_buy_volume) 적재** ✅ (2026-02-20)
  - [x] 13~16번 CSV (외인/기관계/연기금/개인 순매수거래량)
  - [x] `scripts/load_net_buy_volume.py` 작성
  - [x] 4종류 × 3,257,951건 전부 채움 (NULL 0건)

- [x] **FnGuide 웹 크롤링 - 발행주식수/유동주식수/유동비율** ✅ (2026-02-20)
  - [x] `scripts/crawl_floating_shares.py` 작성
  - [x] 2026-02-19 기준 2,635개 종목 저장 (차단 0회)
  - [x] floating_ratio: FnGuide 사이트 값 우선
  - [x] 업데이트 주기: 월 1~2회 수동 실행으로 결정

### 🔄 진행 중 / 대기 중

### 맥 환경에서 진행 (윈도우 작업 후)

- [x] **인포맥스 API 연동** ✅ (2026-02-20)
  - [x] `collectors/infomax.py` 작성 (thread-safe InfomaxClient)
  - [x] `get_hist()` — OHLCV + 시가총액
  - [x] `get_investor()` — 투자자별 수급 (4개 타입)
  - [x] `scripts/daily_update.py` — 일별 업데이트 + 특이사항 감지 + 보고서 생성
  - [x] 멀티스레드 병렬화 (ThreadPoolExecutor, 공유 rate limiter)

- [x] **데이터 검증 로직** (부분 완료)
  - [x] `validators/schemas.py` (Pydantic 스키마) ✅ 완료 (2026-02-18)
    - [x] StockSchema
    - [x] OHLCVDailySchema
    - [x] InvestorTradingSchema
    - [x] 나머지 7개 스키마 모두 완료
    - ⚠️ 실제 API 데이터 확인 후 조정 필요
  - [ ] `validators/quality_checks.py`
    - [ ] NULL 체크
    - [ ] 중복 체크
    - [ ] 범위 체크 (가격, 거래량)
  - [ ] `validators/business_rules.py`
    - [ ] 날짜 검증 (주말/공휴일)
    - [ ] 연기금 = 기관 - 순수기관 로직

- [ ] **ETL 파이프라인**
  - [ ] `etl/extract.py` (데이터 추출)
  - [ ] `etl/transform.py` (데이터 변환)
  - [ ] `etl/load.py` (스테이징 → 프로덕션)
  - [ ] `etl/pipeline.py` (전체 오케스트레이션)
  - [ ] 에러 핸들링 및 재시도 로직
  - [ ] 데이터 수집 로그 기록 (data_collection_logs 테이블)

- [x] **테스트** (부분 완료)
  - [x] `tests/conftest.py` - pytest 설정 및 fixture ✅ (2026-02-18)
  - [x] `tests/test_validators/test_schemas.py` - Pydantic 스키마 테스트 ✅
  - [x] `tests/test_models/test_stock.py` - Stock 모델 테스트 ✅
  - [x] `tests/test_models/test_hypertables.py` - Hypertable 테스트 ✅
  - ⚠️ 테스트 일부 실패 (53개 중 26개 통과) - 실제 데이터 확인 후 수정
  - [ ] `tests/test_collectors/test_infomax.py`
  - [ ] `tests/test_etl/test_pipeline.py`
  - [ ] 실제 API 호출 테스트 (소량 데이터)

- [ ] **스크립트 작성**
  - [ ] `scripts/collect_stocks.py` (종목 마스터 수집 스크립트)
  - [ ] 수동 실행으로 데이터 수집 테스트

**완료 기준**: 수동으로 데이터 수집 → DB 저장 성공

---

## ⏰ Phase 3: 스케줄링 및 자동화 (진행 중)

- [x] **APScheduler 설정** ✅ (2026-02-20)
  - [x] `schedulers/daily_scheduler.py` 작성 (매일 16:30 KST)
  - ⚠️ `next_run_time` AttributeError 버그 → 수정 필요

- [ ] **실제 실행 테스트 및 안정화**
  - [ ] `daily_scheduler.py` 버그 수정
  - [ ] 1주일 이상 무인 자동 수집 확인

- [ ] **모니터링**
  - [ ] `scripts/check_collection_status.py` (상태 확인 스크립트)

**완료 기준**: 1주일 이상 무인 자동 수집 성공

---

## 🛡️ Phase 4: 데이터 품질 및 백업 (예정)

- [ ] **데이터 품질 체크**
  - [ ] 일별 품질 리포트 생성
  - [ ] `scripts/data_quality_report.py`
  - [ ] 이상 탐지 알림

- [ ] **백업 전략**
  - [ ] PostgreSQL 일별 백업 스크립트
  - [ ] 백업 보관 정책 (7일)
  - [ ] 복구 테스트

- [ ] **과거 데이터 백필**
  - [ ] `scripts/backfill_data.py`
  - [ ] 과거 1년치 데이터 수집

**완료 기준**: 백업 자동화, 1년치 데이터 확보

---

## 🌐 Phase 5: 인터페이스 개발 (예정)

- [ ] **Python 라이브러리**
  - [ ] SQLAlchemy 모델 export
  - [ ] 헬퍼 함수 제공

- [ ] **FastAPI (선택)**
  - [ ] `api/main.py`
  - [ ] CRUD 엔드포인트
  - [ ] Swagger 문서

- [ ] **접근 권한 관리**
  - [ ] Read-only 사용자 생성

- [ ] **사용 가이드**
  - [ ] 다른 프로젝트 연동 예시

**완료 기준**: 수급 분석 프로젝트에서 사용 성공

---

## 🚀 Phase 6: 확장 및 최적화 (예정)

- [ ] **분봉 데이터**
  - [ ] ohlcv_minute 테이블
  - [ ] 실시간 수집기
  - [ ] 압축 정책

- [ ] **추가 데이터 소스**
  - [ ] 증권사 HTS API
  - [ ] 웹 크롤링

- [ ] **성능 최적화**
  - [ ] 인덱스 튜닝
  - [ ] 쿼리 최적화
  - [ ] 캐싱 (Redis)

**완료 기준**: 분봉 데이터 안정적 수집

---

## 🐛 버그 및 이슈

(발견되는 대로 추가)

---

## 💡 아이디어 / 향후 고려사항

- [ ] Grafana 대시보드 (데이터 모니터링)
- [ ] 데이터 품질 알림 (Slack, 이메일)
- [ ] 데이터 버전 관리 (Audit Log)
- [ ] Docker Compose로 전체 스택 패키징
- [ ] CI/CD 파이프라인 (GitHub Actions)

---

## 📌 참고

- 긴급/중요한 작업은 맨 위 섹션으로 이동
- 작업 완료시 `[x]` 체크
- 새로운 Phase 시작시 해당 섹션 확장
- 막히는 부분은 **DEVELOPMENT_LOG.md**에 기록
