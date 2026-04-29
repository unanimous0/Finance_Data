# 📝 TODO - 작업 목록

> **마지막 업데이트**: 2026-04-30
> **현재 Phase**: Phase 4 완료 + foreign_ownership + **Phase 5 배당 시스템 + LENS 연동 완료** / 주식 2026-04-28 / 배당 2026-04-29

---

## 🆕 2026-04 신규/완료 (배당 시스템)

- [x] **배당(Dividends) DB 스키마 + ORM** ✅ (2026-04-25)
  - `database/schema/dividends_schema.sql` (정관변경 시대 대응 — A/B 그룹, version, 5종 날짜)
  - `database/models.py`: `Dividend` 클래스 추가
- [x] **DART API 수집기** ✅ (2026-04-26~28)
  - `collectors/dart.py`: rate 60/min, 페이지네이션, 인코딩 자동 감지(UTF-8/CP949), 자회사 공시 차단
- [x] **배당 백필 (2022~현재)** ✅ (2026-04-28~29)
  - `scripts/backfill_dividends.py`: 일별 chunks (DART 5,000건 한도 회피)
  - 디스크 캐시 1.3GB (cache/dart/, gitignore)
  - 6,922건 / 1,465종목 적재
- [x] **정관변경 분류** ✅ (2026-04-28)
  - `scripts/classify_charter_groups.py` + `verify_charter_groups.py`
  - A 266 / B 289 / NULL 28 (record_date 휴리스틱과 cross-check)
- [x] **자회사 misattribution 정리** ✅ (2026-04-29~30)
  - 콜마홀딩스→연우 같은 케이스 발견
  - report_nm + 본문 키워드 이중 필터로 954건 retroactive 제거
- [x] **LENS export** ✅ (2026-04-26)
  - `scripts/export_dividends.py`: 14필드 + revisions 임베드, 원자적 tmp→rename
  - 합의된 contract: `/home/una0/projects/LENS/data/dividends.json`
- [x] **daily_update.py 통합** ✅ (2026-04-27)
  - `run_dividend_pipeline()` — 자동 갭 backfill + ex_date 자동 정확화 + LENS export
- [x] **한국 시장 영업일 정확도** ✅ (2026-04-29)
  - `holidays.KR` + 근로자의 날 5/1 (거래소 휴장)
  - `refresh_future_ex_dates()` — ohlcv 채워지면 미래 ex_date 자동 정확화
- [x] **yield_pct NULL 보정** ✅ (2026-04-30)
  - DART 미공시 케이스 (378건) → ohlcv 종가 기반 recompute → 366건 채움 (12건 상폐 잔여)
- [x] **종목명(corp_name) fallback** ✅ (2026-04-29)
  - dividends.corp_name 컬럼 추가 + cache backfill
  - export 시 COALESCE(stocks.stock_name, corp_name) → name NULL 0건

---

## 🔥 긴급/중요 (최우선)

- [x] **97개 신규 ETF 시계열 데이터 보충** ✅ (2026-02-24)
  - 94개: 2/19 신규 상장 → 과거 데이터 없음 (정상, 엑셀 파일 수령 후 처리 예정)
  - 3개(01669A/01777A/01221D): 2022-01-13~2026-02-19 OHLCV 삽입 완료
- [x] **2026-02-20 데이터 수동 수집** ✅ (2026-02-23): OHLCV 3,822건 / 수급 2,747종목, 실패 0건
- [x] **2026-02-23 데이터 수집** ✅ (2026-02-24 오후): OHLCV 3,821건 / 수급 2,747건, 실패 1건
- [x] **2026-02-24 데이터 수집** ✅ (2026-02-25): OHLCV 3,791건 / 수급 2,720건, 실패 1건 (481200)
- [x] **2026-02-25 데이터 수집** ✅ (2026-02-26): OHLCV 3,791건 / 수급 2,720건, 실패 4건 (신규상장당일)
  - 신규 상장 4개 자동 추가: 0155N0, 0162L0, 0162M0, 0162Z0 (당일 데이터 없음 → 정상)
- [x] **2026-02-26 데이터 수집** ✅ (2026-02-27): OHLCV 3,795건 / 수급 2,720건, 실패 0건
- [x] **DB 복원** ✅ (2026-03-01): backup_20260227_2331.dump (187MB) → dropdb/createdb/pg_restore, 2/26까지 정상
- [x] **`scripts/restore_db.sh` 작성** ✅ (2026-03-03)
  - TimescaleDB pg_restore 후 hypertable 유니크 인덱스 자동 재생성
  - `bash scripts/restore_db.sh <dump>` 1줄로 복원 완료
- [x] **FnGuide FICS 업종 크롤링 구축** ✅ (2026-03-02)
  - `stock_sectors` 테이블 생성 (stock_code PK, fics_sector, updated_at)
  - `scripts/crawl_sector.py` 작성 (2,720개 KOSPI+KOSDAQ, 약 70분 소요)
  - `schedulers/daily_scheduler.py`: 분기별 잡 추가 (1/4/7/10월 첫 번째 일요일 03:30)
  - 초기 전체 수집 완료: 섹터 확인 2,607개 / NULL 113개 (우선주·스팩 등)
  - 인코딩 버그 수정: `resp.content` → `resp.text` (bs4 잘못된 인코딩 감지 문제)
  - `korea_stock_reader` 읽기 권한 부여 (GRANT SELECT + DEFAULT PRIVILEGES)
  - 중간 샘플 체크 추가 (crawl_sector.py + daily_update.py)
- [x] **2/27~ 누락 데이터 수집** ✅ (2026-03-04)
  - 2/27: OHLCV 3,794건 / 수급 10,868건 / 상장폐지 455910 처리
  - 3/2: 대체공휴일(휴장) — 수집 불필요
  - 3/3: OHLCV 3,794건 / 수급 10,876건 / 품질체크 이상없음
- [x] **삼성전자(005930) 2026-02-26 데이터 오류 수정** ✅ (2026-03-04)
  - 종가 50,500원 → 218,000원 / market_cap_daily도 동시 수정
- [x] **전체 데이터 품질 전수 검토** ✅ (2026-03-04)
  - OHLCV 논리 오류 0건, 수급 극단값 0건
  - 이상 2건(폰드그룹·PLUS인버스2X) 발견 → 냅두기로 결정
- [x] **DB 복원 (backup_20260303_2247.dump)** ✅ (2026-03-04)
- [x] **DB 정합성 정비** ✅ (2026-02-24)
  - investor_trading ETF 데이터 2,925,544건 삭제
  - EN/MF/RT/IF/DR/SW/SR/EW/BC/FS 기타 유형 30개 종목 삭제
  - get_stock_codes() 단순화: KOSPI/KOSDAQ/ETF 3가지만 수집
- [x] **주가이벤트의심 탐지 추가** ✅ (2026-02-24)
  - ±30% 초과 변화 = 이벤트 확정 (한국 가격제한 초과 = 정상 거래 불가)
  - 보고서 맨 앞 🚨 경고박스 + 수정계수 확인 필요 안내
  - 상장폐지는 sync_stock_master()가 이미 자동 처리 (pykrx 추가 불필요)
- [x] **481200(SOL 미국테크TOP10인버스) is_active=FALSE 처리** ✅ (2026-02-25)
  - API code/expired 모두 미등재, 2/23·2/24 데이터 없음, 2/20 거래량=0 → 청산 확정
  - delisting_date=2026-02-24 설정
- [x] **94개 ETF 과거 OHLCV 보충** ✅ (2026-02-25 재완료)
  - 2/24 xlsx 적재 시 실제로는 4건씩만 있었던 것으로 확인 (ON CONFLICT 스킵 오판)
  - 집 dump(backup_20260224_2357.dump)에서 2/19 이전 15,268건 추출 → 현재 DB UPSERT
  - ohlcv_daily: 3,249,045 → 3,264,313건 (+15,268건)
- [x] **DB 유니크 인덱스 정비** ✅ (2026-02-24)
  - UPSERT용 유니크 인덱스 3개 테이블 모두 누락 발견 → 생성 완료
  - `uq_ohlcv_time_stock` (time, stock_code)
  - `uq_mktcap_time_stock` (time, stock_code)
  - `uq_investor_time_stock_type` (time, stock_code, investor_type)
- [x] **프로젝트 파일 정리** ✅ (2026-02-24)
  - 삭제: `raw_data/temp/` (248MB), `raw_data/*.xlsx`, `.pytest_cache/`
  - 삭제: 미사용 코드 (`database/connection.py`, `utils/logger.py`, `utils/exceptions.py`)
  - 삭제: 구버전 스키마 (`init_schema.sql`, `alter_stocks_table.sql`)
- [x] **DB 복원 (backup_20260304.dump)** ✅ (2026-03-07): 3,279,487건, 최신 데이터 2026-03-03
- [x] **`scripts/restore_db.sh` --no-owner 추가** ✅ (2026-03-07): Windows 덤프 복원 시 `role "postgres" does not exist` 오류 제거
- [x] **리눅스 서버 Python 환경 세팅** ✅ (2026-03-09)
  - venv 생성 (`python3 -m venv venv`)
  - requirements.txt 설치 완료
  - `.env` 설정 완료 (DB_HOST=/var/run/postgresql, DB_USER=una0, peer 인증)
  - 인포맥스 API 연결 확인 (3,793개 종목 반환)
- [x] **3/4 ~ 3/10 누락 데이터 수집 완료** ✅ (2026-03-09~11)
  - 3/4~3/6: 2026-03-09 서버에서 수집 완료
  - 3/9: 2026-03-10 03:22~05:18 수집 완료 (3,795건)
  - 3/10: 2026-03-10 23:07~03-11 01:03 수집 완료 (3,798건, 신규 상장 3종목 자동 추가)
    - OHLCV 25건 API 일시 장애 → `--missing-only` 재수집으로 24건 복구 (472350만 미수집)
- [x] **3/11~3/16 데이터 수집 완료** ✅ (2026-03-11~16)
- [x] **3/17 데이터 수집 완료** ✅ (2026-03-17 21:21)
  - 신규 상장 5개 자동 감지: 0166N0, 0167A0, 0167B0, 0167Z0, 0168K0
  - 상장폐지 1개 자동 처리: 036180
- [x] **인포맥스 데이터 제공 시간 실험** ✅ (2026-03-17)
  - 18:10 KST 1차 수집: 29% 성공 → 데이터 불완전
  - 20:23 KST 2차 수집: ~100% 성공 → **19:00 이후 수집 정책 결정**
- [x] **`sync_stock_master()` ghost_delisted 추가** ✅ (2026-03-17)
  - 상장 API·상폐 API 모두 미등록 활성 종목 자동 비활성화 (ETF 청산 등)
  - 472350, 0106J0, 0120X0 비활성화 처리 완료
- [x] **불필요 파일 정리** ✅ (2026-03-17)
  - ERROR 보고서 3개 삭제, utils/ 폴더 삭제, docs/.gitkeep 삭제
- [x] **외국인 지분율(foreign_ownership) 수집 추가** ✅ (2026-03-24)
  - `foreign_ownership` 테이블 생성 (Hypertable)
  - `collectors/infomax.py`: `get_foreign()` 추가
  - `scripts/daily_update.py` STEP 3 통합 (ETF/SPAC 제외 ~2,642종목, 매일)
  - 백필: 2022-01-03~2026-03-20, 1,252,296건 완료 (`collect_foreign_ownership.py`)
- [x] **investor_trading 단위 버그 수정** ✅ (2026-03-24)
  - close_price ≥ 100,000원 종목에서 net_buy_value 1/1000 오류
  - `collectors/infomax.py`: 단위 자동감지 제거, 항상 × 1,000 고정
  - DB 교정 완료 (0.001x 버그 0건)
- [x] **UNIT_CHECK 품질 체크 추가** ✅ (2026-03-24)
  - `validators/quality_checks.py`: `check_investor_unit()` (역산단가 검증)
- [x] **3/18~3/23 데이터 수집 완료** ✅ (2026-03-24)
  - 3/23: 신규 상장 0166S0 자동 추가
- [x] **`get_missing_foreign_stocks()` LIKE 이스케이프 버그 수정** ✅ (2026-03-24)
  - psycopg2 파라미터 포함 SQL에서 `%%스팩%%` 이스케이프 필요
- [ ] **스케줄러 재가동**: tmux 세션으로 실행
  - 수집 시간 **19:00 이후**로 변경 (실험으로 확인: 18:40+ 2차 제공 후 99%+ 수집 가능)
  - daily_update.py 기본값이 "어제"까지만 수집 → 당일 수집 시 날짜 명시 필요
- [ ] **`scripts/collect_foreign_ownership.py` 삭제** (백필 완료, 불필요)
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

- [x] ~~**database/connection.py**~~ (2026-02-24 삭제: 미사용)

- [x] ~~**utils/logger.py**~~ (2026-02-24 삭제: 미사용)

- [x] ~~**utils/exceptions.py**~~ (2026-02-24 삭제: 미사용)

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
  - [x] `validators/quality_checks.py` ✅ (2026-02-22)
    - [x] NULL 체크
    - [x] 중복 체크
    - [x] OHLCV 논리 체크 (high>=low 등)
    - [x] 거래일 연속성 체크
    - [x] 수급 합산 검증

- [x] **테스트** (부분 완료)
  - [x] `tests/conftest.py` - pytest 설정 및 fixture ✅ (2026-02-18)
  - [x] `tests/test_validators/test_schemas.py` - Pydantic 스키마 테스트✅
  - [x] `tests/test_models/test_stock.py` - Stock 모델 테스트 ✅
  - [x] `tests/test_models/test_hypertables.py` - Hypertable 테스트 ✅

**완료 기준**: 수동으로 데이터 수집 → DB 저장 성공

---

## ⏰ Phase 3: 스케줄링 및 자동화 (진행 중)

- [x] **APScheduler 설정** ✅ (2026-02-20)
  - [x] `schedulers/daily_scheduler.py` 작성 (매일 16:30 KST)
  - [x] `next_run_time` AttributeError 버그 수정 (`CronTrigger.get_next_fire_time` 사용)

- [x] **2026-02-19 데이터 수집 및 재수집 모드 구현** ✅ (2026-02-20)
  - [x] 설 연휴 직후 API 데이터 지연 원인 분석
  - [x] `--missing-only` 재수집 모드 추가
  - [x] 3,820건(OHLCV) + 10,992건(수급) 100% 완성

- [x] **모니터링** ✅ (2026-02-22)
  - [x] `scripts/check_collection_status.py` 작성 완료
  - [x] `schedulers/daily_scheduler.py`: 매주 일요일 03:00 백업 추가
  - [x] `schedulers/daily_scheduler.py`: 분기별 FICS 섹터 크롤링 추가 (2026-03-02)

- [ ] **실제 실행 테스트 및 안정화**
  - [ ] 1주일 이상 무인 자동 수집 확인
  - [ ] 연휴 직후 재수집 루틴 점검

**완료 기준**: 1주일 이상 무인 자동 수집 성공

---

## 🛡️ Phase 4: 데이터 품질 및 백업 ✅ 완료 (2026-02-22)

- [x] **데이터 품질 체크** ✅
  - [x] `validators/quality_checks.py`: 5종 품질 체크 자동화
  - [x] `scripts/data_quality_report.py`: 품질 체크 이력 조회
  - [x] `collectors/infomax.py`: `get_stock_codes()`, `get_expired_codes()` 추가
  - [x] `scripts/daily_update.py` STEP 0: `sync_stock_master()` — 신규 상장/폐지 자동 처리

- [x] **백업 전략** ✅
  - [x] `scripts/backup_db.py`: pg_dump -Fc 주간 백업
  - [x] 백업 보관 정책: 7일 초과 자동 삭제
  - [x] `schedulers/daily_scheduler.py`: 매주 일요일 03:00 자동 백업

- [x] **프로젝트 정리** ✅
  - [x] Read-only DB 계정 생성 (`korea_stock_reader`)
  - [x] 불필요 파일/폴더 삭제 (api/, etl/, notebooks/, 1회성 스크립트)

**완료 기준 달성**: ✅

---

## 🌐 Phase 5: 인터페이스 개발 (예정)

- [ ] **Python 라이브러리**
  - [ ] SQLAlchemy 모델 export
  - [ ] 헬퍼 함수 제공

- [ ] **FastAPI (선택)**
  - [ ] `api/main.py`
  - [ ] CRUD 엔드포인트
  - [ ] Swagger 문서

- [x] **접근 권한 관리** ✅ (2026-02-22)
  - [x] Read-only 사용자 생성 (`korea_stock_reader`)

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
  - 수집 현황, 품질 체크, 데이터 추이 시각화
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
