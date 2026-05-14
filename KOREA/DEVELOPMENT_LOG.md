# 📓 개발 이력 (DEVELOPMENT LOG)

> 프로젝트 진행 과정에서의 주요 결정사항, 변경사항, 배운 점을 기록합니다.
> 새로운 환경에서 작업 재개시 최근 항목부터 읽어 컨텍스트를 빠르게 파악하세요.

---

## 2026-05-14 — 분봉 일배치 안정화 (운영 사고 + 5중 구조 개선)

### 운영 사고 타임라인

| 시각 (KST) | 사건 |
|---|---|
| 5/13 22:00 | 첫 `job_stockfut_today` cron 실행 → **34ms 만에 실패** (`signal only works in main thread`) |
| 5/13 22:09 | 메인 스레드에서 수동 보충 — 546 코드 / 432,266 row / 에러 0 / 10.2분 |
| 5/13 23:19 | scheduler 재시작 (hard_timeout fix 적용) |
| 5/14 04:00 | `job_minute_bars_daily` 트리거 → 5/12 갭 처리 시작 |
| 5/14 04:00~10:13 | LS API **5xx 다발 (3.4%)** → retry 누적 → 6시간 돌고 5/12 526/2,011 코드만 적재 |
| 5/14 08:13 | daily_update의 `export_futures_master_json()` HTTP 500 (분봉 일배치와 LS 동시 hit) |
| 5/14 10:13 | scheduler 강제 종료 → 분봉 일배치 중단 → 5/12 1,485 종목 + 5/13 전체 누락 |
| 5/14 11:11~13:38 | 수동 보충 — 5/12 + 5/13 종목/ETF (4,032 호출) **5xx 0건 / 154분** |

→ **새벽 vs 한낮 LS API 안정성 35배 차이** 측정 (3.4% → 0%)

### 🔧 5중 구조 개선 (커밋 6개)

#### 1. `hard_timeout` worker thread no-op (`3071fd2`)
**문제**: APScheduler worker thread에서 `signal.signal(SIGALRM)` 즉시 실패.
**수정**: `collectors/ls_api.py:64` worker thread면 no-op (requests timeout만 의존). 메인 스레드(수동 실행/백필)에서는 기존 동작 유지.

#### 2. `futures_master.json` export 구조 분리 (`df54abc`)
**문제**: scheduler 프로세스 안에서 04:00 분봉 일배치(LS API)와 05:30 daily_update의 export(LS t8401)가 동시 실행 → 5xx. `_ls_backfill_pause`는 외부 프로세스만 STOP, 자기 자신은 STOP 못함 (deadlock).
**수정**: `daily_update.main()`에서 export 호출 제거 → `daily_update`는 LS API 호출 0건. `job_minute_bars_daily` 끝(outer pause/resume 안쪽)에 export 추가. **모든 LS-using 작업이 한 cron job으로 일원화 → 충돌 불가능**.

#### 3. `backfill_index_minute_bars` 정책 정합 (`20b5874`)
**문제**: 스크립트가 `t8424` 전체업종 master(252개) 받음 → 5/13 보충 시 `run_index_minute_bars_pipeline`의 KOSPI200/KOSDAQ150 hardcoded 정책과 불일치 → 250개 불필요 지수 124,500 row 사후 삭제 필요.
**수정**: 기본 `["101", "301"]`만. `--codes` override + `--all-master` 예외 플래그.

#### 4. cron 04:00 → 23:00 KST (`86eaa70`)
**3개 에이전트 병렬 토의 결과** (옵션 1·2·3 분석):
- 옵션 1 권고: 23:00 — 22:00 stockfut와 1시간 안전 마진 + 데이터 무결성 (정규장 마감 + 시간외 18:00 종료 후 5시간) + 사용자 활동 시간대
- DB 검증: t8452 응답에 시간외 단일가(16~18시) 봉 미포함 → 정규장만 받음 (사용자 요구 부합)
- 효과: D-1 처리 (이전 04:00은 ohlcv_daily 미적재로 D-2 처리)

#### 5. LS API 단축 timeout (`262875c`)
- `hard_timeout` 25→10s, `requests timeout` (10,30)→(5,15) — 4 호출 사이트
- 효과: 5xx 1건당 묶임 ~120s → ~60s (2x 단축)
- 정상 호출 1.05s 평균이라 4.7배 안전 마진. 토큰 fetch(critical low-frequency)는 (15s, (10,30)) 유지.

#### 6. 갭 fill 종목별 max(time)으로 전환 (`751d872`)
**문제**: `_gap_business_days`가 테이블 전체 max(time)만 봄 → 분봉 일배치 도중 죽으면 일부 종목만 적재된 날짜가 done 처리 → 누락 종목 영원히 회복 안 됨 (5/14 5/12 1,485개 누락 사례).
**수정**: `_per_stock_gap(table, code_col, codes, target_date)` 신규 — bulk 1쿼리로 종목별 max(time) (인덱스 `(stock_code, time DESC)` 활용 ~수십 ms). `run_minute_bars_pipeline` 순회 day-first → **code-first**: 중단 시 완료 종목은 전부 채워지고 미완료 종목은 다음 실행이 자연 재개.
**비용**: 종목별 쿼리 ~2초/실행 (무시 가능).

### 📊 데이터 영향
- 종목/ETF 30초봉: 5/8, 5/11, 5/12, 5/13 모두 2,016 코드 적재 완료 ✅
- 지수 30초봉: 5/13 KOSPI200(101) + KOSDAQ150(301) 각 500 row ✅
- 지수선물 30초봉: 5/13 KP F + KQ F 각 근/원월물 4 contracts × 1,000 row ✅
- 주식선물 30초봉: 5/13 546 코드 / 432,432 row ✅ (수동 보충)
- 5/13 불필요 지수 249개 삭제: 124,500 row

### 📌 다음 작업
- 5/14 23:00 KST 첫 새 cron 실행 검증 (모니터 `b44if6de0` watch 중)
- 옵션 2 후속: 한낮 LS latency p95/p99 1주일 측정 → false-fail 발생 시 timeout 상수 조정
- ETF 청산 종목 blacklist (현재 수동 → 자동 detection: 연속 N일 empty_response → is_active=FALSE)

### 🎓 배운 점
- **APScheduler 서비스 설계**: `signal.SIGALRM` 등 main-thread-only API는 무조건 worker thread에서 깨짐. 라이브러리에서 사용 시 thread 검사 필수.
- **scheduler 내부 자기 STOP 불가**: `pgrep + SIGSTOP` 패턴은 외부 프로세스만 가능. 같은 프로세스 안의 두 LS-using job은 별도 cron job으로 일원화하거나 모듈 레벨 lock 필요.
- **테이블 전체 max(time) 갭 검사의 함정**: 부분 적재 상태에서 max만 보면 회복 불가. **종목별 max** + 인덱스 `(code, time DESC)` 조합이 비용·회복성 모두 만족.
- **시간대별 LS API 안정성 35배 차이**: 동일 호출량이 새벽 5xx 3.4% / 한낮 0%. 새벽 자동화 작업은 retry 정책으로 가리지 말고 **시간대 자체를 회피**가 ROI 최고.
- **3 에이전트 병렬 토의의 효과**: 옵션 1·2·3 동시 분석으로 30분 → 5분 단축. 각 에이전트가 코드 + 웹 검증을 독립적으로 수행해 권고가 일관됨.

### 🔗 관련 커밋
- `3071fd2` fix: hard_timeout worker thread no-op
- `df54abc` fix: futures_master export 분봉 일배치로 이전
- `20b5874` fix: backfill_index 기본 KOSPI200/KOSDAQ150만
- `86eaa70` chore: 분봉 일배치 cron 04:00 → 23:00 KST
- `262875c` perf: LS API hard_timeout 25→10s, timeout (10,30)→(5,15)
- `751d872` feat: 분봉 일배치 갭 fill 종목별 max(time)으로 전환

---

## 2026-05-13 — 지수/지수선물/주식선물 30초봉 통합 (LS API)

### 배경
- 1분봉 백필 진행 중, 사용자가 발견 — 지수/지수선물/주식선물 30초봉 미수집
- 기존 `ohlcv_intraday`는 종목/ETF만. `_minute_scope.py`에 지수/선물 코드 미포함
- LS rolling window 특성상 30초봉은 시간 흐를수록 lookback 못 받게 됨 → 즉시 시작 필요

### 🔬 LS API TR 검증 (`ls_api_full.md` + 8회 STOP/CONT 사이클)

| TR | 그룹 | 용도 | endpoint |
|----|------|------|----------|
| **t8418** | [업종] 차트 | 지수 N분차트 (30초/1분) | `/indtp/chart` |
| **t8424** | [업종] 시세 | 전체업종 마스터 (252개) | `/indtp/market-data` |
| **t8465** | [선물/옵션] 차트 | 선물 N분차트 (t8415 신 TR — 5/28 deprecate 대비) | `/futureoption/chart` |
| **t8467** | [선물/옵션] 시세 | 지수선물 마스터 (t8432 신 TR) — KOSPI200 F | `/futureoption/market-data` |
| **t8435** | [선물/옵션] 시세 | 파생종목 마스터 (gubun=SF → KOSDAQ150 F) | `/futureoption/market-data` |
| **t8401** | [선물/옵션] 시세 | 주식선물 마스터 (3,080건, 273 종목) | `/futureoption/market-data` |
| **t8406** | [선물/옵션] 시세 | 주식선물 분차트 (cgubun='M' bgubun=0=30초) — **당일만** | `/futureoption/market-data` |

### Lookback 한계 (실측)

| 데이터 | 30초봉 | 1분봉 |
|--------|--------|-------|
| 종목/ETF (t8465) | 16일 한도 | 그 이전 자동 fallback |
| 지수 (t8418) | **2026-01-02부터** | 2026-01-02부터 (동일) |
| 지수선물 (t8465) | 2025-10 이전부터 | 2025-07부터 |
| **주식선물 (t8406)** | **❌ historical 불가 — 당일만** | 동일 |

→ 주식선물은 매일 받지 않으면 영구 손실. 별도 22:00 KST cron 필요.

### ✅ 완료 작업

#### 1. DB 스키마 분리 (`database/schema/index_futures_intraday_schema.sql`)
- `index_ohlcv_intraday` (PK: index_code, time, interval_seconds) — TimescaleDB hypertable
- `futures_ohlcv_intraday` (PK: futures_code, time, interval_seconds) + `open_interest` (미결제약정) — TimescaleDB hypertable
- 종목/ETF는 기존 `ohlcv_intraday` 그대로 (3 테이블 분리 — 일별과 같은 패턴: `ohlcv_daily` / `index_ohlcv_daily` / `futures_ohlcv_daily`)

#### 2. `collectors/ls_api.py` 확장
- **401 자동 token refresh + retry** — 다중 백필 프로세스 충돌 보호 (`_invalidate_token`)
- `_post_generic` — 신규 TR 일반화 POST helper (401/5xx/429/timeout 일관 처리)
- `get_index_intraday_bars(shcode, target_date, ncnt)` — t8418 페이징
- `get_futures_intraday_bars(shcode, target_date, ncnt)` — t8465 페이징
- `get_stockfut_today_bars(focode, bgubun)` — t8406 단일 호출 (cnt=900으로 1일 전체)
- `get_stockfut_master()` — t8401 마스터
- **만기 식별**: `_parse_expiry_yyyymm` (hname의 YYMM/YYYYMM → 해당 월 두번째 목요일) + `select_near_next_two` (group별 근월+다음월물 자동 식별)

#### 3. `scripts/daily_update.py` — 새 파이프라인 4개
- `run_index_minute_bars_pipeline(target_date)` — KOSPI200(101) + KOSDAQ150(301) 만 (사용자 정책)
- `run_futures_minute_bars_pipeline(target_date)` — KOSPI200 F + KOSDAQ150 F **각 근월+다음월물** (총 4개)
- `run_stockfut_minute_today_pipeline(target_date)` — t8406, **당일만**, basecode별 근월+다음 (273 × 2 ≈ 546)
- **갭 backfill**: `_gap_business_days(table, code_col, target)` — max(time)+1 ~ target 거래일 sweep (일별 OHLCV와 동일 패턴 — 며칠 누락도 자동 회복)
- **STOP/CONT 정책**: `_ls_backfill_pause/resume` — 백필 진행 중에도 일배치 우선. 백필 PID 자동 발견 (`pgrep -f backfill_*.py`), SIGSTOP → 일배치 → SIGCONT

#### 4. 백필 스크립트 신설
- `scripts/backfill_index_minute_bars.py` (지수, 2026-01-02부터)
- `scripts/backfill_futures_minute_bars.py` (지수선물, 2026-01-02부터)
- 주식선물은 historical 불가 → 백필 스크립트 없음

#### 5. `schedulers/daily_scheduler.py` 변경
- `job_minute_bars_daily` (04:00 KST) — 4 파이프라인 호출 (종목/ETF + 지수 + 지수선물). outer pause/resume.
- `job_stockfut_today` (**22:00 KST 평일**) 신규 — 주식선물 t8406 당일 적재 (장 마감 후 사후호가/정산 끝난 시점)

### 🔑 핵심 결정 — 근월+다음월물만

거래량 분석 결과 **근월물 99.9% 집중**. 원월물 5개는 거래 거의 0:

| futures_code | 만기 | row | 누적 거래량 |
|---|---|---|---|
| A0166000 | F 2606 (근월) | 31,500 | **1,597,836** |
| A0169000 | F 2609 (다음) | 31,500 | 1,585 |
| A016C000 | F 2612 | 31,500 | 57 |
| 나머지 4개 | — | — | 0~10 |

→ **근월+다음월물만** 유지 정책. 매일 master 호출 시 만기 임박하면 자동 다음으로 이동 (`select_near_next_two`).

### 5/28 LS deprecate 공지 대비

기존 `t8415`/`t8432` 등 신 TR로 마이그 (가이드 라인 14-36 참조):
- t8415 → **t8465** (선물/옵션 N분차트)
- t8432 → **t8467** (지수선물 마스터)
- t8414 → t8464 (틱)
- t8416 → t8466 (일주월)

신 TR은 가격 필드 자릿수 확대 (다른 InBlock/필드 동일).

### 운영 정책

```
04:00 KST  daily_minute_bars  종목/ETF + 지수 + 지수선물 (갭 backfill, STOP/CONT)
22:00 KST  stockfut_today     주식선물 t8406 (당일만, historical 불가)
05:30 KST  daily_update       OHLCV/수급/외인 + 배당 + LENS export (인포맥스 + DART)
일03:00    weekly_backup      DB 백업
```

### 알려진 한계
- **주식선물 historical 불가** — 매일 22:00 cron 미실행 시 그날 영구 손실
- 1/2~3월 시점의 진짜 근월(F 2603)은 만기 지나서 t8467 master에서 안 잡힘 → 그 시점 데이터는 받을 수 없음 (현재 master active만 받히는 LS 한계)
- t8418 (지수)는 종목과 달리 **2026-01-02 이전 lookback 불가** — 그 이전 데이터 영구 없음

### 백필 진행
- KOSPI200 F (7개 만기, 87일) — 2026-05-13 14:22 완료. 이후 5개 (근월/다음 외) DB DELETE — 195k row 정리
- KOSDAQ150 F + 신코드(4개) 호출 — 진행 중 (chain 자동)
- 지수 (101, 301, 87일) — 대기
- 1분봉 백필 (3/6~4/26, 19.85% STOPPED) — chain 끝나면 SIGCONT (5/15 10시 경 최종 완료 예상)

---

## 2026-05-12 - 지수/섹터/지수선물/주식선물 일별 OHLCV (인포맥스)

### 배경
- 분봉 백필 진행 중 사용자가 발견 — 지수 자체 (코스피200, 코스닥150 등) 일별 OHLCV 없음
- 지수선물·주식선물 근월/원월 일별 OHLCV도 없음 — 차익거래 분석 필수
- Phase 7(선물 분봉) 와 별개로, 일별은 즉시 받기 가능

### ✅ 완료 작업

1. **신규 테이블 4개** (`database/schema/indices_futures_schema.sql`)
   - `indices` 마스터 — code/kr_name/index_type(K/Q/X/T/N)/return_type/is_sector
   - `index_ohlcv_daily` — 지수 일별 OHLCV + marketcap + constituents
   - `futures_underlyings` — 선물 기초자산 마스터 (01=코스피200, 06=코스닥150, GN=금양, …)
   - `futures_ohlcv_daily` — 선물 일별 OHLCV (NEAR/NEXT 연결) + 미결제약정 + 이론가 + 베이시스(시장/이론)

2. **`collectors/infomax.py`** 메서드 4개 추가
   - `get_index_codes(type_)` — `/api/index/code`
   - `get_index_hist(code, start, end)` — `/api/index/hist` (1000행 한도)
   - `get_future_codes(underlying_type)` — `/api/future/code`
   - `get_future_active(code, start, end, contract_class)` — `/api/future/active`(NEAR) / `/api/future/2active`(NEXT)

3. **`scripts/backfill_indices_futures.py`** — 4년 백필 실행
   - 지수: 273개 → 245,832 row (코스피 113 + 코스닥 66 + KRX 90 + 일반상품 3 + 코넥스 1)
   - 선물 active/2active: 45 underlying / 58,975 row (200개 중 거래량 있는 것만)
   - 700일 chunks 호출 (1000행 한도 회피)
   - 선물 dedup 안전망 (롤오버 시점 같은 일자 중복 행 제거)
   - 총 34.6분

4. **`scripts/daily_update.py`** — `run_indices_futures_daily_pipeline(target_date)` 신규
   - 매일 어제 ~ 어제+7일 마진 호출 (지연 등록 케이스 회수)
   - 멱등 UPSERT
   - scheduler 재시작 → 5/13 05:30 부터 자동 누적

### 발견한 한계 (미해결)

- **인포맥스 CD91/RP 등 무위험금리** — `/api/bond/rate/ir_yield` 에 `cd91d_yld`/`call_yld`/`msb1y_yld` 포함됨이 확인됐으나 **최근 7일치만 조회 권한** → 4년 백필 불가
  - daily_update에 매일 누적은 가능 (놓치면 영구 손실이지만 우선순위 보류)
  - 과거 데이터는 한국은행 ECOS API 또는 LS API로 추후 시도

### 🐛 발견·해결

- **indices.return_type VARCHAR(5) 부족** — PR/TR/NTR 외에 더 긴 값 존재 → VARCHAR(20) 으로 확장
- **선물 active 응답 같은 일자 중복** — 롤오버 시점에 NEAR↔NEXT 만기물이 동시 응답되는 경우 → INSERT 전 dedup

---

## 2026-05-11 - Phase 6 분봉 백필 (LS t8452) + 정정공시 처리 + ETF 일별 스냅샷

### 배경
- LENS Phase 6(분봉 수집) 본격 시작 — 트레이딩 데스크 백테스팅 데이터
- LENS Claude와 협업으로 spec 확정 후 인프라 구축

### Phase 6 분봉 시스템 (LS증권 OpenAPI)

1. **TR 결정 — t8412 → t8452** (실측 검증 후)
   - t8412 (`/stock/market-data`): 30초봉 가능하지만 **과거 ~10거래일치만** (롤링 윈도우)
   - **t8452** (`/stock/chart`, 통합 N분 차트): 1분봉(ncnt=1)은 **2026-01-02 전구간 OK**, 30초봉(ncnt=0)은 t8412와 동일 10거래일 한도
   - t8412는 deprecate 클래스 (`_DeprecatedT8412`)로 보존, 운영은 t8452 단일

2. **봉 단위 혼합 정책** (LENS 결정)
   - 2026-01-02 ~ 2026-04-26: **1분봉** (ncnt=1, interval_seconds=60) — t8452 가용 깊이 안에서
   - 2026-04-27 ~: **30초봉** (ncnt=0, interval_seconds=30) — 미세 흐름 보존, 매일 점진 누적
   - 30초봉 시작점 `START_30SEC = 2026-04-27` (실측 확정)
   - `select_ncnt(target_date)` 헬퍼로 자동 분기

3. **스키마 — `ohlcv_intraday`** (단일 통합)
   - PK: (stock_code, time, exchange, interval_seconds)
   - `exchange CHAR(1) DEFAULT 'K'` (향후 NXT 확장 대비)
   - `interval_seconds SMALLINT` (30 / 60)
   - `volume` = t8452 `jdiff_vol` (봉 단위)
   - `trading_value` = t8452 `value × 1,000,000` (백만원 → 원)
   - 기존 `ohlcv_30sec` DROP 후 마이그레이션

4. **collector — `collectors/ls_api.py`**
   - OAuth2 토큰 23h 캐시 + **5000 호출마다 자동 refresh** (`TOKEN_CALL_LIMIT`) ← LS token-level 호출 한도 회피
   - `hard_timeout(25)` (signal.alarm) — `requests timeout`이 CLOSE-WAIT에서 안 먹는 케이스 방어
   - 매 호출 새 `requests.Session()` (`_refresh_session`) — CLOSE_WAIT 누적 방지
   - `Connection: close` 헤더
   - `get_intraday_bars(code, target_date)` — ncnt 자동 분기 + 페이징

5. **스코프 — `scripts/_minute_scope.py`**
   - KOSPI200 + KOSDAQ150 active (`index_components` SCD2) ∪ 한국 ETF (해외 키워드 제외) ∪ ETF PDF underlying union
   - **stocks 매칭 필터** (외국주식/채권/의사코드 제외) — 약 2,011 종목

6. **백필 — `scripts/backfill_30sec_bars.py`**
   - 30초봉 (4/27~5/8): 12.24M row, 0 에러, 9.8시간
   - 1분봉 (1/16~4/26): 진행 중 (1분봉당 ~381봉 × 종목 × 거래일)

### ETF 일별 스냅샷 (LENS 요청 — etf_portfolios SCD2 폐기)

- **이유**: ETF PDF의 현금(KRD010010001) 항목이 매일 변동 → SCD2 변화 감지 무의미
- **변경**: `etf_portfolios` (SCD2) **DROP** → `etf_portfolio_daily` (5일 FIFO) + `etf_master_daily` (5일 FIFO)
- `collectors/infomax.py` `get_etf_master()` 추가 (`/api/etp` — creation_unit, listed_shares 등)
- `daily_update.py` `run_etf_daily_snapshot_pipeline()` — 매일 631 ETF × 2 endpoint
- 첫 적재: PDF 38,922 row + Master 631 row (22분)
- 분봉 스코프 SQL → `etf_portfolio_daily` 가장 최근 snapshot 참조 + stocks 매칭

### 정정공시 처리 ([기재정정]) + DART cache 지연 등록 대응

- **버그**: BNK금융지주(138930) 4/30 배당 공시 누락 — 4/30 시점 cache에 1건만 (실제 7건), 늦게 등록된 6건 영구 누락
- **원인**: `cache_get_list`가 cache 영구 유효 → DART D+N 늦게 등록되는 공시 못 받음
- **수정**: `CACHE_FRESH_DAYS = 14` — 최근 14일 cache 자동 무효화
- 4/30~5/11 cache 강제 갱신 → 누락 10건 추가 INSERT (BNK 포함)

- **정정공시 [기재정정]**: 같은 (code, fiscal_year, period) 의 정정공시도 INSERT 시도하는데 batch 내 version=1 부여 → ON CONFLICT (기존 v1) 무시
- **수정**: `_assign_version_and_ex(conn)` — DB max(version) 조회 후 +1 부여
- 001390 KG케미칼 검증: v1(2/13 원본 is_latest=FALSE) / v2(4/30 정정 is_latest=TRUE) 자동 토글
- 과거 4년치 전수 재실행: 502건 추가 INSERT

### 🐛 (대량) 발견·해결한 백필 운영 이슈

- **stuck 진단 헛발질** (5/10~11): "5xx 31초 timeout" 패턴 → 원인 추정 다수
  - tee pipe buffer? (X) — 실제 영향 X
  - CLOSE_WAIT 누적? (부분 영향) — 매 호출 새 session으로 해결
  - LS 새벽 점검 시간대? (X) — 일시 부하였을 뿐
  - **진짜 원인**: **LS token-level 호출 한도** (추정 ~10k 호출). 단일 호출(새 token)은 정상, 백필(같은 token 누적)만 차단
  - **해결**: 5000 호출마다 자동 token refresh (`TOKEN_CALL_LIMIT`)
- **t8412 vs t8452**: t8412는 과거 깊이 한계 → 백필 불가 발견 후 t8452로 전환
- **`isdigit()` 필터로 알파벳 종목 누락** (KOSPI200 SCD2 시드 시): 0126Z0/0009K0 등. stocks 매칭으로 정정
- **DB row count 기반 stuck monitor false alarm** — 멱등 UPDATE 구간엔 row 안 늘어남 → **로그 mtime 기반**으로 변경

### 운영 메모

- tmux 백그라운드 실행 시 `tee` 대신 **파일 직접 redirect** (`> log 2>&1`) — `tee` + pseudo-tty 조합에서 block 사례 있음
- 큰 백필은 자동 self-heal monitor 가동 (10분 stuck/dead/complete 감지 + 알림)
- LENS realtime 과 같은 LS 계정 사용 — 시간대 분리 (LENS 장중 + 08:30/15:50 / Finance_Data 05:30 + 백필 야간)

---

## 2026-05-06 - dividends ex_date 산출 버그 fix (record_date 휴장 케이스)

### 배경
- LENS 측에서 `ex_date` 데이터 검증 중 12-31 류 record_date 케이스에서 잘못된 값 발견
- KRX 룰: record_date가 휴장이면 **직전 영업일이 실질 권리 확정일**, 그 직전 영업일이 ex_date (즉 두 단계 backstep)
- 기존 코드는 `business_day_before()` 한 번만 호출 → 한 단계만 backstep → record_date 휴장 케이스 전부 오답

### 잘못된 케이스 예 (수정 전 → 수정 후)
| record_date | 폐장일 (실질 record) | 잘못된 ex | 정정 ex |
|---|---|---|---|
| 2025-12-31 (수, 휴장) | 12/30 (화) | 12/30 ❌ | 12/29 (월) ✅ |
| 2024-12-31 (화, 휴장) | 12/30 (월) | 12/30 ❌ | 12/27 (금) ✅ |
| 2023-12-31 (일, 휴장) | 12/28 (목, 폐장) | 12/28 ❌ | 12/27 (수) ✅ |
| 2022-12-31 (토, 휴장) | 12/29 (목, 폐장) | 12/29 ❌ | 12/28 (수) ✅ |

### ✅ 완료 작업

1. **`scripts/backfill_dividends.py`** (LENS Claude 측 fix 적용분)
   - 신규 헬퍼 `_is_business_day(biz_days, d)` — ohlcv 범위 안: 거래일 list 멤버십 / 범위 밖: weekday + krx_holidays
   - 신규 함수 `compute_ex_date(biz_days, record_date)` — record_date 휴장 시 두 단계 backstep
   - `refresh_future_ex_dates` (L225) + INSERT 경로 (L496) 모두 `compute_ex_date` 사용으로 교체
2. **단위 테스트** — 4개 휴장 케이스 + 1개 영업일 케이스 모두 정답 확인
3. **기존 dividends 전수 재계산** — `refresh_future_ex_dates()` 1회 호출로 **4,352건 UPDATE**
4. **LENS dividends.json 재export** — 6,493건 전부 정정된 ex_date로 갱신

### 자동 보정
- `refresh_future_ex_dates()` 는 `daily_update.run_dividend_pipeline()` 안에서 매일 호출됨
- 즉 향후 ohlcv가 채워지거나 새 공시 들어와도 자동으로 정확한 ex_date 산출

### 🤝 LENS 협업
- LENS Claude가 데이터 검증 중 발견 → 코드 fix까지 첨부해 전달
- Finance_Data 측에서 fix 검증 + 전수 재계산 + 재export 실행
- LENS는 새 mtime 감지하면 자동 reload

---

## 2026-05-10 - KOSPI200/KOSDAQ150 구성종목 SCD2 적재 + daily_update 통합

### 배경
- LENS Phase 6(분봉 수집) spec 진행 전 "분봉 수집 종목 스코프"를 결정해야 함
- 그 전제로 KOSPI200/KOSDAQ150 구성종목을 정기적으로 추적할 수단 필요
- `index_components` 테이블은 진작 만들어 뒀지만 데이터 0건 / 수집 코드도 없었음

### 결정한 데이터 소스 — Infomax `/api/etf/port` (ETF PDF)
- 인포맥스 백서(32페이지) 전수 조사 결과 **지수 구성종목 list API 자체는 없음**
- `/api/index/code|info|hist` 모두 메타/시계열만, `constituents` 컬럼은 "개수"만
- 우회: KOSPI200 추적 ETF (KODEX 200 069500) + KOSDAQ150 추적 ETF (KODEX 코스닥150 229200) PDF
- 둘 다 **physical full replication** 방식 → 종목 list 99%+ 정확
  - 정기변경(6/12월) 시점에 ETF 리밸런싱과 KRX 공식 변경일 사이 ±수일 lag 가능 (실용적 trade-off)

### ✅ 완료 작업

1. **`collectors/infomax.py`** — `get_etf_portfolio(code, target_date)` 메서드 추가
   - `/api/etf/port` 호출 → `{date, etf_code, constituents, port_code, port_name, port_volume, port_value}` list 반환
   - 의사코드(010010 원화현금) / 알파벳 종목코드 모두 raw 반환, 호출자가 필터

2. **5/8 baseline 적재** — `index_components` 테이블에 SCD2 시작점
   - KOSPI200 200종목 / KOSDAQ150 150종목 / `effective_date=2026-05-08, end_date=NULL`

3. **`scripts/daily_update.py`** — `run_index_components_pipeline(target_date)` 신규 함수
   - 매일 두 ETF PDF 받아서 active 멤버십(`end_date IS NULL`) 과 diff
   - 편입: `INSERT effective_date=target_date, end_date=NULL`
   - 편출: `UPDATE end_date=target_date`
   - PDF 빈 응답(휴장일/오류) 시 변경 적용 안 함 (보호 가드)
   - `main()`의 KRX 휴일 파이프라인 다음에 호출 추가

### 🐛 적재 버그 (해결)
- 첫 baseline 시 `port_code.isdigit()` 필터로 6자리 숫자만 통과 → **알파벳 포함 종목 누락**
  - KOSPI200: `0126Z0` 삼성에피스홀딩스 누락 (199만 적재)
  - KOSDAQ150: `0009K0` 에임드바이오 누락 (149만 적재)
- 우연한 카운트 일치로 발견 어려웠음 — `010010` 의사코드 +1, 알파벳 종목 -1 로 균형이 맞아 일견 정상으로 보임
- **수정**: `stocks` 테이블 매칭(set & known)으로 변경 — 의사코드/알파벳 종목 모두 정확히 처리
- 누락분 INSERT 추가 → 최종 200/150 정확

### 검증
- 동일 날짜(5/8) 재실행 → 변경 0 (idempotent ✅)
- 일요일(5/10) 빈 PDF → skip, DB 무변동 (가드 작동 ✅)
- 시장 분류 100% 일치 (KOSPI200 → 전부 KOSPI / KOSDAQ150 → 전부 KOSDAQ)

### 향후
- 과거 백필(2022~) 선택사항 — 4년 × 250일 × 2 ETF ≈ 2,000회 호출 (~33분). 정기변경 이력 재구성 가치 있을 때
- 분봉 수집 종목 스코프에 즉시 활용 가능

---

## 2026-05-06 - dividends ex_date 산출 버그 fix (record_date 휴장 케이스)

### 배경
- LENS 측에서 `ex_date` 데이터 검증 중 12-31 류 record_date 케이스에서 잘못된 값 발견
- KRX 룰: record_date가 휴장이면 **직전 영업일이 실질 권리 확정일**, 그 직전 영업일이 ex_date (즉 두 단계 backstep)
- 기존 코드는 `business_day_before()` 한 번만 호출 → 한 단계만 backstep → record_date 휴장 케이스 전부 오답

### 잘못된 케이스 예 (수정 전 → 수정 후)
| record_date | 폐장일 (실질 record) | 잘못된 ex | 정정 ex |
|---|---|---|---|
| 2025-12-31 (수, 휴장) | 12/30 (화) | 12/30 ❌ | 12/29 (월) ✅ |
| 2024-12-31 (화, 휴장) | 12/30 (월) | 12/30 ❌ | 12/27 (금) ✅ |
| 2023-12-31 (일, 휴장) | 12/28 (목, 폐장) | 12/28 ❌ | 12/27 (수) ✅ |
| 2022-12-31 (토, 휴장) | 12/29 (목, 폐장) | 12/29 ❌ | 12/28 (수) ✅ |

### ✅ 완료 작업

1. **`scripts/backfill_dividends.py`** (LENS Claude 측 fix 적용분)
   - 신규 헬퍼 `_is_business_day(biz_days, d)` — ohlcv 범위 안: 거래일 list 멤버십 / 범위 밖: weekday + krx_holidays
   - 신규 함수 `compute_ex_date(biz_days, record_date)` — record_date 휴장 시 두 단계 backstep
   - `refresh_future_ex_dates` (L225) + INSERT 경로 (L496) 모두 `compute_ex_date` 사용으로 교체
2. **단위 테스트** — 4개 휴장 케이스 + 1개 영업일 케이스 모두 정답 확인
3. **기존 dividends 전수 재계산** — `refresh_future_ex_dates()` 1회 호출로 **4,352건 UPDATE**
4. **LENS dividends.json 재export** — 6,493건 전부 정정된 ex_date로 갱신

### 자동 보정
- `refresh_future_ex_dates()` 는 `daily_update.run_dividend_pipeline()` 안에서 매일 호출됨
- 즉 향후 ohlcv가 채워지거나 새 공시 들어와도 자동으로 정확한 ex_date 산출

### 🤝 LENS 협업
- LENS Claude가 데이터 검증 중 발견 → 코드 fix까지 첨부해 전달
- Finance_Data 측에서 fix 검증 + 전수 재계산 + 재export 실행
- LENS는 새 mtime 감지하면 자동 reload

---

## 2026-05-02 - KRX 휴장일 DB SSoT 전환 + 스케줄러 휴장일 skip

### 배경
- 5/2 새벽 스케줄러가 5/1(근로자의 날) 데이터 수집을 시도해서 모든 종목 "실패"로 보고됨 → 노이즈
- 휴장일 정보가 3군데 분산 (`holidays.KR` 라이브러리 / `backfill_dividends.py` 인라인 / `export_krx_holidays.py` 산출 로직) → SSoT 위반
- LENS도 휴장일 JSON을 소비 중이라 결국 같은 데이터를 두 시스템이 각자 들고 있음

### ✅ 완료 작업

1. **`krx_holidays` 테이블 신설** (`database/schema/krx_holidays_schema.sql`)
   - `date PK / reason TEXT / source TEXT (CHECK 제약) / updated_at TIMESTAMPTZ`
   - source enum: `ohlcv_gap | manual | holidays_kr | rule_0501 | rule_1231`
   - 토/일 미저장 (LENS export 정책과 일치)
   - 별도 인덱스 없음 — PK가 자동 인덱스

2. **`scripts/export_krx_holidays.py` 개편**
   - 기존 산출 로직(과거 ohlcv 갭 + 미래 holidays.KR/rule)에 source 추적 추가
   - 트랜잭션 내 UPSERT (`ON CONFLICT (date) DO UPDATE`) 후 동일 결과로 LENS JSON write
   - 사후 정정 삭제 정책: 산출 [year_start, year_end] 범위 내 산출에 없는 행 DELETE
     - **단 `source = 'manual'` 행은 보호** (수동 임시공휴일 보존)
   - JSON은 DB에서 다시 읽어 적음 (manual 행 포함, source 컬럼 제외)
   - `run_export()` 진입점 추가 — daily_update에서 import해 호출

3. **백필 첫 실행** — 2022~2027 96건 적재 (`ohlcv_gap` 71 / `holidays_kr` 22 / `rule_0501` 1 / `rule_1231` 2)

4. **`scripts/daily_update.py` 휴장일 처리**
   - `is_market_closed(conn, d)` / `last_business_day_on_or_before(conn, d)` 헬퍼 추가 (DB 조회)
   - `get_update_range()`가 어제 기준 마지막 영업일을 end로 반환 → 자동모드는 휴장일 자체를 타겟팅 안 함
   - `run_update()` 진입 직후 단일 휴장일 가드 → `{"skipped_holiday": True}` 반환
   - `main()`이 skip 케이스에서 미니 보고서(`_skip.txt`) 작성 후 배당/휴일 파이프라인은 그대로 진행
   - 신규 함수 `run_krx_holidays_pipeline()` 추가, `main()`에서 배당 다음에 호출

5. **`scripts/backfill_dividends.py` 휴일 출처 통일**
   - `holidays.KR` 라이브러리 직접 호출 제거
   - `_load_krx_holidays()` — 모듈 레벨 캐시로 DB 1회 조회 (daily_update 1회 실행 동안 불변)
   - `_is_market_closed(d)` → DB-backed 셋 조회

### 📐 설계 결정

- **DB가 SSoT, JSON은 파생물** — LENS realtime은 JSON 계약(경로/포맷/reason) 그대로 유지 → LENS 코드 변경 0
- **삭제 정책에서 manual 보호** — 사람이 수동 INSERT 한 임시공휴일을 라이브러리 갱신 한 번에 날려버리는 사고 방지
- **`source` enum 우선순위** — `ohlcv_gap` 위에 `manual` 두지 않음. 과거는 ohlcv가 진실. 단 미발생 미래 날짜에 대한 manual INSERT는 holidays_kr보다 위.
- **daily_update 매일 호출** — 산출 자체가 가벼우니 idempotent UPSERT로 매일 돌려도 부담 없음. 임시공휴일 발표 시 자동 반영.

### 🐛 운영 시 주의

- **DART 공시는 휴장일에도 등록 가능** — 이사회결의/정정공시는 평일/휴일 무관. 휴장일에도 배당 파이프라인은 그대로 돌려야 함 (실제로 main이 그렇게 동작).
- 임시공휴일 사전 INSERT 방법: `INSERT INTO krx_holidays (date, reason, source) VALUES ('2026-XX-XX', '임시공휴일', 'manual');`

### 🤝 LENS 협업

- LENS Claude 가 테이블 설계안 사전 제안 → Finance_Data 측에서 미세조정 후 반영
  - 미세조정: 별도 INDEX 줄 제거 / source CHECK 제약 추가 / 삭제 정책에 manual 보호 추가
- 작업 완료 통보 별도 메시지 송부

---

## 2026-05-01 - 스케줄러 첫 자동 실행 + KRX 휴장일 LENS export

### ✅ 완료 작업

1. **스케줄러 첫 자동 실행 정상**:
   - 새벽 05:30 자동 트리거 → 08:12 완료 (2시간 42분, 정상 패턴)
   - OHLCV/수급/외인지분율 4/30 + 배당 5/1 + LENS export 모두 자동
   - **외인지분율 5:30 안전 시간 검증 완료** (0건 실패)

2. **KRX 휴장일 export** (`scripts/export_krx_holidays.py`):
   - LENS 화면용 휴장일 캘린더 JSON
   - 출력: `/home/una0/projects/LENS/data/krx_holidays.json`
   - 형식: `[{"date": "2026-05-01", "reason": "근로자의 날"}, ...]`
   - 범위: 2022 ~ 2027 (97건)
   - 산출 방식:
     - 과거 (ohlcv_daily 범위): 갭 분석 = 진실 (임시휴장 2건 자동 포착)
     - 미래: holidays.KR + 근로자의 날(5/1) + 연말 폐장(12/31)
   - 영어→한국어 매핑 (어린이날, 삼일절 대체공휴일 등)

### 🐛 발견·해결한 버그

- **holidays.KR이 제헌절(7/17)을 휴일로 잘못 분류**
  - 제헌절은 2008년부터 공휴일 아님, KRX 정상 거래 (2024/2025-07-17 ohlcv 존재 확인)
  - `KRX_NON_HOLIDAYS` 셋으로 필터링 추가

### 🐛 운영 시 주의

- **tmux에서 Ctrl+C로 나오지 말 것** — 안의 프로세스 죽임
- 올바른 detach: `Ctrl+B` 후 `D`
- 5/1 14:44에 우연히 Ctrl+C로 스케줄러 종료된 사례 발생 → 즉시 재시작

### 📌 KRX 휴장일 갱신 정책

- 1년 1회 수동 실행 (매년 12월 KRX 영업일정 발표 후)
- LENS는 startup 시 로드 → 갱신 후 LENS 재시작 필요
- daily_update에 통합 안 함 (사용 빈도 낮음)

---

## 2026-04-25 ~ 04-30 - 배당(Dividends) 데이터 시스템 구축 + LENS 연동

> **목적**: LENS 프로젝트의 배당 화면/종목차익 베이시스 계산용 데이터 소스 구축. DART 공시를 단일 진실 소스로.

### ✅ 완료 작업

#### 1. DB 스키마 + ORM (`database/schema/dividends_schema.sql`, `database/models.py`)
- `dividends` 테이블 신규 (정관변경 시대의 복잡성 반영)
- 핵심 설계:
  - **Surrogate PK + UNIQUE (code, fiscal_year, period, version)** — 정정공시 이력 보존
  - **5종 날짜 필드**: `board_resolution_date` (이사회 결의일), `announced_at` (공시 접수), `record_date` (배당기준일), `ex_date` (배당락일), `pay_date` (지급일)
  - **`charter_group` (A/B)** — A=정관변경(이사회 결의로 기준일 지정), B=미변경(결산일=기준일)
  - **`source` enum**: DART/SEIBro/KRX/ESTIMATE
  - **`is_latest` flag** — 같은 (code, fy, period) 그룹의 최신 version만 TRUE
  - **`corp_name`** (DART 공시 시점 회사명, stocks.stock_name fallback)
  - **`raw_text_url`** + `dart_rcp_no` (원문 추적)
- Hypertable 미적용 — 이벤트 기반 데이터, PK가 surrogate라 부적합

#### 2. DART API 수집기 (`collectors/dart.py`)
- `DartClient` 클래스 (rate limit 60/min, thread-safe)
- 주요 메서드:
  - `get_corp_code_map()` — corp_code ↔ stock_code 매핑 (3,961개)
  - `search_disclosures()` — 페이지네이션 자동 (max_pages=50)
  - `get_dividend_decisions()` — '현금ㆍ현물배당결정' 필터 + **자회사 공시 자동 제외**
  - `get_charter_changes()` — 정관변경 공시
  - `get_document_xml()` — 본문 다운로드 + **인코딩 자동 감지** (UTF-8/CP949 혼재)
  - `parse_dividend_decision()` — 본문 파싱 (amount, yield_pct, dates, dividend_class, period)
  - `is_subsidiary_disclosure()` — 본문 키워드 다중 패턴 검사 (자회사 misattribution 차단)
  - `classify_charter_group()` — 정관 본문에서 A/B 분류

#### 3. 백필 시스템 (`scripts/backfill_dividends.py`)
- 2022-01-01 ~ 현재 전 종목 백필
- 디스크 캐시: `cache/dart/list/{yyyymmdd_yyyymmdd}.json` + `cache/dart/document/{rcp_no}.xml` (1.3GB, gitignore)
- ON CONFLICT DO NOTHING + version 자동 부여 + is_latest 마킹
- ex_date 산출: ohlcv_daily 거래일 캘린더 우선, 미래는 `holidays.KR` + 근로자의 날 fallback

#### 4. 정관변경 분류 (`scripts/classify_charter_groups.py` + `verify_charter_groups.py`)
- 종목별 주주총회소집공고 본문 분석 → A/B 분류
- record_date 휴리스틱과 cross-check (불일치 케이스 별도 보고)
- 결과: A 266 / B 289 / NULL 28
- 분류 결과 JSON 저장 (`cache/dart/charter_classification.json`)

#### 5. 추정 엔진 (`estimators/dividend_estimator.py`)
- 과거 N년(기본 5년) 패턴으로 미래 배당 추정 (source='ESTIMATE')
- amount: 직전 평균, record_date: 직전 동기 (월·일) 투영
- ex_date: ohlcv_daily 거래일 기반 직전 영업일

#### 6. LENS Export (`scripts/export_dividends.py`)
- DB → `/home/una0/projects/LENS/data/dividends.json` (원자적 tmp→rename)
- LENS 합의 형식: 14개 필드 + revisions 배열 임베드
- COALESCE(stocks.stock_name, dividends.corp_name) — 상폐 종목도 이름 표시

#### 7. daily_update.py 통합 (`scripts/daily_update.py::run_dividend_pipeline`)
- 매일 daily_update 마지막 단계로 자동 실행
- 자동 갭 backfill (DB MAX(announced_at) + 1 ~ end_date+1)
- `refresh_future_ex_dates()` — ohlcv가 채워질 때마다 미래 ex_date 자동 정확화
- LENS export 자동 갱신

### 🐛 발견·해결한 주요 버그

#### Bug-1: DART list.json 5,000건 한도
- **증상**: 결산기 peak month(2~3월)의 일부 종목 누락
- **원인**: DART API가 페이지 제한 (page 100 × max 50 = 5,000) → AJ네트웍스(095570) 등 정상 종목이 list에서 빠짐
- **단계별 해결**:
  - 월별 chunks (52개) → 누적 1,393건 → **1,378건만 적재**
  - 주별 chunks (226개) → 누적 6,541건 → **5,159건 추가 적재** (5배)
  - 일별 chunks (1,580개) → 누적 8,598건 → **2,214건 추가 적재** (최종)
- **결론**: 일별 chunks가 가장 안전. `day_chunks()` 함수 채택

#### Bug-2: DART 본문 인코딩 깨짐
- 거래소 자율공시(800/900XXX rcp_no)는 EUC-KR/CP949 인코딩
- UTF-8로 강제 디코딩 → 한글 깨짐 → 정규식 미매칭 → 파싱 실패
- 해결: `_decode_xml_bytes()` 헬퍼 추가 (`<?xml encoding="..."?>` 우선, UTF-8 strict 시도, CP949 fallback)
- 효과: 첫 백필 파싱률 79% → 99%

#### Bug-3: 자회사 misattribution (가장 위험했던 버그)
- **증상**: 콜마홀딩스(024720) 2024 H1 yield_pct 41.749% — 평균 1~2% 대비 비정상
- **원인**: DART에서 `(자회사의 주요경영사항)` 공시는 모회사 corp_code로 들어옴 → 우리는 모회사 배당으로 잘못 매핑. 예: ㈜연우(비상장)의 배당 4,033원이 콜마홀딩스 배당으로 잡힘 → 모회사 주가 9,660원 대비 yield 41% 폭발
- **이중 필터 적용**:
  - 1차: `report_nm`에 "자회사" 포함 시 스킵 (951건)
  - 2차: 본문 키워드 6개 패턴 (`is_subsidiary_disclosure`) — 주요자회사명 / 자회사의 주요경영사항 / [비상장] 마커 / 지주회사+자회사 / 100% 자회사+비상장 / (안전망) 자회사+비상장+1주당+지주회사
- **Retroactive**: 캐시된 본문 5,982건 + 캐시 누락 942건 다운로드 후 검증 → 총 **954건 자회사 misattribution 제거**

#### Bug-4: ex_date 미래 record_date 처리
- **증상**: 신한지주 2026 Q2 record=2026-04-30, ex_date=2026-04-24 (4영업일 차이)
- **원인**: backfill 시점에 ohlcv_daily가 record_date보다 과거까지만 있어, business_day_before가 ohlcv 마지막 거래일을 반환
- **해결**:
  - `business_day_before` 보강 — ohlcv 범위 밖이면 `holidays.KR` + 근로자의 날 적용한 weekday 추정
  - `refresh_future_ex_dates()` 함수 추가 — daily_update 끝에 자동 호출, ohlcv가 채워지면 정확값으로 자동 갱신

#### Bug-5: yield_pct NULL (378건)
- **원인**: DART 공시 본문에 "시가배당율(%) 보통주식 -" (회사가 미공시한 케이스)
- **해결**: ohlcv_daily의 ex_date 종가로 일괄 recompute (`amount / close_price * 100`) — 355건 채움
- **잔여 12건**: ohlcv 자체가 없는 상폐 종목 → NULL 유지

### 🎯 주요 결정사항

1. **LENS-Finance_Data 분리 아키텍처**:
   - LENS는 DB 직접 쿼리 X, **JSON 파일 contract**로 통신
   - export 경로: `/home/una0/projects/LENS/data/dividends.json` (mtime 기반 자동 reload)
2. **자회사 공시 차단 정책**: 비상장 자회사 배당은 모회사 주가와 무관 → DB 적재 자체를 차단
3. **추정값과 확정값 같은 테이블**: source 컬럼으로 구분 (`DART`/`SEIBro`/`KRX`/`ESTIMATE`)
4. **정정공시 보존**: 같은 (code, fy, period)에 version 1, 2, 3... 저장 후 is_latest로 최신만 노출
5. **한국 시장 영업일 규칙**: `holidays.KR` (공공 공휴일) + 근로자의 날 5/1 (거래소 휴장) — 12/31 폐장은 ohlcv가 자연 반영

### 💡 배운 점 / 인사이트

1. **DART API 한도 (5,000건)는 문서에 명시 안 됨** — peak month 데이터 누락은 운 좋게 다른 데이터(AJ네트웍스)와 비교해서 발견
2. **본문 인코딩 검증의 중요성** — replacement character (`�`)는 정규식이 못 잡으면 파싱 실패가 silent fail
3. **자회사 misattribution은 yield 필터로만 발견** — yield 정상 범위 안에 숨어있는 케이스는 본문 패턴 매칭 필수
4. **이중 안전망 (report_nm + 본문 키워드)** — 한 가지 필터만으론 우회 케이스 다수
5. **Retroactive 검증의 가치** — 새 필터 적용 후 기존 데이터에 소급 적용해야 진짜 정리 완료

### 📊 최종 데이터 규모 (2026-04-30 기준)

| 지표 | 값 |
|------|------|
| 전체 dividends row | 6,922 |
| is_latest=TRUE (LENS export) | 6,492 |
| 종목 수 | 1,465 |
| 정정공시 그룹 | 840 |
| name NULL | 0 |
| yield_pct NULL (의미 있는 케이스) | 12 (모두 상폐 종목) |
| 자회사 misattribution 제거 | 954건 |
| 데이터 범위 | 2021 ~ 2026 (6년) |
| DART 캐시 디스크 사용 | 1.3GB (gitignore) |

### 🔧 신규/변경 파일

**신규**:
- `database/schema/dividends_schema.sql`
- `collectors/dart.py`
- `scripts/backfill_dividends.py`
- `scripts/export_dividends.py`
- `scripts/classify_charter_groups.py`
- `scripts/verify_charter_groups.py`
- `scripts/analyze_missing_dividends.py`
- `estimators/dividend_estimator.py`

**변경**:
- `database/models.py` (Dividend 클래스 추가)
- `config/settings.py` (DART_API_KEY, LENS_EXPORT_PATH 등)
- `.env.example` / `.env`
- `scripts/daily_update.py` (`run_dividend_pipeline()` 통합)

### 📌 남은 작업

1. **코일러레이트 검증** (옵션): corp_code(공시 제출자) vs 본문 corp_name 일치 검증 자동화
2. **SEIBro/KRX 검증 모듈** (Plan C): 사용자 보류 결정 — 운영 중 필요 시 추가
3. **추정 엔진 활성화**: 현재 코드는 있으나 자동 실행 안 함. LENS가 추정값 필요 시 daily_update에 통합
4. **외인지분율 익일 수집 패턴**: 인포맥스 API가 외인 데이터를 익일 새벽~오전에 제공 → 당일 daily_update 시 누락 → `--missing-only` 익일 보충 필요

---

## 2026-03-24 (화) - 외국인 지분율 수집 추가 + investor_trading 단위 버그 수정 + 버그 수정

### ✅ 완료 작업

1. **외국인 지분율(foreign_ownership) 테이블 및 수집 추가**
   - DB: `foreign_ownership` 테이블 생성 (TimescaleDB hypertable)
     - 컬럼: `time DATE`, `stock_code`, `frn_ownership_ratio`, `frn_ownership_vol`, `frn_limit_ratio`
     - `listed_shares` 미포함 (ohlcv_daily와 중복)
     - 인덱스: `idx_foreign_ownership_code_time ON (stock_code, time DESC)`
   - `collectors/infomax.py`: `get_foreign()` 메서드 추가 (`/api/stock/foreign`)
   - `scripts/daily_update.py`: STEP 3 외국인 지분율 수집 통합 (매일 16:30)
     - ETF/SPAC 제외 KOSPI+KOSDAQ ~2,642개 종목, ThreadPoolExecutor 병렬 수집
   - `scripts/collect_foreign_ownership.py`: 백필 전용 스크립트 (2022-01-03~2026-03-20, 1,252,296건)
   - `schedulers/daily_scheduler.py`: 주석/설명 업데이트 (외국인지분율 daily_update 통합 반영)
   - **`frn_limit_ratio` 의미 확인**: 외국인 보유 한도 비율 자체 (KT=49%, SKT=49%)
     - 소진율은 쿼리 시점에 `frn_ownership_ratio / frn_limit_ratio × 100`으로 계산

2. **investor_trading 단위 버그 수정**
   - **버그 원인**: `get_investor()`에 단위 자동감지 로직이 있었음
     - `bid_val / bid_vol` (天원 단위) ≥ 100이면 `unit=1` 사용
     - close_price ≥ 100,000원 종목은 역산단가가 100+ → `unit=1`로 오인
     - → net_buy_value가 1/1000 단위(천원)로 저장됨
   - **수정**: 자동감지 제거, 항상 `unit = 1_000` 고정
   - **왜 이상치 탐지가 못 잡았나**: `THRESHOLD_LARGE_NET_BUY = 500억` 임계값이 너무 높아
     - 삼성전자 기관 순매수가 수천억 → 잘못 저장된 수억도 평범하게 보임
   - **DB 교정**: 수차례 ×1000 / ÷1000 반복 수행
     - 최종 상태: 0.001x 버그 레코드 0건, too_large 0건
     - 잔존 32,861건 (ratio 0.01~0.1x): 단위 버그 아님
       - API 특성상 매수/매도 금액이 거의 균형인 경우 역산단가가 시세와 괴리 발생
       - 물리적 불가능(net_buy_vol × close > trading_value) 15,148건 포함
       - 교정 불가 판단 (원시 bid_value/ask_value 미저장), 알려진 이슈로 문서화

3. **UNIT_CHECK 품질 체크 추가**
   - `validators/quality_checks.py`: `check_investor_unit()` 추가 (6번째 체크)
   - 역산단가 `|net_buy_value / net_buy_volume|` ≈ 당일 종가 검증
   - 조건: `|net_buy_volume| ≥ 1,000주`, 임계값: 종가 × 0.1배 미만 or × 10배 초과

4. **3/20 ~ 3/23 데이터 수집 완료**
   - 3/20: OHLCV 3,799건 / investor_trading 10,876건 / foreign_ownership 2,642건 (첫 정규 수집)
   - 3/23: OHLCV 3,799건 / investor_trading 10,876건 / foreign_ownership 2,642건
     - 신규 상장 0166S0 추가 (OHLCV 실패 1건 — 당일 데이터 미제공)
     - 수급 데이터: 구버전 코드로 수집 → 단위 버그 565건 → 삭제 후 수정 코드로 재수집

5. **버그 수정: `get_missing_foreign_stocks()` LIKE 절 이스케이프**
   - psycopg2에서 파라미터 포함 SQL의 `%` → `%%` 이스케이프 필요
   - `LIKE '%스팩%'` → `LIKE '%%스팩%%'` 수정
   - `--missing-only` 모드에서만 호출되는 함수 → 기존 정규 수집은 영향 없었음

### 📝 설계 결정

- **외국인 지분율 수집 주기**: 주간이 아닌 일별로 결정 (daily_update.py STEP 3 통합)
  - API 조회 범위: 2002-06-14~현재 (24년치), 3년 청크로 백필
- **frn_limit_ratio**: 소진율이 아닌 한도 자체 (외국인이 보유 가능한 최대 %)
  - 소진율(한도 대비 실제 보유 %)은 쿼리 시점에 파생 계산
- **investor_trading 단위**: API는 항상 千원 단위 → 항상 × 1,000
  - 단위 자동감지 로직은 고가 종목에서 오인식 버그 유발 → 제거

### 현재 DB 현황 (2026-03-24 기준)

| 테이블 | 레코드 수 | 최신 날짜 |
|--------|----------|----------|
| stocks | ~3,800건 (활성 ~3,800) | - |
| ohlcv_daily | ~3,600,000건 | **2026-03-23** |
| market_cap_daily | ~3,600,000건 | **2026-03-23** |
| investor_trading | ~11,200,000건 | **2026-03-23** |
| foreign_ownership | ~1,260,000건 | **2026-03-23** |
| floating_shares | 1,034,865건 | 2026-02-19 |
| stock_sectors | 2,720건 | 2026-03-02 |

---

## 2026-03-17 (화) - 3/11~3/17 수집 완료 + 인포맥스 제공시간 실험 + sync_stock_master 개선

### ✅ 완료 작업

1. **3/11~3/16 데이터 수집 완료**
   - 각 날짜 `python scripts/daily_update.py YYYYMMDD`로 순차 수집
   - 건수 정상, 품질 체크 이상 없음

2. **인포맥스 당일 데이터 제공 시간 실험**
   - 배경: 인포맥스는 당일 데이터를 1차(16:30~16:40), 2차(18:40~) 두 번에 걸쳐 제공
   - 실험: 3/17 데이터를 18:10 KST에 1차 수집, 20:23 KST에 2차(`--missing-only`) 수집
   - 결과:
     - 1차 (18:10): OHLCV 1,111/3,803 (29%) 성공 — 대부분 데이터 미제공 상태
     - 2차 (20:23): 2,346/2,347 (~100%) 성공 — 472350 1건만 실패
   - **결론: 19:00 이후 수집 정책 결정** (스케줄러 재가동 시 반영 예정)

3. **`sync_stock_master()` ghost_delisted 추가**
   - 문제: ETF 청산 등 일부 종목이 `/api/stock/code`(상장)에도, `/api/stock/expired`(상폐)에도 없어 자동 처리 불가
   - 해결: 두 API 모두에 없는 DB 활성 종목 → `is_active=FALSE` 자동 처리
   - 즉시 발견·처리:
     - 472350 (1Q 차이나H) — 오래된 수동 보류 건
     - 0106J0 (대신 KOSPI200인덱스 X클래스)
     - 0120X0 (유진 챔피언중단기크레딧 X클래스)

4. **불필요 파일/폴더 정리**
   - ERROR 보고서 3개 삭제: 20260303, 20260309, 20260310
   - `utils/` 폴더 삭제 (빈 `__init__.py`만 존재)
   - `docs/.gitkeep` 삭제

### 📝 설계 결정

- **인포맥스 API는 REST 기반, OS 무관** (macOS/Linux/Windows 모두 동작)
  - 이전에 "Windows 전용"으로 오해했으나 HTTP API라 환경 무관
  - 유료 플랜 rate limit이 실질적 제약 (Lite 60회/분, Pro 180회/분)
- **수집 시간 정책: 19:00 이후** — 2차 제공(18:40+) 후 99%+ 종목 데이터 확정
- **ghost_delisted 처리**: ETF 청산처럼 두 API 모두에 미등록된 케이스를 매일 자동 정리

### 현재 DB 현황 (2026-03-17 기준)

| 테이블 | 레코드 수 | 최신 날짜 |
|--------|----------|----------|
| stocks | ~3,810건 (활성 ~3,800) | - |
| ohlcv_daily | ~3,500,000건 | **2026-03-17** |
| market_cap_daily | ~3,500,000건 | **2026-03-17** |
| investor_trading | ~11,000,000건 | **2026-03-17** |
| floating_shares | 1,034,865건 | 2026-02-19 |
| stock_sectors | 2,720건 | 2026-03-02 |

---

## 2026-03-11 (수) - 3/4~3/10 누락 수집 완료 + 472350 이슈 분석

### ✅ 완료 작업

1. **리눅스 서버에서 3/4~3/10 데이터 수집 완료**
   - `python scripts/daily_update.py YYYYMMDD` 날짜 명시 방식으로 순차 수집
   - daily_update.py 기본값은 "어제"까지만 수집 (당일 장 마감 전 실행 방지 설계)
     → 당일 수집 시 날짜 명시 필수: `python scripts/daily_update.py 20260310`
   - 3/9: 3,795건 / 3/10: 3,798건 수집 (신규 상장 3종목 자동 추가: 0162Y0, 0163Y0, 0164G0)

2. **3/10 OHLCV 25건 API 장애 → `--missing-only` 재수집**
   - 수집 당시 `008700~009780` 코드 대역 25개 종목 API 응답 없음
   - 다음날 `--missing-only` 재수집으로 24건 복구, 472350만 반복 실패
   - 재수집 명령: `python scripts/daily_update.py 20260310 --missing-only`

3. **472350 (1Q 차이나H(H)) 이슈 분석**
   - 3/4 이후 매일 API 응답 없음 (ohlcv_daily 최신: 2026-03-03)
   - 인포맥스 `/api/stock/expired` 상폐 API에도 미등록 → 자동 처리 불가
   - 2월 말부터 거래량 극소 (22주, 157주 수준) → ETF 청산 추정
   - **결정: 그냥 두기** — 상폐 종목 일괄 정리 시 함께 처리

### 📝 설계 결정

- **ETF 청산은 상폐 API에 늦게 반영**되거나 별도 처리됨 → `sync_stock_master()`로 자동 처리 불가
- 향후 **상폐 종목 일괄 정리 스크립트** 작성 필요 (API 미등록 케이스 수동 처리용)

### 현재 DB 현황 (2026-03-11 기준)

| 테이블 | 레코드 수 | 최신 날짜 |
|--------|----------|----------|
| stocks | 3,799 활성 / 3 비활성 | - |
| ohlcv_daily | ~3,430,000건 | **2026-03-10** |
| market_cap_daily | ~3,430,000건 | **2026-03-10** |
| investor_trading | ~10,520,000건 | **2026-03-10** |
| floating_shares | 1,034,865건 | 2026-02-19 |
| stock_sectors | 2,720건 | 2026-03-02 |

---

## 2026-03-04 (수) - net_buy_value 단위 오류 발견·수정 (천원→원)

### 🐛 문제 발견

`investor_trading.net_buy_value`가 천원(千원) 단위로 저장되어 있음을 발견.

- 원인: 인포맥스 `/api/stock/investor` API가 `bid_value`, `ask_value`를 **천원 단위**로 반환
- 증상: 삼성전자 외국인 순매도 32억원 → 실제는 **3.21조원**이어야 함
  - `net_buy_value / net_buy_volume` = 205원/주 (삼성전자 195,100원과 불일치)
  - × 1,000 적용 시 205,437원/주 → 정상 범위

### ✅ 완료 작업

1. **DB 기존 데이터 전체 수정**
   - `UPDATE investor_trading SET net_buy_value = net_buy_value * 1000;`
   - 대상: 10,096,996건 전체
   - bigint 범위 사전 검증: max(3.62조) × 1,000 = 3.62경 << bigint 한계(9.2경) ✓

2. **`collectors/infomax.py` 수정** (`get_investor()`)
   - `(bid_val - ask_val) * unit` 으로 원 단위 변환
   - 단위 자동 검증 로직 추가: `bid_value / bid_volume`으로 역산 단가 계산
     - 100원 미만 → 천원 단위(`unit=1000`) 적용
     - 100원 이상 → 이미 원 단위(`unit=1`) (API 사양 변경 대비)
     - 변환 후 100원~10,000,000원 범위 이탈 시 경고 발생

### 📝 확인된 단위 정리

| 테이블·컬럼 | 단위 | 비고 |
|------------|------|------|
| `ohlcv_daily.trading_value` | 원(₩) | API 직접값 |
| `ohlcv_daily.open/high/low/close_price` | 원(₩) | API 직접값 |
| `market_cap_daily.market_cap` | 원(₩) | close × listed_shares 계산 |
| `investor_trading.net_buy_value` | **원(₩)** | API 천원 단위 → ×1,000 변환 후 저장 |
| `investor_trading.net_buy_volume` | 주(株) | API 직접값 |

---

## 2026-03-04 (수) - DB 복원(3/3 덤프) + 데이터 품질 전수 검토 + 2/27·3/3 수집

### ✅ 완료 작업

1. **DB 복원** — `backup_20260303_2247.dump` (WSL Linux 환경)
   - 파일: `C:\Users\infomax\Downloads\backup_20260303_2247.dump` → WSL `/mnt/c/...`
   - 방법: `psql -h localhost -U postgres` 기준으로 dropdb/createdb/pg_restore 직접 실행
   - `role "unanimous0" does not exist` 에러 다수 발생 → 기능 영향 없음 (테이블 소유자 postgres로 정상)
   - 복원 후 유니크 인덱스 3개 자동 포함 확인 ✅

2. **삼성전자(005930) 2026-02-26 데이터 오류 발견 및 수정**
   - 증상: 전후일(203,500/218,000원) 대비 2/26 종가 50,500원으로 잘못 저장
   - 원인: 인포맥스 API가 2/26 삼성전자 OHLCV를 잘못 반환한 것으로 추정 (거래량은 정상)
   - 수정: API 재조회 → open=206,500 high=219,000 low=206,000 close=218,000 으로 UPDATE
   - market_cap_daily도 동시 수정

3. **전체 데이터 품질 전수 검토**
   - OHLCV 논리 오류(high<low 등): **0건** ✅
   - 종가=0/음수(거래 있는 날): **0건** ✅
   - 수급 net_buy_value > trading_value: **0건** ✅
   - 전일·익일 대비 모두 ±30% 초과 이탈(데이터 오류 패턴): **2건 발견**
     - 폰드그룹(472850) 2025-12-30: API도 동일값 반환, 정확한 값 확인 불가 → 냅두기로 결정
     - PLUS 200선물인버스2X(253160) 2025-07-08: high/close=4,695원, avg_unit=3,039원 불일치 → 냅두기로 결정
   - 수정주가 이슈(244개 종목) 재확인 → 정상 동작 확인 (아래 설계 결정 참조)

4. **2026-02-27 데이터 수집 완료** (소요: 1시간 52분)
   - OHLCV + 시가총액: 3,794건 (실패 0)
   - 투자자별 수급: 10,868건 (실패 2건 — 054620, 054780)
   - 상장폐지 자동 처리: **455910** 1개 (is_active=FALSE)
   - 주가이벤트의심 3건 감지 → 삼성전자는 2/26 데이터 오류로 인한 오탐, 나머지 2건(캠시스·씨케이솔루션)은 실제 이벤트

5. **2026-03-03 데이터 수집 완료** (3/2는 대체공휴일 휴장)
   - OHLCV + 시가총액: 3,794건 (실패 0)
   - 투자자별 수급: 10,876건 (실패 0) — 완벽
   - 신규 상장/폐지: 없음
   - 품질 체크 5종 **이상 없음** ✅
   - 시장 특이사항: 방산·에너지 테마 급등 (LIG넥스원+29.9%, 한화에어로+19.8%), 레버리지 ETF -15~20%

### 📝 설계 결정

- **수정주가 이슈 확정**: `ohlcv_daily` OHLCV 가격은 주식분할 반영 수정주가 기준으로 저장됨 (정상/의도된 동작)
  - 근거: 에코프로(086520) 5:1 분할(2024-04) 전후 가격이 연속적으로 이어짐 (분할 전~101,000원 → 분할 후~106,000원)
  - `trading_value`는 실제 거래대금(금액)이므로 수정 불가 → 분할 전 구간에서 trading_value/volume ≠ close_price는 정상
  - 기존 MEMORY.md의 "DB 전체 비수정주가" 기록은 오류였음 → 수정 완료

### 현재 DB 현황 (2026-03-04 기준)

| 테이블 | 레코드 수 | 최신 날짜 |
|--------|----------|----------|
| stocks | 3,794 활성 / 3 비활성 | - |
| ohlcv_daily | **3,279,487건** | **2026-03-03** |
| market_cap_daily | **3,279,487건** | **2026-03-03** |
| investor_trading | **10,096,996건** | **2026-03-03** |
| floating_shares | 1,034,865건 | 2026-02-19 |
| stock_sectors | 2,720건 | 2026-03-02 |

---

## 2026-03-02 (월) - FnGuide FICS 업종 크롤링 구축

### ✅ 완료 작업

1. **`stock_sectors` 테이블 생성** (flat 구조)
   - `stock_code VARCHAR(10) PRIMARY KEY REFERENCES stocks(stock_code)`
   - `fics_sector VARCHAR(100)` — FICS 업종명 (예: "반도체 및 관련장비")
   - `updated_at TIMESTAMP` — 마지막 수집일시
   - 기존 `sectors` 테이블(계층형)은 유지하되, 실제 데이터는 `stock_sectors`에만 저장

2. **`scripts/crawl_sector.py` 작성**
   - 대상: KOSPI + KOSDAQ 활성 종목 (ETF 제외) 약 2,720개
   - 소스: `https://comp.fnguide.com` — 페이지 텍스트에서 `FICS\s+([^|\n\r\t]+)` 정규식으로 추출
   - DELAY=1.5초, RETRY=3, TIMEOUT=15, LOG_EVERY=100
   - `--missing` 플래그: stock_sectors에 없는 종목만 수집 (신규 상장 후 사용)
   - UPSERT: `ON CONFLICT (stock_code) DO UPDATE SET fics_sector, updated_at`
   - `requirements.txt`에 `beautifulsoup4` 추가

3. **`schedulers/daily_scheduler.py`에 분기별 잡 추가**
   - `job_quarterly_sector()` 함수 추가
   - `CronTrigger(month="1,4,7,10", day="1-7", day_of_week="sun", hour=3, minute=30)`
   - 분기 첫 번째 일요일 03:30 KST (1/4/7/10월)

4. **초기 전체 수집 완료** — 2,607개 섹터 확인 / 113개 NULL (우선주·스팩·리츠 등)

5. **인코딩 버그 발견 및 수정** ← 수집 1회차에서 한글 깨짐 발생
   - 원인: `BeautifulSoup(resp.content, "html.parser")` 사용 시 bs4 내부 인코딩 감지(`ptcp154`)가 UTF-8 대신 잘못 적용됨
   - 수정: `BeautifulSoup(resp.text, "html.parser")` — requests가 HTTP 헤더(`charset=utf-8`) 기준으로 정상 디코딩한 문자열을 전달
   - 교훈: 인코딩을 명시할 때는 `resp.content + from_encoding`보다 `resp.text`가 더 안전
   - 재수집 완료 (2회차) — 정상 확인

6. **`korea_stock_reader` 읽기 전용 권한 부여**
   - 기존에 계정만 생성되고 GRANT가 누락된 상태였음
   - `GRANT CONNECT`, `GRANT USAGE`, `GRANT SELECT ON ALL TABLES`, `ALTER DEFAULT PRIVILEGES` 적용

7. **중간 데이터 샘플 체크 추가** (10분 이상 수집 작업 공통 정책)
   - `scripts/crawl_sector.py`: 100번째 종목 수집 후 최근 5건 출력 + 비한글 감지 시 경고
   - `scripts/daily_update.py` STEP 1/2: 500번째 종목 완료 후 최근 5건 출력 + 이상값 비율 경고

### 📝 설계 결정

- **왜 flat 테이블?**: FnGuide FICS는 단일 문자열로 제공됨 → 계층 구조 불필요
- **왜 ETF 제외?**: ETF는 FICS 섹터 미적용 (ETF 페이지에 FICS 정보 없음)
- **왜 인포맥스 API 아님?**: 인포맥스는 업종/지수 API만 있고, 종목별 섹터 분류 API 없음

---

## 2026-03-03 (화) - TimescaleDB pg_restore 유니크 인덱스 소실 문제 조사 및 해결

### 🔍 문제 발견

다른 PC에서 dump 파일로 복원 후 `daily_update.py` 실행 시 ON CONFLICT 에러 발생.

```
ERROR: there is no unique or exclusion constraint matching the ON CONFLICT specification
```

### 📝 원인 분석

TimescaleDB **hypertable 레벨 유니크 인덱스는 `pg_dump`에 포함되지 않음** (설계상 의도적 제외).

- `pg_dump` 시 `uq_ohlcv_time_stock` 등 3개 인덱스가 dump에 없음 → `pg_restore` 후 소실
- 청크 레벨 PK(`649_649_ohlcv_daily_pkey`)는 dump에 있으나, 부모 인덱스 없이 복원 불가 → 조용히 실패
- 결과: 복원 후 hypertable에 유니크 제약 전혀 없음 → ON CONFLICT 동작 불가

이 문제는 2/24 유니크 인덱스 최초 생성 이후부터 잠재해 있었으나, 복원 테스트를 안 해서 몰랐음.

### ✅ 해결책: `scripts/restore_db.sh` 작성

`pg_restore` 후 유니크 인덱스 3개를 자동 재생성하는 스크립트 작성.

```bash
bash scripts/restore_db.sh <dump_file>   # 1줄로 복원 완료
```

내부 동작 (3단계):
1. 기존 DB 드롭 + 재생성 (세션 강제 종료 포함)
2. `pg_restore` 실행
3. 유니크 인덱스 3개 재생성 (`CREATE UNIQUE INDEX IF NOT EXISTS`)

### ❌ 시도했다 철회한 방법: DELETE+INSERT 방식

ON CONFLICT 없이 DELETE 후 INSERT 방식으로 변경 시도 → 아래 이유로 원상복구:
- **비원자성**: DELETE → INSERT 사이 장애 시 해당 날짜 데이터 유실
- **무결성 미보장**: 신규 청크(미래 데이터)에 DB 레벨 중복 방지 없음
- 복원 경험 차이 없음 (어차피 restore_db.sh 1줄)

### 📝 설계 결정 및 교훈

- **ON CONFLICT UPSERT 유지** — 원자성·무결성 측면에서 우월
- **복원은 항상 `bash scripts/restore_db.sh`** — bare `pg_restore`는 인덱스 소실로 사용 불가
- TimescaleDB를 사용할 때 pg_dump/restore는 반드시 인덱스 재생성 절차 포함 필요

### 현재 DB 현황 (복원 후)
- ohlcv_daily: **3,271,899건** (2022-01-03 ~ 2026-02-26)
- market_cap_daily: **3,271,899건**
- investor_trading: **10,075,252건**
- stock_sectors: **2,720건** (FICS 섹터 확인 2,607건 / NULL 113건)
- 유니크 인덱스 3개: `uq_ohlcv_time_stock`, `uq_mktcap_time_stock`, `uq_investor_time_stock_type` ✅

---

## 2026-03-01 (일) - DB 복원 (backup_20260227_2331.dump)

### ✅ 완료 작업

1. **DB 복원** — 회사 PC 덤프 파일 → 맥 DB 최신화
   - 파일: `backup_20260227_2331.dump` (187MB, pg_dump -Fc 포맷)
   - 방법: 기존 DB 드롭 후 재생성 → `pg_restore` (TimescaleDB 특성상 `--clean` 사용 불가)
   - 에러 673건은 모두 무시 가능 (hypertable 청크 제약조건 / role "postgres" 소유권 차이)

2. **복원 결과 검증**
   - ohlcv_daily: **3,271,899건** (최신: 2026-02-26) ✅
   - market_cap_daily: **3,271,899건** ✅
   - investor_trading: **10,075,252건** (최신: 2026-02-26) ✅
   - floating_shares: **1,034,865건** ✅
   - stocks: **3,797건** ✅

### 📝 TimescaleDB pg_restore 주의사항

- `pg_restore --clean` 사용 시 hypertable 청크 제약조건 드롭 실패 → **DB 드롭 후 재생성** 방식 사용
- role 소유권 오류(`role "postgres" does not exist`)는 기능에 영향 없음 — 무시 가능
- 복원 명령:
  ```bash
  dropdb -U unanimous0 korea_stock_data
  createdb -U unanimous0 korea_stock_data
  /opt/homebrew/opt/postgresql@17/bin/pg_restore -U unanimous0 -d korea_stock_data <dump_file>
  ```

---

## 2026-02-27 (금) - 2/26 데이터 수집 완료

### ✅ 완료 작업

1. **2026-02-26 데이터 수집 완료** (소요: 1시간 58분 25초)
   - OHLCV + 시가총액: 3,795건 저장 (실패 0건 — 2/25 신규 상장 4종목 포함 완벽 수집)
   - 투자자별 수급: 10,880건 저장 (2,720개 종목, 실패 0건)
   - 신규 상장/상장폐지: 없음
   - 품질 체크 5종 이상 없음

2. **특이사항 148건 감지**
   - 거래정지 82건 (만호제강·에이비프로바이오 신규 편입)
   - 거래정지의심 2건, 무거래(스팩) 4건
   - 가격급등 TOP 30: 한미반도체 +28.4%, 삼천당제약 +29.8% 등 반도체 강세
   - 가격급락 TOP 30: 한주에이알티 -18.0%, 서울바이오시스 -16.8% 등

### 현재 DB 현황
- ohlcv_daily: **3,271,899건** (2022-01-03 ~ 2026-02-26)
- market_cap_daily: **3,271,899건**
- investor_trading: **10,075,252건**

---

## 2026-02-26 (목) - 2/25 데이터 수집 + 신규 상장 4종목 자동 추가

### ✅ 완료 작업

1. **2026-02-25 데이터 수집 완료** (소요: 2시간 0분 3초)
   - OHLCV + 시가총액: 3,791건 저장 (실패 4건: 신규 상장 당일이라 데이터 없음 → 정상)
   - 투자자별 수급: 10,880건 저장 (2,720개 종목, 실패 0건)
   - 품질 체크 5종 이상 없음

2. **신규 상장 4종목 자동 추가** (STEP 0 sync_stock_master)
   - `0155N0`, `0162L0`, `0162M0`, `0162Z0` → stocks 테이블 INSERT
   - 현재 활성 종목: 3,795개 (전체 3,797건 중 2건 비활성)

3. **특이사항 151건 감지**
   - 거래정지 80건 (연속 90일 이상 포함), 거래정지의심 7건, 무거래(스팩) 4건
   - 가격급등 TOP 30 (참엔지니어링·경남제약·엠에스오토텍 등 +30.0% 상한가)
   - 가격급락 TOP 30 (스테이지원엔터 -30.0%, 캐리 -29.3% 등)

### 현재 DB 현황
- ohlcv_daily: **3,268,104건** (2022-01-03 ~ 2026-02-25)
- market_cap_daily: **3,268,104건**
- investor_trading: **10,064,372건**

---

## 2026-02-25 (수) - 94개 ETF 과거 데이터 보충 + 2/24 데이터 수집 + 보고서 개선 + 481200 처리

### ✅ 완료 작업

1. **94개 ETF 과거 OHLCV + 시가총액 보충** (집 dump → 현재 DB)
   - **원인**: 회사 DB의 2/24 xlsx 적재 시 `ON CONFLICT DO NOTHING` 으로 271건만 신규 삽입, 나머지는 기존 데이터(4건씩)가 있다고 판단해 스킵 → 실제로는 과거 데이터 미적재
   - **진단**: 집 dump(backup_20260224_2357.dump)와 현재 DB 비교 → 94개 ETF가 현재 DB에 4건(2/19~2/24), dump에 평균 160건
   - **처리**: dump 복원 후 해당 94개 ETF의 2/19 이전 데이터 15,268건 추출 → UPSERT
   - ohlcv_daily: 3,249,045 → **3,264,313건** (+15,268건)
   - market_cap_daily: 동일

2. **2026-02-24 데이터 수집 완료**
   - OHLCV: 3,791건 저장 (실패 1건: 481200)
   - 투자자별 수급: 10,880건 저장 (2,720개 종목, 실패 0건)
   - 품질 체크 5종 이상 없음
   - 소요 시간: 약 1시간 56분

2. **481200(SOL 미국테크TOP10인버스(합성)) is_active=FALSE 처리**
   - API code/expired 모두 미등재, 2/20 거래량=0, 2/23·2/24 데이터 없음
   - `UPDATE stocks SET is_active=FALSE, delisting_date='2026-02-24' WHERE stock_code='481200'`
   - 현재 활성 종목: 3,791개 (전체 3,793건 중 2건 비활성)

3. **보고서 가격급등/급락 TOP 30 개선** (`scripts/daily_update.py`)
   - **기존**: `THRESHOLD_PRICE_CHANGE=0.295` (29.5% 이상만) → 상/하한가 근접 종목만 표시
   - **변경**: 임계값 제거 → 전체 가격 변동 수집 후 급등 TOP 30 / 급락 TOP 30 표시
   - `price_changes` 임시 리스트에 30% 미만 전종목 수집 → 정렬 후 각 30개만 anomalies 추가
   - `TOP_PRICE_CHANGE_COUNT = 30` 상수로 관리

4. **TODO.md Grafana 원복**
   - 이전 세션에서 "Streamlit으로 대체 검토"로 잘못 기록된 것을 Grafana로 원복

---

## 2026-02-24 (화) - 94개 ETF 적재 + DB 유니크 인덱스 정비 + 프로젝트 정리

### ✅ 완료 작업

1. **94개 ETF OHLCV + 시가총액 적재 완료**
   - 엑셀(`raw_data/종목 결과.xlsx`) → ohlcv_daily + market_cap_daily UPSERT
   - 94개 종목 전체 매칭 성공 (case-insensitive UPPER 비교)
   - 각 15,271건 처리 (신규 271건, 기존 동일 15,000건 스킵)
   - 검증: ohlcv_daily 데이터 없는 활성 종목 **0개** 확인

2. **DB 유니크 인덱스 정비** (3개 테이블)
   - **발견**: ohlcv_daily, market_cap_daily, investor_trading 모두 UPSERT용 유니크 인덱스 누락
   - daily_update.py의 `ON CONFLICT` SQL이 지금까지 동작한 이유: 중복 삽입이 발생하지 않았을 뿐
   - 생성한 인덱스:
     - `uq_ohlcv_time_stock` ON ohlcv_daily (time, stock_code)
     - `uq_mktcap_time_stock` ON market_cap_daily (time, stock_code)
     - `uq_investor_time_stock_type` ON investor_trading (time, stock_code, investor_type)

3. **프로젝트 파일 정리**
   - 삭제 (1회성 스크립트): `scripts/load_etf_from_xlsx.py`
   - 삭제 (초기 적재 원본, ~249MB): `raw_data/temp/` (CSV 14개), `raw_data/종목 결과.xlsx`, `raw_data/1-종목코드_종목명.xlsx`
   - 삭제 (미사용 코드): `database/connection.py`, `utils/logger.py`, `utils/exceptions.py`
   - 삭제 (구버전 스키마): `database/schema/init_schema.sql`, `database/schema/alter_stocks_table.sql`
   - 삭제 (캐시): `.pytest_cache/`

4. **DB 검증 결과** (정리 후 최종 확인)
   - ohlcv_daily: 3,260,522건 / market_cap_daily: 3,260,522건 (일치)
   - investor_trading: 10,042,612건
   - 중복: 0건 (3개 테이블 모두)
   - OHLCV 논리 오류 (high<low): 0건
   - 음수 값: 0건
   - ohlcv 누락 종목: 0개

---

## 2026-02-24 (화) - 2/23 데이터 수집 + DB 정합성 정비 + API/코드 개선

### ✅ 완료 작업

1. **2026-02-23 데이터 수집 완료**
   - 오전 11:05 첫 수집 시도 → OHLCV 47% 실패 (1,835건)
   - 인포맥스 문의 결과: **당일 데이터 확정 시각** — 1차 16:40 / 2차 18:40 (19시 이후 완전)
   - 오후 13:44 재수집 → OHLCV 3,821/3,822 (실패 1건), 수급 2,747/2,747 (실패 0건)
   - 품질 체크 5종 전부 이상 없음
   - 오전 실패의 원인: 데이터 미확정 시간대 수집 (설 연휴 때문이 아님)

2. **보고서 버그 수정** (`scripts/daily_update.py`)
   - **가격급락 부호 버그**: `chg_rate = abs(close - prev) / prev` → `abs()` 제거 후 `signed_rate` 분리
     - 급락도 `+82.7%`로 표시되던 문제 → `-82.7%`로 정상 출력
   - **가격급등/급락 TOP 30 제한**: 각각 독립적으로 30개 제한 (초과 시 `TOP 30 / 전체 N건` 표시)

3. **`get_stock_codes()` 전면 개선** (`collectors/infomax.py`)
   - **문제**: API 1,000개 상한 → KOSDAQ ~800개, ETF ~72개 누락 (sync_stock_master 사각지대)
   - **원인 분석**: `stock/code` API 페이지네이션 없음, 파라미터 무시
   - **해결**: `code` 파라미터 2자리 prefix(00~49) 분리 + `startswith()` 필터링
     - KOSPI ST: market=1 단일 호출 (919개, 1,000 미만)
     - KOSDAQ ST: market=7 + prefix 분리 (1,801개 ✅)
     - ETF EF: type=EF + prefix 분리 (1,579개 ✅)
   - **MARKET_MAP 버그 수정**: API가 숫자코드가 아닌 한글 텍스트(`거래소(코스피)`) 반환 → 양쪽 대응
   - **equity_type 버그 수정**: `'ST'`만 체크하던 것을 `'주식'`도 추가 (한글 반환 대응)
   - 결과: 단일 호출 1,000개 → **3가지 구분(KOSPI/KOSDAQ/ETF) 전종목 수집**

4. **DB 정합성 정비**
   - `investor_trading` ETF 데이터 혼입 제거: **2,925,544건 삭제** (CSV 초기 적재 시 ETF 수급까지 포함됐던 오류)
     - 13,031,804건 → 10,139,228건 (ETF 잔여 0건)
     - 2월 전체 날짜 2,748개로 일관성 확보 (설 연휴 2/14~2/18 제외)
   - `floating_shares` 없는 ETF 3개 과거 OHLCV + 2/19 보충
     - 01669A(KCGI베트남) / 01777A(맵스미국11호) / 01221D(DH오토리드 9WR)
     - 2022-01-13~2026-02-19 데이터 삽입
   - ETF listing_date NULL 1,072개 → API에서 일괄 업데이트 → **잔여 1개** (481200만)
     - 0106J0(대신 KOSPI200 X클래스), 0120X0(유진 챔피언 X클래스): 2025-10-27 업데이트
     - **481200(SOL 미국테크TOP10인버스)**: API 어디에도 없음 → 청산 의심, 내일 재확인 필요

5. **기타 유형 종목 전면 제거** (`collectors/infomax.py` + DB)
   - **발견**: `equity_type not in {'ST','주식'} → market="ETF"` 로직으로 EN/MF/RT/IF/DR/SW/SR/EW/BC/FS 유형 종목들이 ETF로 오분류
   - 해당 유형: 수익증권(EN), 뮤추얼펀드(MF), 리츠(RT), 인프라펀드(IF), 파생상품(DR), 워런트(SW/SR/EW), 기업채권(BC), 구조화상품(FS) 등
   - **DB 삭제**: 30개 종목 (`stocks` 테이블) + 관련 OHLCV/시가총액 등 연쇄 삭제
   - **코드 단순화**: `get_stock_codes()`에서 기타 유형 단일 호출 제거 → KOSPI/KOSDAQ/ETF 3가지만 수집
   - **근거**: 이 시스템의 목적(주식+ETF 시세·수급)에 불필요한 유형, DB에 이미 있던 30개도 의도치 않게 혼입된 것

6. **수정주가(adj price) 현황 분석 및 주가이벤트 탐지 추가**

   **수정주가 현황 분석:**
   - DB 데이터 및 Infomax API 모두 **비수정주가(실제 체결가)** 임을 확인
     - 근거: 제일바이오(052670) 2026-02-09 주식병합 → 병합 전 2022년 가격이 소급 수정 안 됨 (3,205원 그대로)
     - 근거: Infomax API 백서(32페이지) 전체에 수정주가 관련 기능 없음 (API 미지원)
   - 엑셀 CSV도 비수정주가 (HTS 차트는 수정주가로 보이지만 export 데이터는 비수정)
   - **두 소스가 동일 기준** → CSV-API 간 연속성 이상 없음

   **수정주가 필요 시 옵션 (미래 대비):**
   - `pykrx` 라이브러리: `stock.get_market_ohlcv(..., adjusted=True)` — 특정 종목 즉석 조회용
   - `adj_close_price` 컬럼 추가 + 수정계수 테이블: 이벤트 발생 시 소급 갱신
   - 수정계수 뷰(View): 원본 보존, 조회 시 실시간 계산
   - **현재 결정**: 지금 당장 불필요 — 수정주가 기반 분석 시작 시점에 도입

   **`주가이벤트의심` 탐지 추가** (`scripts/daily_update.py`):
   - `THRESHOLD_PRICE_EVENT = 0.30` 상수 추가
   - ±30% 초과 변화 = 한국 가격제한(±30%) 밖 → 정상 거래 불가 → 이벤트 확정
   - 기존 `가격급등/급락`(±29.5%)과 분리: 이벤트 발생 시 `주가이벤트의심` 타입으로 별도 분류
   - 보고서 맨 앞에 `!!!` 경고박스 + 🚨 이모지로 강조
   - "수정계수 확인 필요 / 비수정주가 불연속 발생 가능" 안내 메시지 포함
   - 향 올라온 이벤트 방향별 힌트: 상승 → "주식병합/분할 의심", 하락 → "무상감자/대규모권리락 의심"

   **상장폐지 자동 처리 확인:**
   - 제일바이오(052670): 주식병합(2/09) 후 상장폐지(2/23) → `sync_stock_master()` 자동 처리 확인
     - DB: `is_active=FALSE, delisting_date=2026-02-23` ← `get_expired_codes()` API가 자동 감지
   - 상장폐지 종목은 다음날부터 수집 대상 자동 제외 (pykrx 추가 불필요)
   - 결론: `주가이벤트의심` 알림은 상장폐지 감지용이 아닌 **가격 단절 기록용** (수정주가 작업 시 기준점)

### 🎯 주요 결정사항

#### 1. 인포맥스 당일 데이터 확정 시각
- 1차: 16:40 / 2차: 18:40 → **19:00 이후 수집 = 항상 안전**
- 연휴·주말 무관 (기존 "연휴 직후 API 지연" 판단은 오인이었음 — 12:46 수집이 문제)
- 스케줄러를 19:00 이후로 변경하면 당일 데이터도 그날 수집 가능
- 기존 스케줄러 16:30은 `end = today-1` 로직으로 "어제" 데이터만 수집

#### 2. stock/code API 1,000개 상한 우회 전략
- 페이지네이션 파라미터 없음 (page, offset, pageSize 등 모두 무시됨)
- `code` 파라미터는 substring 검색 → 2자리 prefix 요청 후 `startswith()` 필터로 정밀 추출
- 2자리 prefix당 최대 127개 → 절대 1,000 초과 없음 (한국 주식 코드 현재 범위 00~49)
- 추가 API 호출 약 100회 → Lite 플랜 기준 ~2분 추가 소요

#### 3. investor_trading ETF 데이터 제거
- CSV 초기 적재 시 ETF 수급 데이터(978종목)까지 포함됐던 것 확인
- ETF는 투자자별 수급 수집 대상 아님 → 전체 삭제
- 2/19 이후 API 수집분은 처음부터 올바르게 KOSPI+KOSDAQ만 수집 중

#### 4. 수집 종목 유형 확정: KOSPI 주식 / KOSDAQ 주식 / ETF 3가지만
- EN/MF/RT/IF/DR/SW/SR/EW/BC/FS 등 기타 equity_type은 수집 대상 외
- `get_stock_codes()`: `_fetch({"market":"1","type":"ST"})` + `_fetch_split({"market":"7","type":"ST"})` + `_fetch_split({"type":"EF"})` 3호출로 단순화
- `stocks` 테이블의 market 값은 KOSPI / KOSDAQ / ETF 3가지만 존재 (기타 유형 삭제 완료)

#### 5. DB 데이터는 비수정주가 (수정주가 미제공)
- Infomax API 백서 전체에 수정주가 기능 없음 — `/api/stock/hist`는 실제 체결가만 제공
- 초기 엑셀 CSV도 비수정주가 (HTS 차트와 달리 export 데이터는 비수정)
- 수정주가 필요한 분석(백테스팅, 수익률 계산)은 pykrx 또는 별도 adj_close 컬럼 추가로 대응

#### 6. 주가이벤트 탐지 전략 확정
- **±30% 초과 = 이벤트 확정** (한국 가격제한 ±30% 밖은 정상 거래 물리적 불가)
- 이벤트 발생 → 보고서에 `주가이벤트의심 🚨` 경고박스로 기록 → 수정주가 도입 시 기준점으로 활용
- 상장폐지 감지는 `sync_stock_master()` + `get_expired_codes()`가 이미 자동 처리 → 별도 로직 불필요

### 💡 배운 점 / 인사이트
- API `success=True` + `results=[]`는 데이터 미확정(오전) 또는 진짜 없는 종목 두 가지 케이스
- ETF 중 특수 코드(01xxxA, 01xxxB 등 신탁형)는 일반 종목 조회 API에 미등재되나 hist 데이터는 존재
- 481200 같은 청산 ETF: expired API에도 없고 code API에도 없는 상태로 남아있을 수 있음
- `equity_type not in STOCK_TYPES → ETF` 로직은 기타 파생상품/펀드까지 ETF로 오분류 → 명시적으로 `type=EF`만 ETF 처리
- 상장폐지 시 Infomax hist API 데이터 없음(fail) + expired API 자동 감지 → 이중으로 커버됨
- 비수정주가 DB에서 ±30% 초과 가격 변동 = 이벤트(무상감자/주식병합 등) 100% 확정 신호

### 📌 다음 작업
- 481200 청산 여부 재확인 후 is_active=FALSE 처리
- 94개 ETF 과거 OHLCV 보충 (사용자가 엑셀 파일 제공 예정)
- 스케줄러 재가동 (19:00 이후로 시간 변경 고려)
- 서버 구축 (맥미니)

---

## 2026-02-23 (월) - 2/20 데이터 수집 완료 + 거래정지 탐지 로직 개선

### ✅ 완료 작업

1. **2026-02-20 데이터 수집 완료**
   - 백그라운드 실행: `PYTHONPATH=. PYTHONUNBUFFERED=1 nohup python -u scripts/daily_update.py 20260220 > logs/update_20260220.log 2>&1 &`
   - 첫 실행 시 Python 출력 버퍼링 문제로 로그 파일 비어있음 → `-u` + `PYTHONUNBUFFERED=1` 옵션으로 해결
   - 결과: OHLCV 3,822건 / 시가총액 3,822건 / 수급 2,747종목 — 실패 0건
   - 품질 체크 5종 이상 없음, 보고서 저장: `reports/daily_update_20260220.txt`
   - 가격급등 11건 (한화생명·미래에셋생명 등 보험주), 가격급락 1건 감지

2. **거래정지 탐지 로직 개선** (`scripts/daily_update.py`)
   - **기존**: `volume=0 AND close>0` → 단순 1일 체크 (노이즈 다수)
   - **개선**: DB 쿼리로 연속 무거래일수 계산, 임계값별 3단계 분류

   | 연속일 | 분류 | 대상 |
   |--------|------|------|
   | 1~2일 | 스킵 | 일상적 무거래 (소형주·ETF) |
   | 3~4일 | 거래정지의심 🟡 | 스팩 제외 |
   | 5일+ | 거래정지 🔴 | 스팩 제외 |
   | 5일+ (스팩) | 무거래(스팩) ⚪ | 스팩 종목 별도 분류 |

   - 신규 함수 `get_halt_suspects(conn, target_date)`: SQL Window Function(ROW_NUMBER)으로 연속일수 계산 (최대 90일 소급)
   - 상수 변경: `THRESHOLD_VOLUME_ZERO` → `THRESHOLD_HALT_SUSPECT=3`, `THRESHOLD_HALT_CONFIRM=5`
   - 보고서 type_order / emoji 추가 (거래정지의심🟡, 무거래(스팩)⚪)

### 🎯 주요 결정사항

#### 1. 거래정지 분류 기준 (3일/5일)
- **고려한 대안**: 전일 종가와 당일 종가 동일 여부 체크
- **기각 이유**: `volume=0`이면 종가는 항상 동일 → 조건 추가 효과 없음, 권리락/배당락 적용 시 오히려 진짜 거래정지를 놓칠 수 있음
- **채택**: 연속일수 기반 분류 — 단순하고 의미 있는 신호

#### 2. 스팩 별도 분리
- SPAC 합병 전까지 수개월 무거래가 정상 → 일반 거래정지와 동일 분류 시 노이즈
- `"스팩" in stock_name` 으로 판별, 5일+ 이상만 `무거래(스팩)` 으로 별도 표시

#### 3. 연속일수 조회 방식
- 매일 OHLCV 저장 후 DB에서 쿼리 (in-memory 처리 불가)
- `get_halt_suspects(conn, end_date)` → `analyze_anomalies(..., halt_suspects)` 로 전달
- Window Function: `ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY time DESC)` — 거래일 기준 연속일, 공휴일 자동 무시

### 💡 배운 점 / 인사이트

#### Infomax API 플랜별 속도 비교
| 플랜 | 제한 | 딜레이 | 예상 소요 | 요금 |
|------|------|--------|---------|------|
| Lite | 60회/분 | 1.05s | ~110분 | 10만원 |
| Standard | 120회/분 | 0.5s | ~55분 | 25만원 |
| Pro | 180회/분 | 0.33s | ~36분 | 50만원 |

- 현재 Lite 플랜 사용 (`collectors/infomax.py` REQ_DELAY=1.05)
- 플랜 업그레이드 시 코드 변경 없이 `REQ_DELAY` 값만 조정하면 됨

### 📌 다음 작업

- 2026-02-23 데이터 수집 (장 마감 후 16:30~): `python scripts/daily_update.py 20260223`
- 스케줄러 재가동: `nohup python schedulers/daily_scheduler.py &`
- 94개 ETF 시계열 데이터 보충
- 서버 구축 (맥미니)

---

## 2026-02-22 (일) - Phase 4 완료: 품질 체크·백업·종목 마스터 자동 갱신·프로젝트 정리

### ✅ 완료 작업

1. **데이터 품질 체크 자동화** (`validators/quality_checks.py`)
   - 5종 체크: NULL 비율, 중복 레코드, OHLCV 논리 오류(high<low 등), 거래일 연속성 갭, 수급 합산 검증
   - 결과 `data_quality_checks` 테이블 저장 → 이력 관리

2. **PostgreSQL 백업 자동화** (`scripts/backup_db.py`)
   - `pg_dump -Fc` 포맷 (압축, `pg_restore`로 복구 가능)
   - `backups/backup_YYYYMMDD_HHMM.dump` 저장
   - 7일 초과 파일 자동 삭제, 빈 파일(실패 잔재) 자동 삭제

3. **품질/수집 모니터링 스크립트**
   - `scripts/data_quality_report.py`: 최근 N일 품질 체크 이력 테이블 출력
   - `scripts/check_collection_status.py`: 날짜별 수집 현황, 누락 감지

4. **스케줄러에 주간 백업 추가** (`schedulers/daily_scheduler.py`)
   - 매주 일요일 03:00 KST — `run_backup()` 자동 실행

5. **종목 마스터 자동 갱신** (`collectors/infomax.py` + `scripts/daily_update.py`)
   - `InfomaxClient.get_stock_codes()`: `/api/stock/code` — 현재 상장 종목 전체 조회
     - API 필드: `code`, `kr_name`, `market`(숫자코드→KOSPI/KOSDAQ/ETF 변환), `equity_type`, `isin`, `listed_date`
     - equity_type ST 외 → "ETF"로 통합
   - `InfomaxClient.get_expired_codes(start_date, end_date)`: `/api/stock/expired` — 상장폐지 종목 조회
     - API 기본값 `startDate=today-365` 한계 → DB `MIN(listing_date)`를 `startDate`로 전달
   - `sync_stock_master()`: 신규 상장 `ON CONFLICT DO NOTHING` INSERT, 상장폐지 soft delete (`is_active=FALSE`, `delisting_date`)
   - `daily_update.py` STEP 0으로 실행 (매일 자동)

6. **Read-only DB 계정 생성**
   - 계정명: `korea_stock_reader`
   - 타 프로젝트에서 읽기 전용으로 DB 접근 가능

7. **프로젝트 불필요 파일 정리**
   - 삭제: `api/` 폴더, `etl/` 폴더, `notebooks/` 폴더
   - 삭제: `tests/test_etl/`, `tests/test_collectors/` (빈 폴더)
   - 삭제: `scripts/` 1회성 스크립트 전체 (test_*, load_*, inspect_*, check_environment.py, crawl_floating_shares.py, setup_readonly_user.py, *.sh)
   - 이동: `database/korea_stock_data.dump` → `backups/`
   - 제거: `daily_update.py`의 불필요 import (`threading`, `io`)

### 🎯 주요 결정사항

#### 1. `/api/stock/expired` startDate 커버리지
- API 기본값 `today-365`로는 전체 폐지 이력 누락 가능
- 해결: `sync_stock_master()`에서 `SELECT MIN(listing_date) FROM stocks WHERE is_active=TRUE`로 최초 상장일 쿼리 → 해당 날짜부터 폐지 이력 조회

#### 2. 1회성 스크립트 삭제 결정
- 초기 적재·검증용 스크립트들은 이미 역할 완수, 코드베이스 정리 차원에서 전부 삭제
- git 이력에 남아 있으므로 필요 시 복구 가능

### 📌 다음 작업

- 2026-02-20 데이터 수동 수집: `python scripts/daily_update.py 20260220`
- 스케줄러 재가동: `nohup python schedulers/daily_scheduler.py &`
- 94개 ETF 시계열 데이터 보충
- 서버 구축 (맥미니)

---

## 2026-02-20 (금) - 스케줄러 버그 수정 / 2-19 데이터 수집 / 재수집 모드 추가

### ✅ 완료 작업

1. **`daily_scheduler.py` next_run_time 버그 수정**
   - 증상: `AttributeError: 'Job' object has no attribute 'next_run_time'`
   - 원인: APScheduler 4.x에서 `next_run_time` 속성 제거 + `scheduler.start()` 전 호출
   - 수정: `CronTrigger.get_next_fire_time(None, now)` 로 대체

2. **2026-02-19 데이터 수집 (설 연휴 직후 첫 거래일)**
   - 12:46 첫 수집: 3,820개 중 2,077개만 성공 (45% 실패)
   - 원인 분석: 인포맥스 API가 설 연휴(2/16~18) 직후 데이터 처리 중 — 오전에는 일부 종목 데이터 미준비 상태 (`success=True, results=[]` 반환)
   - 오후 15:26 재수집: 1,743건 추가 성공 (실패 0건) → 최종 3,820건 완성

3. **`--missing-only` 재수집 모드 추가 (`scripts/daily_update.py`)**
   - 특정 날짜에 이미 수집된 종목은 스킵, 누락된 종목만 재수집
   - 소요 시간 ~53분 (전체 재실행 2시간 대비 1/3)
   - 사용법: `python scripts/daily_update.py 20260219 --missing-only`
   - 내부 함수: `get_missing_ohlcv_stocks()`, `get_missing_investor_stocks()` 추가

### 🎯 주요 결정사항

#### 연휴 직후 데이터 수집 전략
- 설날/추석 연휴 후 첫 거래일은 **오전 수집 금지** → 오후 늦게 수집
- 또는 수집 후 실패 종목 많으면 `--missing-only`로 재수집

#### 인포맥스 API 특성 확인
- API는 항상 `HTTP 200, success=True` 반환
- 데이터 없으면 `results=[]` (success=False는 실제로 발생 안 함)
- 스케줄러 `end = today-1` 로직: 매일 16:30 실행 시 "어제"까지만 수집
  → 당일 데이터는 다음날 스케줄러에서 수집됨

### 📊 최종 데이터 현황 (2026-02-20 기준)

| 테이블 | 레코드 수 | 최신 날짜 |
|--------|----------|---------|
| ohlcv_daily | 3,261,771건 | 2026-02-19 |
| market_cap_daily | 3,261,771건 | 2026-02-19 |
| investor_trading | 13,042,796건 | 2026-02-19 |

### 📌 다음 작업

- 스케줄러 실전 가동 (16:30 자동 실행 모니터링)
- 94개 ETF 시계열 데이터 보충
- 서버 구축 (맥미니 구매 후 설정)

---

## 2026-02-20 (금) - 일별 업데이트 파이프라인 구축 + 멀티스레드 병렬화

### ✅ 완료 작업

1. **인포맥스 API 수집기 구현 (`collectors/infomax.py`)**
   - `InfomaxClient` 클래스: thread-safe 공유 rate limiter
   - `get_hist()`: OHLCV + 시가총액 (`/api/stock/hist`)
   - `get_investor()`: 투자자별 수급 4개 타입 (`/api/stock/investor`)
   - 핵심: 클래스 변수 `_rate_lock`, `_rate_last_call`으로 멀티스레드 간 rate 공유

2. **일별 업데이트 스크립트 구현 (`scripts/daily_update.py`)**
   - ThreadPoolExecutor(max_workers=4) 병렬 수집
   - 특이사항 자동 감지: 거래정지, OHLCV오류, 가격급등락(±29.5%), 대규모순매수도(500억↑)
   - 보고서 자동 생성: `reports/daily_update_YYYYMMDD.txt`

3. **스케줄러 구현 (`schedulers/daily_scheduler.py`)**
   - APScheduler BlockingScheduler, 매일 16:30 KST (월~금)

### 🎯 속도 분석 결과

- Infomax Lite 플랜: 60회/분 = 1.05s/req
- 전체 종목: OHLCV 3,820건 + 수급 2,748건 = 6,568 API calls
- 최소 소요: 6,568 × 1.05s = **1시간 55분** (Rate limit이 근본 한계)
- 멀티스레드 효과: latency(~0.1s) overlap으로 **~10% 개선** (2시간 → 1시간 50분)

### 🔍 bulk API 탐색 결과

- 날짜별 전 종목 bulk API 없음 (종목별 호출만 지원)
- 속도 개선을 위해선 Infomax 상위 플랜 업그레이드만이 해결책

### ⚠️ 주의사항

1. **독립 rate limiter 사용 금지**: 스레드별 독립 throttle 사용 시 access_limit 에러
2. **DB 쓰기 메인 스레드 전용**: psycopg2는 thread-safe하지 않아 메인 스레드에서만 UPSERT

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
