# 📝 TODO - 작업 목록

> **마지막 업데이트**: 2026-05-14 (오후)
> **현재 Phase**: Phase 4 + Phase 5(배당) + KRX 휴장일 + KOSPI200/KOSDAQ150 SCD2 + ETF 일별 스냅샷 + 지수/지수선물/주식선물 일별 + **Phase 6 분봉**: 종목/ETF 30초봉 ✅ / 1분봉 1/2~4/24 ✅ + **Phase 7**: 지수/지수선물/주식선물 30초봉 + 분봉/일별 NEAR/NEXT 통합 view ✅

---

## 🆕 2026-05-14 (오후) — Phase 7 NEAR/NEXT 정합성

- [x] **1분봉 백필 4/21~4/26 마무리** (148.6분 / 5xx 1건만, 한낮 LS 안정성 입증)
- [x] **4/20 누락 877 종목 보충** (15.6분 / 5xx 0)
- [x] **`_per_stock_gap` v2 — (종목, 일자, 인터벌) 3차원** (`cc6e3d3`) — 인터벌 다른 부분 누락 검출
- [x] **지수선물 백필 contract별 useful 구간** (`62612ca`) — `_useful_start_date(만기)` helper, 3월물(만기됨) 자동 skip + 9월물 1/2~3/12(NEXT 격상 전) 자동 skip
- [x] **9월물 1/2~3/12 noise 데이터 46k row 삭제** (KP=97 KQ=1 거래량, farther future)
- [x] **분봉 NEAR/NEXT view v2 — self-join 만기 정렬** (`8e1f695`) — 인포맥스 매핑 의존성 제거, LS 진짜 NEAR+NEXT 자동 식별
- [x] **일별 NEAR/NEXT 통합 view** (`198d4a3`) — NEAR=인포맥스, NEXT=분봉 derived (정합성 확보)
- [x] **DB 종합 점검** — 종목/ETF 분봉 + 지수/지수선물/주식선물 + 일별/분봉 view 검증

## 🆕 2026-05-14 (오전) — 분봉 일배치 안정화 (운영 사고 + 5중 개선)

- [x] **`hard_timeout` worker thread no-op** (`3071fd2`) — APScheduler signal 에러 회피
- [x] **`futures_master.json` export 구조 분리** (`df54abc`) — 분봉 일배치로 이전, daily_update LS 호출 0건
- [x] **`backfill_index_minute_bars` 정책 정합** (`20b5874`) — 기본 KOSPI200/KOSDAQ150만
- [x] **분봉 일배치 cron 04:00 → 23:00 KST** (`86eaa70`) — 새벽 LS 5xx 회피 (3.4% → 0%)
- [x] **LS API 단축 timeout** (`262875c`) — hard_timeout 25→10s, requests (10,30)→(5,15)
- [x] **갭 fill 종목별 max(time) v1** (`751d872`) — 분봉 일배치 자연 회복성 + 인덱스 활용
- [x] **5/13 불필요 지수 249개 삭제** (124,500 row)
- [x] **5/12 누락 1,485 종목 + 5/13 전체 종목 수동 보충** (4,032 호출 / 154분 / 5xx 0)

## 후속 검증 + 보완

- [ ] **5/14 22:00 stockfut + 23:00 분봉 일배치 첫 새 cron 실행 검증** — 모니터 `b44if6de0` watch 중
- [ ] **옵션 2 후속**: 한낮 LS latency p95/p99 1주일 측정 → false-fail 발생 시 timeout 상수 조정
- [ ] **ETF 청산 자동 detection**: 연속 N일 empty_response → `stocks.is_active=FALSE` (현재 수동 — 472350 사례)
- [ ] **2030년 year wraparound 대비**: 분봉 NEAR/NEXT view 만기 정렬 키 (현재 chars 4-5 알파벳 정렬)

---

## 🛠️ 백필 완료 후 진행 (1분봉 백필 ETA 2026-05-15 10시 경 예상 — chain 끝나면 SIGCONT)

- [ ] **LS API token fetch 실패 retry** (`collectors/ls_api.py`)
  - 다음날 06:55~07:00 KST 만료 시점에 _fetch_token 실패하면 _token_value=None 보존 → 이후 호출 모두 실패
  - 수정: fetch 실패 시 raise + 다음 호출에서 재시도 가능하게 (현재 코드도 부분적으로 OK이지만 명시적 retry 권장)
- [ ] **run_update 외부 except 보호** (`scripts/daily_update.py` run_update 543~)
  - DB 연결 끊기면 main()에서 후속 STEP 모두 skip. main()에 try/except 한 겹 더
- [ ] **신규 주식선물 자동 매핑 로직** (현재 수동)
  - 매주/월 신규 주식선물 추가 시 LENS json sync 또는 LS t8401 호출 → futures_underlyings 자동 보강
  - daily_update의 적절한 STEP에 추가
- [ ] **5/28 LS deprecate 마이그 검증**
  - 신 TR 적용: t8415→t8465 ✅ / t8432→t8467 ✅ (코드 변경 완료, 5/28 이후 실제 동작 검증)
  - 미마이그: 종목 t8452는 아직 구 TR — t8451/t8453/t8454로 검토 필요

## 🆕 2026-05-13 — 지수/지수선물/주식선물 30초봉 통합

- [x] **DB 백업 권한 fix** ✅ (TimescaleDB chunks SELECT to una0)
- [x] **분봉 STEP 분리** — daily_update에서 빠짐, 04:00 KST 별도 cron으로 분리, 백필 진행 중 자동 skip 가드
- [x] **LS API TR 검증 — 8회 STOP/CONT 사이클** ✅
  - t8418 (업종/지수 N분), t8465 (선물 N분, t8415 신 TR), t8406 (주식선물 분, 당일만), t8401 (주식선물 마스터), t8424 (전체업종), t8467 (지수선물 마스터), t8435 (파생 마스터)
  - lookback 한계 측정: 지수=2026-01-02부터, 지수선물=2025-10 이전부터, 주식선물=당일만
- [x] **DB 스키마 분리** ✅ (`index_ohlcv_intraday`, `futures_ohlcv_intraday` 신설)
- [x] **collectors/ls_api.py 확장** ✅
  - 401 자동 token refresh + retry (`_invalidate_token`)
  - 새 TR 메서드 5개 (t8418/t8465/t8406/t8401/_post_generic)
  - 만기 식별 (`_parse_expiry_yyyymm`, `select_near_next_two`) — group별 근월+다음월물 자동 식별
- [x] **새 파이프라인 4개** (`scripts/daily_update.py`) ✅
  - `run_index_minute_bars_pipeline` (KOSPI200 + KOSDAQ150)
  - `run_futures_minute_bars_pipeline` (KOSPI200 F + KOSDAQ150 F, 각 근월/다음월물)
  - `run_stockfut_minute_today_pipeline` (주식선물 t8406, 당일만, 273 종목 × 근월/다음)
  - 갭 backfill 로직: `_gap_business_days(table, code_col, target)` — 며칠 누락도 자동 회복
  - STOP/CONT 정책: `_ls_backfill_pause/resume` — 백필 진행 중에도 일배치 우선
- [x] **백필 스크립트 신설** ✅ (`backfill_index_minute_bars.py`, `backfill_futures_minute_bars.py`)
- [x] **scheduler 변경** ✅ (`schedulers/daily_scheduler.py`)
  - `job_minute_bars_daily` (04:00 KST) — 4 파이프라인 + outer STOP/CONT
  - `job_stockfut_today` (**22:00 KST 평일**) 신규 — 주식선물 당일 적재
- [-] **백필 chain 진행 중** — KOSPI200 F ✅ / KOSDAQ150 F 진행 중 / 지수 (101, 301) 대기 / 1분봉 CONT 대기
- [-] **1분봉 백필 재시작** — 3/6~4/26, 19.85% STOPPED, chain 끝나면 SIGCONT, 최종 완료 5/15 10시 경 예상

### 데이터 정책 (확정)
- 지수: KOSPI200 (101) + KOSDAQ150 (301) 만
- 지수선물: KOSPI200 F + KOSDAQ150 F **각 근월+다음월물만** (총 4개, 매일 자동 갱신)
- 주식선물: 종목별 근월+다음월물 (273 × 2 ≈ 546, basecode 기준 group)
- 만기 식별: 매일 master 호출 + `select_near_next_two` → 만기 임박 시 자동 다음월물 추가

### 운영 정책
```
04:00 KST  daily_minute_bars  종목/ETF + 지수 + 지수선물 (갭 backfill, STOP/CONT)
05:30 KST  daily_update       OHLCV/수급/외인 + 배당 + LENS export (인포맥스 + DART)
22:00 KST  stockfut_today     주식선물 t8406 당일 (historical 불가 — 매일 받기 필수)
일03:00    weekly_backup      DB 백업
```

### 알려진 한계
- 주식선물 historical 불가 → 22:00 cron 미실행 시 그날 영구 손실
- 1/2~3월 시점 진짜 근월(F 2603) 데이터는 master에서 안 잡힘 (LS master active만 반환)
- 지수 (t8418)는 2026-01-02 이전 lookback 불가 — 그 이전 데이터 영구 없음

---

## 🆕 2026-05-12 신규/완료

- [x] **지수/지수선물/주식선물 일별 OHLCV** ✅ (2026-05-12)
  - 신규 테이블 4개: indices / index_ohlcv_daily / futures_underlyings / futures_ohlcv_daily
  - 인포맥스 API: /api/index/code, /api/index/hist, /api/future/code, /api/future/active|2active
  - 4년 백필: 지수 273개 245k row + 선물 45 underlying 59k row (34.6분)
  - 섹터지수 포함 (코스피200 헬스케어/금융/에너지 등 + 코스닥/KRX 섹터 다수)
  - 선물 ohlcv 에 이론가 / 시장 베이시스 / 이론 베이시스 / 미결제약정까지 포함
  - daily_update에 `run_indices_futures_daily_pipeline` STEP 통합 (5/13 05:30부터 자동 누적)
- [-] **CD91/RP 등 무위험금리** — 인포맥스 `/api/bond/rate/ir_yield` 발견. 단 최근 7일치만 권한. 4년 백필 보류. 향후 ECOS API 또는 LS 시도.

---

## 🆕 2026-05-11 신규/완료

- [x] **Phase 6 분봉 시스템 (LS t8452)** ✅ 부분 완료 (2026-05-11)
  - `ohlcv_intraday` 통합 테이블 (PK: stock_code, time, exchange, interval_seconds)
  - 봉 단위 자동 분기: 4/27 이후=30초봉(ncnt=0), 그 이전=1분봉(ncnt=1)
  - LS t8452 collector 강화: 5000호출 자동 token refresh / hard_timeout 25s / 매 호출 새 session
  - 30초봉 4/27~5/8 완료: 12.24M row, 0 에러
  - 1분봉 1/16~4/26 백필 중 (ETA 5/14 06시 KST)
- [x] **ETF 일별 스냅샷 (5일 FIFO)** ✅ (2026-05-11)
  - 기존 `etf_portfolios` (SCD2) DROP, 신규 `etf_portfolio_daily` + `etf_master_daily`
  - `collectors/infomax.py` `get_etf_master()` 추가 (/api/etp — creation_unit 등)
  - daily_update `run_etf_daily_snapshot_pipeline` STEP 통합
  - 첫 적재: PDF 38,922 row, Master 631 row
- [x] **정정공시 [기재정정] 처리** ✅ (2026-05-11)
  - dividends `_assign_version_and_ex(conn)` — DB max(version)+1 부여
  - 4년치 재실행: 502건 추가 INSERT, 001390/138930 등 검증
  - cache 14일 무효화 (`CACHE_FRESH_DAYS=14`) — DART 지연 등록 자동 회수
- [ ] **Phase 7 (선물 분봉, LS API)** — 별도 진행 예정 (지수선물/주식선물 + 지수 자체 분봉)
- [ ] **CD91/RP 4년치 무위험금리** — ECOS API 또는 LS 추후

---

## 🆕 2026-05-10 신규/완료

- [x] **KOSPI200/KOSDAQ150 구성종목 SCD2 적재** ✅ (2026-05-10)
  - 데이터 소스: 인포맥스 `/api/etf/port` — KODEX 200(069500) / KODEX 코스닥150(229200) PDF
  - `collectors/infomax.py` `get_etf_portfolio()` 신규
  - `index_components` 테이블에 5/8 baseline 적재 (200/150)
  - `scripts/daily_update.py` `run_index_components_pipeline()` — 매일 diff → SCD2 (편입 INSERT / 편출 end_date close)
  - PDF 빈 응답(휴장/오류) 시 변경 적용 안 함 (가드)
  - 적재 버그 수정: `isdigit()` 필터로 알파벳 종목(`0126Z0` 삼성에피스, `0009K0` 에임드바이오) 누락 → `stocks` 매칭으로 변경
- [ ] **분봉 수집(LENS Phase 6)** — 다음 작업 (LS API spec 확인 후 진행)
- [ ] **과거 백필(2022~)** — 선택. 정기변경 이력 재구성 필요시

---

## 🆕 2026-05-02 신규/완료

- [x] **KRX 휴장일 DB SSoT 전환** ✅ (2026-05-02)
  - `database/schema/krx_holidays_schema.sql`: date PK / reason / source(CHECK) / updated_at
  - `scripts/export_krx_holidays.py` 개편: 산출 → DB UPSERT → JSON write (manual 행 보호)
  - 2022~2027 백필 96건 (ohlcv_gap 71 / holidays_kr 22 / rule_0501 1 / rule_1231 2)
  - LENS JSON 계약 그대로 (LENS 코드 변경 0)
- [x] **daily_update.py 휴장일 skip + KRX 휴일 파이프라인** ✅ (2026-05-02)
  - `is_market_closed()` / `last_business_day_on_or_before()` 헬퍼 (DB 조회)
  - `get_update_range()` end → 어제 기준 마지막 영업일
  - 단일 휴장일 타겟 시 미니 보고서(`*_skip.txt`) + 배당/휴일 파이프라인은 그대로 진행
  - `run_krx_holidays_pipeline()` 매일 호출
- [x] **`backfill_dividends.py` 휴일 출처 통일** ✅ (2026-05-02)
  - `holidays.KR` 직접 호출 제거 → DB 조회 (모듈 캐시)

---

## 🆕 2026-05-01 신규/완료

- [x] **스케줄러 매일 05:30 자동 실행 가동** ✅ (2026-05-01)
  - tmux 세션 `scheduler`, daily_update.main() 호출 (dividend pipeline + LENS export 자동 통합)
  - 첫 자동 실행 정상 완료 (외인지분율 5:30 안전 시간 검증)
- [x] **KRX 휴장일 LENS export** ✅ (2026-05-01)
  - `scripts/export_krx_holidays.py` (2022~2027, 97건)
  - 한국어 reason, 임시휴장 자동 포착, 제헌절 함정 필터링

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
