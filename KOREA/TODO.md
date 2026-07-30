# 📝 TODO - 작업 목록

> **마지막 업데이트**: 2026-07-30
> **현재 Phase**: Phase 4 + Phase 5(배당) + KRX 휴장일 + KOSPI200/KOSDAQ150 SCD2 + ETF 일별 스냅샷 + 지수/지수선물/주식선물 일별 + **Phase 6 분봉**: 종목/ETF 30초봉 ✅ / 1분봉 1/2~4/24 ✅ + **Phase 7**: 지수/지수선물/주식선물 30초봉 + 분봉/일별 NEAR/NEXT 통합 view ✅ + **5/15-16 사고 대응** ✅ + **수정주가 시스템 (5/16-17) ✅** + **5/24 운영/문서 정비 ✅**

---

## 🆕 2026-07-30 — LENS 문의: ohlcv_daily.adj_close 대량 결측 (원인 3종 + 전면 수정)

LENS 통계차익 엔진 왜곡 추적 중 발견. 최근 3년 2,553,933행 중 604,971행(23.7%) NULL.

### NULL 구조 (조사 결과)
| 구분 | NULL 행 | 종목 | 원인 |
|---|---|---|---|
| A. 2024-04-22 이전 | 1,689,043 | 3,264 | **LS t8451 500봉 상한** |
| B. 2026-06-02~09 | 1,008 | 205 | ghost_delist 사후 백필분 |
| C. **2026-07-28** | 3,855 | 3,855 | DNS 사고 → 파이프라인 미실행 |
| D. 상시 | 27,193 | 177 | **부트스트랩 트랩** |

### 원인 1 — LS t8451 500봉 상한 (A)
- `2024-04-23 ~ 2026-05-16 = 정확히 500 거래일`, adj 채워진 행 종목당 평균 508행. 2026-05-16 백필이 4년치를 요청했으나 LS가 최근 500봉만 반환
- **cts_date 페이징은 동작하지 않는다**: 응답 `cts_date='20240705'`를 정상 반환하지만 그 값으로 재호출하면 **1회차와 완전히 동일한 500봉**이 다시 온다(실측). 기존 루프는 `nd in seen_cts`로 탈출 → 500봉이 상한
- [x] **`get_daily_bars` 페이징을 edate 스텝 방식으로 교체** — `edate = 직전 청크 최오래된날 - 1일`. 검증: 005930/069500 모두 **1,117봉(2022-01-03~2026-07-29)** 확보, 1일 조회 회귀 정상(1봉). MAX_CHUNKS=40 + 진전없음 탈출로 무한루프 방지

### 원인 2 — 파이프라인이 target_date 하루만 처리 (B, C)
- `run_adjusted_price_pipeline` STEP 1이 `WHERE a.time = target_date`만 갱신 → **나중에 백필·보충으로 들어온 행은 영영 NULL**. 수집이 하루라도 밀리면 그날 adj가 통째로 빔(구조적)
- [x] **`backfill_missing_adj(conn, start, end)` 신설** — 구간 내 NULL 행을 날짜 오름차순으로 하루씩 `raw × 직전 factor` 적용. STEP 1이 이걸 최근 `ADJ_LOOKBACK_DAYS=15`일에 대해 호출하도록 교체

### 원인 3 — 부트스트랩 트랩 (D)
- 기존 STEP 1은 INNER JOIN이라 **직전에 factor가 있는 종목만** 갱신. 신규 상장은 첫날 직전 행이 없어 NULL → 둘째 날의 '직전'인 첫날도 NULL → **영구히 갇힘**. 실측 83종목이 adj를 한 번도 못 받음
- [x] **`COALESCE(prior_factor, 1.0)`으로 부트스트랩** — 수정주가 관례상 "이벤트 없음 = adj와 raw 동일". 실제 이벤트는 STEP 2/3(gap>15% → LS 전체 history 재호출)이 교정
- 검증: 6/2~6/9 복구 시 **59종목 288행이 1.0이 아닌 실제 factor를 승계** → 부트스트랩이 실제 값을 덮어쓰지 않음 확인

### Q4 — 완결성 체크 (LENS 요청)
- [x] **`check_adj_completeness(conn, target_date)`** — `close_price`는 있는데 `adj_close`가 NULL인 행 집계. 당일 + 최근 15일 누적. issue 시 `data_quality_checks(table_name='ohlcv_daily', check_type='adj_close_completeness')` 영속화. `run_adjusted_price_pipeline` 반환값에 `completeness` 포함
- 이번 7/28 건은 이 체크가 있었으면 다음 날 아침에 잡혔다

### 실행
- 복구(SQL, LS 호출 0): tmux `adj_repair`, `scripts/_adj_repair_run.py 20240423 20260729`. 멱등 — 일자별 커밋이라 중단/재실행 안전
- 과거 백필(LS): tmux `adj_backfill`, `backfill_adjusted_daily.py --from 20220103 --to 20240422`. 종목당 4콜(raw/adj × 2청크)
- **주의**: `fetch_scope`가 `is_active=TRUE`만 → A구분 3,264종목 중 **비활성 46종목은 백필 범위 밖**. 필요 시 `--codes`로 별도 처리
- **확인 필요**: 복구가 쓴 부트스트랩 1.0과 과거 백필이 쓴 LS 실제 factor의 경계 정합성 (양쪽 완료 후 factor 불연속 점검)

---

## 🆕 2026-07-30 — DNS 플래핑으로 daily_update 사망 + 무알림 실패 3종 정리

### 사고 경과 (2026-07-29 02:00)
- **02:02~02:03 KST Tailscale MagicDNS(100.100.100.100) 플래핑** → `openapi.ls-sec.co.kr` 이름 해석 ~1.5분 실패
  - `journalctl -u systemd-resolved`: `Using degraded feature set UDP instead of UDP+EDNS0` → `Flushed all caches` ×2
  - `/etc/resolv.conf` = `nameserver 127.0.0.53` + `search tail5eb786.ts.net` → **DNS 경로가 Tailscale 단일**
- LS t8451 호출이 `socket.gaierror [Errno -3]` → `daily_update.main()`이 `sys.exit(1)` → 7/28 데이터 통째 결손
- **알림이 안 갔다**: `job_daily_update`의 핸들러가 `except Exception`인데 `SystemExit`은 `BaseException` 상속 → 미포착 → APScheduler까지 올라가 `on_job_error`가 **로그만** 남김. 사용자가 하루 뒤 질문으로 발견
- 데이터는 익일 자동 복구됨(분봉 일배치 갭 채움 3,148,645행 = 2일치 + 08:30 종합 보충). **실제 손실 0**

### 조치
- [x] **`job_daily_update`가 `SystemExit` 포착** — `e.code not in (0, None)`이면 fail 처리 후 알림. `sys.exit(0)`은 정상 통과
- [x] **`on_job_error`에 알림 추가** — 잡 자체 핸들러를 빠져나온 예외는 무엇이든 텔레그램으로. 최후 안전망(어떤 잡도 조용히 죽지 못하게)
- [x] **LS 클라이언트 `ConnectionError` 전용 긴 재시도** (`collectors/ls_api.py`)
  - `_post_resilient()` 신설 — 5회 × (15/30/60/120/180s) = 호출당 최대 ~6.4분. `session.post` 5개 호출부(토큰 발급 포함) 전부 경유
  - **ConnectionError만** 대상. `_HardTimeout`/`ReadTimeout`/5xx/429는 기존 정책 그대로 전파 (요청이 나가지도 못한 실패 = LS 무죄라 길게 기다리는 게 맞음)
  - `sleep`은 `hard_timeout` 컨텍스트 **밖**에서 (안에서 자면 alarm에 즉사)
  - 지속형 장애 방어: 긴 대기는 **프로세스 전역 예산 900s**(`_conn_wait_left`, 클래스 lock)에서 차감, 소진 시 fail-fast. workers=4 × 3,900종목이 각자 6.4분 기다리면 배치가 며칠이 되는 문제 회피
  - 검증 4종: 계속 실패→5회 재시도 후 전파 / 3번째 성공→복구 / ReadTimeout→즉시 전파 / 예산0→즉시 포기
- [ ] **DNS 이중화 (미착수)** — 근본 처방이나 `/etc/resolv.conf`·Tailscale DNS는 서버 전역 + LENS 영향 범위라 별도 판단
- [x] **`verify_scheduler_sync.sh` PID 오인 fix** — `pgrep -f`가 **명령줄에 문자열이 든 셸까지** 잡는 게 원인(파이프라인 부모가 아니었음). 재시작+검증을 한 줄에서 돌리면 래퍼 셸 cmdline에 `python schedulers/daily_scheduler.py …`가 통째로 들어가고, 셸이 python보다 먼저 떠 PID가 작으므로 `head -1`이 그걸 집어 시작 시각을 오판(실측: 스크립트 3853175 vs 진짜 3853260)
  - `/proc/<pid>/cmdline` 파싱으로 교체 — **argv[0]에 python 포함 + 인자에 스크립트 경로**인 것만 인정. 자기 자신($$) 제외. 2개 이상이면 중복 가동 경고 후 가장 오래된 것(상주 데몬) 기준
  - 검증: pgrep에 실제로 잡히는 미끼 셸(`bash -c '… python schedulers/daily_scheduler.py …'`)을 띄워 배제 확인 + 진짜 python 인식 확인

### 후속 (같은 날 오후) — 외인 부분 수집 발견 + B/C 조치
- **증상**: 08:30 보충이 7/28 외인 2,645건은 완벽히 채웠는데 **7/29는 1,415건만** 채우고 "완료"로 끝남 (알림엔 `foreign: 4060`만)
- **원인**: 경합/한도 아님. flush된 로그가 결정적 — `7/28: [2645/2645] 성공:2645 실패:0` vs `7/29: [500/2645] 성공:283 실패:217 … [2645/2645] 성공:1415 실패:1230`. **첫 500건부터 실패율 46%로 균일** = 한도 소진(초반 성공→후반 실패)이 아니라 **인포맥스에 7/29 외인이 46%만 등록된 상태**였음. 두 패스 모두 2,645종목 전체를 60 RPM으로 정상 처리
- [x] **(C) 빈 응답 종목 수 노출** — `run_supplement_pipeline` 요약에 `foreign_fail` / `foreign_fail_days` 추가, 08:30 알림에 `⚠️ 외인 빈응답 N종목 (날짜:건수)` 표기. 실패 카운트는 이미 집계돼 있었는데 **알림에 안 실려서** 사용자가 부분 수집을 못 알아챈 게 핵심
- [x] **(B) 잡 마커로 in-process 잡 노출** — `schedulers/job_state.py` 신설. 스케줄러가 `with infomax_busy(...)`로 표시, 백필 `maybe_pause_for_daily_update()`가 `active_job()` 확인. 잡당 파일 1개(겹침 대응) + PID 기록(stale 자동 청소). 상세는 `docs/스케줄러_운영.md`
  - 이번 사고의 원인은 아니었으나, 08:30 잡 종료가 10:50까지 밀려 백필 회피창(~10:05)이 어긋난 건 실재 → 예방 차원
- **7/29 외인 1,230종목은 미복구** (인포맥스 일별 한도 소진으로 당일 재시도 불가). 다음 08:30 보충이 최근 3영업일에 7/29를 포함해 자동 재시도

### 참고 — 조사 중 헛짚은 것 (재발 방지)
- **`tee` 파이프 통과 시 Python stdout이 블록 버퍼링** → 로그가 40분 멈춘 것처럼 보임. 살아있는지는 로그가 아니라 `/proc/<pid>/stat` CPU 증가 + `ss -tnp` 연결로 판정. (프로세스 재시작 시 버퍼가 flush되므로 사후 분석에는 로그가 남는다 — 실제로 이 flush된 `성공/실패` 라인이 원인 규명의 결정타였음)
- **외인 수집 속도** = 종목당 1콜 × 60 RPM → 2,645종목 ≈ **44분**. 커밋이 500건 단위라 "500건/분"으로 오독 금지
- **적재 행수 ÷ 소요시간 ≠ 처리 속도** — 빈 응답이 섞이면 처리는 다 하고도 행수가 적다. "1,415행/46분=30.7"을 처리속도로 읽고 경합이라 오판했으나 실제 처리는 57.5종목/분(정상). 속도는 **행수가 아니라 처리 종목 수**로 계산할 것
- **`verify_scheduler_sync.sh` PID** = 파이프라인 부모. 실제 python은 `pgrep -af daily_scheduler`로 별도 확인 (이번에 "프로세스 죽었다" 오판 유발)

---

## 🆕 2026-07-28 — ETF PDF 백필 크래시(70h 방치) fix + 상장 전 마스터 행 정리

### (1) 백필 크래시 — 긴 sleep 중 DB 커넥션 사망
- **증상**: `backfill_etf_pdf.py`가 **2026-07-24 10:00에 크래시 후 70시간 방치**. 9,000/64,095(14.0%)에서 정지, 아무도 모름(백필은 알림 대상 아님)
- **원인**: 한도 초과 → `wait_until_midnight()` ~20h sleep → 그 사이 커넥션 끊김 → 깨어나 재시도 시 `OperationalError`. **재시도가 `except InfomaxDailyLimitError:` 블록 *안*에 있어 형제 `except (OperationalError, InterfaceError)`가 못 잡음** → 프로세스 종료. (같은 try의 sibling except는 다른 except 안의 예외를 잡지 못함)
- [x] **`_ensure_conn(conn)` 신설** — `SELECT 1` ping + 죽었으면 재연결, ping 후 `rollback()`(idle in transaction 방지). 1 TPS라 왕복 비용 무시 가능
- [x] **재시도를 except 블록 밖 루프로 이동** — 매 시도 직전 `_ensure_conn` 호출(최대 3회). `finally: conn.close()`도 None 가드
- [x] **검증**: `pg_terminate_backend`로 7/24 상황 재현 → 구버전 `OperationalError`로 사망, 신버전 재연결 후 정상

### (2) 상장 전 마스터 행 — 인포맥스 마스터 API가 날짜를 무시
- **증상**: `etf_master_daily`에 `snapshot_date < listing_date`인 행 **1,750건 / 71종목** (예: HANARO K휴머노이드테마TOP10 상장 2/26인데 1/2 행에 순자산 254억)
- **원인**: 미상장 종목 조회 시 **PDF는 빈 응답이지만 마스터 API는 날짜 무시하고 현재 값 반환** → 백필이 요청 날짜로 stamp
- [x] **`fetch_listing_dates()` 신설 + work_list에서 `d < listing_date` 페어 제외** — 신규 발생 원천 차단. 덤으로 100% 빈 응답 확정 콜 4,592건(잔여의 8.2%) 절감 → 15.6h→11.8h
- [x] **기존 1,750행 삭제** (백업 후). 삭제 전 안전 검증 2종: ①71종목 전부 상장 후 행 보유(종목 소멸 없음 → LENS kr_name 룩업 안전) ②71종목 전부 상장 전 OHLCV 0건(listing_date 신뢰성 확인). PDF 오염은 0건, 의존 view 없음
- **원칙**: `snapshot_date=실측` — 7/21 마스터 완결성 때 carry-forward를 거부한 것과 같은 기준

---

## 🆕 2026-07-26 — stockfut 휴장일 fallback 재발 (7/17 제헌절) + "감지 후 방치" fix

- **증상**: `futures_ohlcv_intraday`에 7/17(휴장) 30초봉 548종목 **449,908행** 존재. 값이 7/16과 100% 동일(행수/close/volume 전부) — LS t8406 휴장일 fallback 복제본. 주식 30초/일봉은 정상적으로 7/17 없음
- **원인 2단**:
  1. 7/17 제헌절이 `krx_holidays` 미등록 → `is_market_closed` skip 가드 미작동 (사후 7/25 `ohlcv_gap`으로 등록됨)
  2. **`_verify_stockfut_loaded`는 3회 모두 정확히 감지했으나 오염 행을 지우지 않음** — 감지만 하고 데이터는 그대로 방치. 게다가 fallback인데도 5분 간격 3회 재시도(30분 낭비, 같은 복제본 재수신)
- **부작용**: 다음 영업일 검증의 `prev_biz`가 오염된 7/17을 참조 (7/20 검증이 7/17과 비교됨)
- [x] **오염 행 삭제** — 449,908행 제거. 삭제 전 CSV 백업. 삭제 후 삼성전자 근월물 `A1168000` 17거래일로 정정(현물·ETF와 일치), 7/24 `prev_biz`가 7/23으로 복귀
- [x] **`_purge_stockfut_day(day)` 신설** — 해당 KST 하루치 30초봉 삭제 + 행수 반환
- [x] **`_verify_stockfut_loaded` 반환 3-tuple 확장** — `(ok, msg, reason)`, reason=`ok/skipped/no_actives/insufficient/fallback_duplicate/error`. "재시도가 의미 있는 실패"와 "무의미한 실패"를 호출부가 구분
- [x] **`fallback_duplicate` → 즉시 purge + 재시도 중단 + 전용 알림** (삭제 행수/중단 사유/휴장일 여부 판단 기준 포함). 부분 적재(`insufficient`)는 historical 불가라 기존대로 보존+재시도 유지
- [x] **과거 전수 검사** — 휴장일/주말 선물 데이터 0건, 직전일 지문 완전일치 0건 → 7/17이 유일 사례 (점검 쿼리는 `docs/스케줄러_운영.md`에 보존)
- **휴장일 자동 등록은 의도적으로 미채택** — 오탐 시 거래일이 휴장 처리돼 `daily_update`가 통째로 skip되는 더 큰 사고. 이득은 거의 없음(stockfut은 historical 재수집 불가 + `ohlcv_gap`이 자가치유)
- **교훈**: 감지 ≠ 복구. 되돌릴 수 있는 오염은 감지 시점에 되돌린다

---

## 🆕 2026-07-09 — futures_master.json HPSP 누락 (SP 문자열 버그) 수정

- **증상**: LENS가 쓰는 `futures_master.json`(daily_update 5:30 export)에 HPSP 종목선물(A1B67000, 202607 근월) 누락 (273종목)
- **원인**: 스프레드 제외 필터 `"SP" in hname`(부분문자열) → **"HPSP"의 "SP"** 오탐 → HPSP 단일선물(F)이 스프레드로 오인돼 제외. `select_near_next_two`(ls_api.py) + `export_futures_master_json`(daily_update.py) 2곳
- [x] **수정**: `"SP" in hname.split()`(토큰 매칭). 재export → 274종목, HPSP 정상. 스케줄러 재시작 반영. 영향=HPSP 1종목뿐
- **LENS 답변**: 유니버스=LS t8401(3083행), 근/차월 2개, 매핑(base_code/front.code) export 포함됨. 금양=만기경과로 라이브 계약 없어 정상 제외
- [x] **신규상장 드롭 갭 해소 (2건)**:
  - export name join을 `futures_underlyings`(수동시딩) → `stocks` 직접으로 변경 (신규 자동 반영, 274종목 동일)
  - DB 선물 OHLCV 수집: `sync_stockfut_underlyings` 추가 — 매일 02:00 t8401(LS·무상한)에서 단일선물 유도(shcode[1:3]=underlying_code, basecode[1:]=stock_code)해 `futures_underlyings` upsert. 신규 종목선물 자동 등록+수집. (get_future_codes는 1000행 상한=166종목이라 부적합)

---

## 🆕 2026-07-06 — 유동주식수(floating_shares) 주간 갱신 복구 + 선물 L NEXT 완료

### 유동주식수 갱신 끊김 → wisereport 소스로 복구
- `floating_shares.floating_shares`/`floating_ratio`가 **2026-02-19 이후 갱신 끊김** — 주간 잡(`update_listed_shares.py`)은 `total_shares`(LS t1102)만 갱신, 유동주식 채우는 코드가 repo에 없었음(2월 데이터는 일회성)
- 소스 조사: LS·인포맥스·Naver메인·FnGuide 전부 상장주식수만 → **wisereport**(`navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd=XXX`, =Naver 기업개요 iframe)의 '발행주식수/유동비율' **정적 파싱**으로 확정 (2월 원본 소스. 사용자가 coinfo 링크로 지적 — main.naver엔 없고 coinfo/wisereport에 있음)
- [x] `update_listed_shares.py`에 **Phase 2** 추가 — 비ETF 활성종목 유동비율 수집 → `floating_shares = total_shares × 비율`, None 가드. 일요일 03:30 주간잡에 통합
- [x] 즉시 복구: base_date 2026-07-05 **2,716종목** 채움(none 3), 스케줄러 재시작 반영

### 선물 L NEXT 백필 완료
- [x] 주식선물(L, 275종목) NEXT 안전윈도우 백필 6/30~7/6 자동 완주 → 275/275, 차근월 실거래 정상. (F+L 모두 완료) → `futures_backfill` 세션 자동 종료
- [ ] **`futures_daily_with_class` view 후속** (F·L NEXT 일봉 수정 완료로 이제 가능) — NEXT를 일봉 테이블 기반으로 전환 시 2022+ 전체이력 일관

---

## 🆕 2026-07-05 — 섹터(stock_sectors) NULL 참사 + 크롤러 FnGuide→Naver 교체

### 사고
- LENS가 섹터 폴백(시장명)만 표시 → `stock_sectors` 2,748종목 중 **2,719개 fics_sector=NULL** (7/4 18:30 크롤 실행이 덮음)
- 원인 3중: (1) **FnGuide가 FICS를 JS 렌더링으로 이동** → 정적 크롤 전량 None (2) `crawl_sector.py` UPSERT에 **None 가드 없음** → None으로 좋은 값 덮음 (3) 7/4 실행이 이 조합 실행
- 토요일 휴장과 무관 — 섹터는 시장 개장과 무관한 분류(reference) 데이터

### 복구 조사 (전부 막힘 → 새 소스)
- 7/4 이전 DB 백업 없음(주간백업 최신 1개=손상후) / PITR 불가(archive_mode=off) / LENS 섹터 export 없음(라이브 조회)
- pykrx 섹터=빈 df(KRX 변경), KRX 직접=OTP/bld 미해결, DART induty=KSIC(투자섹터 아님)
- **Naver 금융이 유일 정상** — 종목페이지 `a[href*=upjong]`에 GICS식 업종 정적 노출 (utf-8)

### 완료
- [x] **None 가드** (`crawl_sector.py`) — 유효 섹터만 UPSERT (커밋 e67f046)
- [x] 대형주 244개 5/15 xlsx로 임시 복구 (중간 단계)
- [x] **크롤러 Naver로 교체** (`fetch_fics_sector`/`extract_fics_sector` 재작성, 컬럼명 fics_sector 유지) + **전체 재크롤** → 활성 2,719종목 100% (NULL 0), 88섹터 (커밋 811cf7d/…)
- [x] 스케줄러 재시작 — 10/4 분기 크롤이 새 코드(Naver+가드) 사용하도록 반영
- 상폐 29종목만 옛 FICS 명칭 잔존 (is_active=false, LENS 미표시 — 무해)

### ⚠️ LENS 후속 (사용자)
- **섹터 분류체계 변경**: FICS("반도체 및 관련장비") → Naver GICS식("반도체와반도체장비"/"화학"/"은행"). LENS에서 섹터명 하드코딩 그룹핑/필터 있으면 새 명칭으로 조정 필요. 컬럼명은 유지라 쿼리는 안 깨짐.

---

## 🆕 2026-06-26 — DB 전수 검증(에이전트 7개) + 선물 차근월 버그 + ETF 5일 FIFO

### 발견·수정 (검증 중 드러난 실데이터 버그)
- [x] **5/18~20 외인·수급 갭 복구** — 5월 사고 잔여 미백필분(5/20 외인 전량 0). 날짜 명시 missing_only로 재수집, 인포맥스가 35일 전 과거분 서빙 → 2,646~2,647/10,872~10,884로 복구.
- [x] **선물 차근월(NEXT) 계약 오선택 버그 (2건)** — `futures_ohlcv_daily` NEXT 전체가 잘못된 계약이었음:
  - (A) `/api/future/2active`는 날짜마다 모든 원월물 반환인데 dedup이 **최원월물(2028-12, OHLC=0 미거래)** 유지 → 진짜 차근월 버림. `pick_nearest_deferred`(infomax.py, kr_name 만기 최소) 헬퍼로 수정. daily_update + backfill 공통 적용.
  - (B) 백필 700일 청크가 2active 1000행 한도 초과 → 과거(2024) truncate 누락. NEXT는 **90일 청크**로 분할. `backfill_futures_ohlcv`에 `underlying_types` 필터 추가.
  - 지수선물(F) 전기간 재수집 완료(25,210행): 코스피200/미니/코스닥150 차근월 2022~ 연속·정확. 섹터선물 OHL0은 실제 저유동(정상).
  - [~] **주식선물(L, 275종목) NEXT 재수집** — 6/26 1차 시도는 한도 조기 도달로 부분완료. 6/30 **안전윈도우 재개 러너 가동**(`scripts/backfill_futures_L_safewindow.py`, tmux `futures_backfill`, 상태파일 `cache/futures_L_backfill_done.txt`). 한도 시 다음날 10시까지 자고 이어받기 → 며칠 내 자동 완료 예정. (daily 수집은 이미 수정됨)
- [ ] **`futures_daily_with_class` view 후속** — 현재 NEXT를 분봉 집계로 유도(2026+만, 일봉 NEXT 버그 우회 흔적). 일봉 NEXT 수정·**L 백필 완료 후** 이 view의 NEXT를 일봉 테이블 기반으로 바꾸면 2022+ 전체이력 + NEAR/NEXT 소스 일관. (분봉 view futures_intraday_near/next/with_class는 점검 결과 정상 — rank 기반이라 버그 무관, 일봉과 교차일치 OK)
- [x] **ETF portfolio 5일 FIFO** — `etf_snapshot.prune_portfolio_retention()` 추가(2-pass 후 최근 5영업일만 유지). 백업(`backups/etf_portfolio_daily_pre_fifo_20260626.dump` 46MB) 후 일회성 4.17M행 삭제 → 178,620행, VACUUM. master는 전기간 보존.

### 운영 교훈
- [x] **pgrep 가드 허점** — in-process job(08:30 종합보충 등)은 별도 프로세스가 아니라 pgrep에 안 잡힘. 재시작 전 **스케줄러 로그로 진행 중 job 확인** 병행 필요(CLAUDE.md/스케줄러_운영.md 반영). graceful shutdown은 실제 작동 확인(C-c 시 job 완료 대기).

### 검증 총평 (에이전트 7개, read-only)
- 핵심 raw 시계열(일봉/수급/외인/시총/지수/분봉/배당) 전반 건전. 위 외 잔여는 경미·설명가능:
  - adj 2022~2023 미백필 + 2024-04말 ~1,205행 아티팩트(raw는 정상)
  - market_cap_daily.shares_outstanding 100% NULL(값 자체는 정상), floating_shares 2~5월 공백(과거 재계산시만 영향)
  - corporate_actions event_type 전량 UNKNOWN_FROM_FACTOR(미분류), 분봉 NXT(exchange=N) 미수집, sectors 레거시 死 스키마

---

## 🆕 2026-06-17 — 배당 정정 공시 반영 점검 + 가짜 중간버전 196개 정규화

### 점검 결과 (정정 메커니즘 정상)
- 정정 공시 처리: 새 version 행 추가 + 이전 version `is_latest=false` 전환 → 정상 작동
- `is_latest` 무결성: 6,601 그룹 전부 is_latest=true 정확히 1개
- LENS export `revisions` 배열에 전체 정정 이력 임베드 (773 레코드)
- 실사례 검증: 이노테나(333050) 2026 Q2 — v1(공시 5/14) → v2(정정 5/18) 둘 다 보존
- 매일 02:00 daily_update 안 `[배당-1/2/3]` 스텝으로 DART 신규/정정 수집 + export, 6월도 정상

### 완료 — 196개 정규화
- [x] **현상**: 다중버전 그룹 969개 중 773개만 이력 보존, **196개는 version≥2인데 행 1개만 존재**
- [x] **원인 규명**: 196개 전부 `created_at=2026-04-29`(초기 백필 1회 배치) + 과거연도(2021~25)
  + 본문 "정정" 없음 + 대부분 지주·홀딩스 → 실제 정정 아님. 백필 day-chunk UPSERT 충돌로
  version만 in-place 증가한 잔재. **4/29 이후 이런 케이스 0건**(daily 파이프라인은 항상 이력 보존)
- [x] **충돌 점검**: uq `(code,fiscal_year,period,version)`은 dividend_type 미포함 →
  version=1 변경 시 충돌 가능성 사전 점검 = **0건** 확인
- [x] **정규화**: 트랜잭션 UPDATE 196 → version=1. 검증 3종 통과(잔여 단일행-다중버전 0 /
  is_latest 무결성 유지 / uq 중복 0). 백업 `/tmp/div_normalize_backup.csv`(재부팅 시 소멸, 이미 적용·검증 완료라 불요)
- [x] **결과**: v1 5,632→5,828(+196), 다중버전 그룹 969→773(이력보존 그룹과 일치).
  실데이터 값(latest amount 등) 무변경, 제거된 건 과거연도 가짜 중간버전 이력뿐
- [x] **LENS export 재갱신**: 6,601건, revisions 773 유지

---

## 🆕 2026-06-10 — 191개 ETF 오비활성 발견 + 재활성/백필 + ghost_delist 가드

### 발견 (운영 점검 중)
- 191개 ETF(국내 118 + 해외 73)가 6/2경 `is_active=FALSE`로 잘못 비활성 → OHLCV 6/2~6/9 누락
- 종목명 정상(TIGER 코스피고배당/차이나CSI300/KODEX 미국S&P500 등), LS로 보면 거래 중
- 원인: `sync_stock_master` ghost_delisted 로직 — 인포맥스 `get_stock_codes`가 이 191개를
  부분 누락 반환. 기존 가드(coverage≥90%)는 통과(191개는 ~5%)해서 191개 오비활성.
  (6/2 supplement 테스트로 run_update 반복 실행되며 sync_master가 여러 번 돈 시점과 겹침)

### 완료
- [x] **OHLCV 정책 확인** — `get_stocks(include_etf=True)`는 유형 구분 없이 활성 ETF 전부 수집
  (국내/해외 무관). etf_snapshot(PDF)만 국내형 필터. 두 정책 별개.
- [x] **191개 전수 LS 확인** → 전부 거래 중 (진짜 상폐 0)
- [x] **191개 재활성** (is_active=TRUE, ETF 946→1137)
- [x] **6/2~6/9 OHLCV 백필** — run_update(missing_only, collect_foreign=False, sync_master=False)
  각 영업일. LS 호출초과(IGW00201) 떴으나 재시도로 191개 전부 복구. ETF 945→1136~1137.
- [x] **ghost_delist 가드 추가** (`sync_stock_master`) — 후보가 GHOST_DELIST_MAX(20) 초과면
  인포맥스 부분 누락 의심 → 비활성 SKIP + errors 기록(알림). 대량 오비활성 재발 방지.
- [x] **run_update에 sync_master 파라미터** — 보충/백필 루프에서 마스터 갱신(~55초) 중복 회피

### 후속
- [ ] **6/11~ daily_update에서 가드 작동 확인** — 인포맥스가 또 191개 누락 시 SKIP + 경고 알림 오는지

---

## 🆕 2026-06-02 — 외인 지분율 10일 누락 발견 + 08:30 종합 보충 구조 전환

### 발견
- 외국인 지분율이 5/22 이후 10일째 적재 0건 (영업일 5일 누락)
- 원인: 5/21 daily_update를 05:30 → 02:00으로 앞당긴 뒤, 외인은 익일 05:30 이후라야
  인포맥스에 등록되는데 02:00엔 빈 응답 → STEP3 전량 실패. `get_update_range`가
  ohlcv 기준으로 날짜 전진시켜 못 받은 외인은 영구 누락.

### 완료
- [x] **누락 5일치 수동 백필** — `collect_foreign_ownership.py --start 20260526 --end 20260601` (13,240건, 실패 0)
- [x] **08:30 종합 보충 구조 전환** (잡 개수 3개 유지, etf_snapshot이 종합 보충으로 확장)
  - `run_update`에 `collect_foreign`/`sync_master` 파라미터 추가
  - 02:00 본체: `collect_foreign=False` (외인 skip, 어차피 빈 응답)
  - 08:30 `job_etf_snapshot` = ETF PDF/마스터 + `run_supplement_pipeline()`
  - `run_supplement_pipeline`: 최근 3영업일 무조건 + 가장 뒤처진 테이블 last+1 검토,
    각 날짜 `run_update(missing_only=True, sync_master=False)`로 누락 보충 (cap 10일)
  - `sync_master=False`로 마스터 갱신(~55초) 보충 루프 중복 제거
- [x] **E2E 검증** — 6/1 외인 10종목 삭제 → supplement가 10건 정확 보충 → 2,648 복구

### 설계 근거
- 외인·ETF PDF는 둘 다 "당일 새벽 인포맥스에 없고 늦게 등록" → 같은 08:30 잡에 통합
- daily_update에 합치면 02:00 프로세스가 08:30까지 idle 점유 → 분리 유지
- catchup = 외인만이 아니라 OHLCV/수급 포함 "전체 누락 종합 검토" 역할

### 후속
- [ ] **6/3(수) 08:30 첫 실운영 검증** — 6/2 외인이 종합 보충으로 들어오는지 + 알림 확인

---

## 🆕 2026-05-30 — LS t8451 페이징 버그 (보고서 111MB 폭증) 수정

### 발견
- daily_update 보고서가 5/27분부터 111~112MB로 폭증 (평소 60KB, 1,800배)
- 원인: `collectors/ls_api.py:get_daily_bars`가 LS t8451 1일 query에 ~500 bar(2년치) 반환
  - LS t8451이 `sdate`를 자주 무시하고 `edate` 기준 과거로 ~500 bar 반환
  - 클라이언트가 `dedup`만 하고 sdate~edate 범위 filter 안 함
  - → `_fetch_ls_ohlcv`가 종목당 ~500 row 적재 → 3,852종목 × ~460 = 1,775,955 row
  - → `analyze_anomalies`가 1.7M row에서 ±30% 변동 779,897건 감지 → 보고서 폭증
- 부작용: daily_update 소요 4h21m (정상 1.5~2h), scheduler.log 321MB 누적

### 완료
- [x] **`get_daily_bars` sdate~edate 범위 filter 추가** — 1일 query에 500 bar 적재 방지
  - 검증: 005930 1일 query → 1 bar (이전 500), 5/23~5/26 → 1 bar (5/26만 영업일)
- [x] **거대 보고서 3개 삭제** (5/27~5/29, 각 ~107MB / DB가 SSoT라 보고서는 파생물)
- [x] **scheduler.log truncate** (321MB → 0, 최근 2000줄 `scheduler_archive_20260530.log`로 백업)
- [x] **scheduler 재시작** (22:09:50 KST, fix 반영)

### 영향
- 데이터 무결성 OK — UPSERT라 옛 raw 데이터 덮어써도 손상 없음 (DB row는 정상)
- fix 적용 첫 정식 수집: **6/2(화) 02:00** (5/31 일·6/1 월은 no-op)

### 후속 검증
- [ ] **6/2 화 02:00 첫 정식 daily_update** — 보고서 60KB대 복귀 + 소요 1.5~2h 복귀 확인

---

## 🆕 2026-05-26 — stockfut 휴일 fallback 발견 + ETF PDF 백필 완료

### 발견
- **LS t8406이 휴장일에 직전 영업일 데이터를 반환** — 2026-05-25(부처님오신날 대체공휴일) 23:30 stockfut가 5/22 데이터를 5/25 timestamp로 적재 (448,266 row)
- 기존 `_verify_stockfut_loaded`가 row count만 봐서 ok=True 잘못 판정 → "성공" 알림 도착했지만 실제론 가짜 데이터

### 완료
- [x] **stockfut cron 휴일 체크 추가** — `is_market_closed(conn, today)` true면 즉시 skip + noop 알림
- [x] **`_verify_stockfut_loaded` 강화** — 직전 영업일 데이터와 close/volume 100% 동일하면 fail (LS 휴장일 fallback 감지)
- [x] **5/25 corrupt row 삭제** — 448,266 row DELETE (futures_ohlcv_intraday)
- [x] **ETF PDF 백필 완료** — 22,794/22,794 (100%) / 1,360,878 PDF row / 22,794 master / 에러 0 / 소요 3,430분 (~57h)

### 검증 결과
- 1~5월 다른 휴장일들(1/1, 2/16-18, 3/2, 5/1, 5/5)은 stockfut cron 미가동 시기라 데이터 없음 (영향 없음)
- 5/25만 손상 → 정리 완료
- 첫 정상 영업일 stockfut 실행은 **오늘(5/26) 23:30** 예정

---

## 🆕 2026-05-24 — 운영 / 문서 정비 + Telegram 알림 + 백필 전략 정비

### 완료 (오전)
- [x] **scheduler tmux 세션 재가동** — 기존 nohup 단독 가동 발견 → `kdata_scheduler` tmux 세션으로 재가동 (`logs/scheduler.log` tee)
- [x] **`daily_scheduler.py` 배너 display 버그 fix** — 옛 `trigger_daily` 05:30 / 배너 문구가 dead code로 남아 있어 실제 잡 02:00과 불일치. 둘 다 02:00으로 동기화.
- [x] **`etf_snapshot.py` retry 로직 추가** — `wait_for_daily_update`에 단계별 대기:
  - 1단계: 60s polling × 최대 4h
  - 2단계: 2h 간격 deep retry × 최대 3회
  - 합계 10h 후에도 daily_update 진행 중이면 RuntimeError abort (다음날 yesterday 2-pass로 부분 회수)
- [x] **CLAUDE.md 슬림화** (189줄 → ~70줄), 운영 상세는 `docs/스케줄러_운영.md` 신설로 이관

### 완료 (새벽 — 백필/알림/회복성)
- [x] **ETF PDF 백필 전략 전면 재정비** (`scripts/backfill_etf_pdf.py`)
  - `fetch_existing_pairs()` 신설 — `etf_portfolio_daily` 에 이미 적재된 `(etf_code, snapshot_date)` 쌍 skip
  - daily etf_snapshot이 매일 적재한 데이터 중복 호출 회피 → 53,770 → 22,794 콜 (57.6% 감소)
  - `--desc` 모드로 가동 (최신→옛, 5/20 268개 누락분 우선 회수)
  - **안전 윈도우 가드** (`wait_for_safe_window`) — 백필은 10:00~24:00만 가동 (00:00~10:00은 daily_update/etf_snapshot에 인포맥스 한도 양보)
  - 옛 self-limit (`--max-calls 2000`) default 제거 → 인포맥스 한도 풀로 활용
  - `wait_until_midnight` 의미 정정: 09:30 → 다음 10:00 (한도 리셋 + 반복 잡 우선권)
  - tmux 세션: `etf_backfill` 별도 가동
- [x] **Telegram 알림 시스템 구축**
  - `schedulers/notifier.py` 신설 — `notify_job()` 헬퍼, .env에서 토큰/chat_id 로드, 실패 시 silent skip
  - 6개 잡에 알림 통합: daily_update(no-op 포함) / etf_snapshot / stockfut_today / update_listed_shares / weekly_backup(실패만) / quarterly_*
  - daily_update 알림은 보고서 tail 첨부, etf_snapshot은 DB row 수 조회, stockfut는 검증 결과 포함
  - `config/settings.py`에 `extra = "ignore"` 추가 — pydantic-settings가 TELEGRAM_* 등 unknown 키 거부하던 부작용 fix
- [x] **stockfut_today 재시도 로직** — historical 불가 → 영구 손실 방지
  - `_verify_stockfut_loaded()` 신설 — DB의 distinct futures_code 수가 actives의 95% 이상이면 성공
  - 검증 실패 시 5분 간격 최대 3회 재시도 (UPSERT라 안전)
  - 마지막 시도까지 실패하면 fail 알림 발송
- [x] **scheduler 시작 시 catch-up 로직** (`startup_catchup`)
  - APScheduler misfire_grace_time이 시작 시점 단일 fire를 항상 잡지 않는 한계 보완
  - weekly_backup: 최신 백업 mtime > 8일 → 즉시 보충 (background thread)
  - update_listed_shares: floating_shares.base_date > 8일 → 즉시 보충
  - daily_update / etf_snapshot은 자체 회복 로직 보유로 catch-up 제외
- [x] **pgrep 가드 확장 (CLAUDE.md)** — backup_db / etf_snapshot / update_listed_shares / crawl_sector / collect_financials / stockfut 포함
  - 5/24 03:00 weekly_backup partial dump 사례 (scheduler 재시작이 백업 진행 중을 잡지 못해 SIGTERM으로 절단) 재발 방지
- [x] **5/24 03:00 백업 partial 정리** — 101MB partial dump 삭제 후 수동 백업 재실행 (1008MB 정상 완성)

### 미완 / 후속 — 일단 운영해보고 7일 후(2026-05-31) 재평가
- [ ] **systemd 서비스 등록** — scheduler crash 자동 재시작 + Restart=always
- [ ] **graceful shutdown 실작동 fix** — BlockingScheduler가 SIGTERM handler 가림 (BackgroundScheduler 전환 또는 wrapper)
- [ ] **`scripts/scheduler_restart.sh` wrapper** — pgrep 자동 차단 + fire 시각 회피 자동 검사
- [ ] **외인지분율 STEP 분리 + daily_update 끝쪽으로 이동** — 5/15~16 사고 대응 잔여
- [ ] **분봉 일배치 + quality_checks 결과를 daily_update 알림 detail에 포함** — 현재 silent
- [ ] **DB 연결 retry — daily_update 본체** — 백필은 retry 있는데 본체는 raise
- [ ] **heartbeat 알림** — N시간마다 "alive" 메시지 (scheduler crash 감지)

---

---

## 🆕 2026-05-16 ~ 17 — 수정주가(Adjusted Price) 시스템 구축

### 완료 (Phase 1~5)
- [x] **Phase 1 schema** (`ohlcv_adjusted_migration.sql`) — ohlcv_daily/intraday adj 컬럼 + corporate_actions 테이블
- [x] **LS API `get_daily_bars(sujung='Y', exchgubun='K')`** (`collectors/ls_api.py`) — t8451 cts_date 페이징
- [x] **Phase 2 일봉 4년치 backfill** (`scripts/backfill_adjusted_daily.py`) — 3,828 종목 / 1.77M row / 256.9분 / 0 에러
- [x] **Phase 3 corporate_actions 자동 추출** (`scripts/extract_corporate_actions.py`) — 7,895건 / 1,259 종목
- [x] **Phase 4 분봉 adj_factor UPDATE** — 11.8M row / 399 영향 종목 / 4.5분
- [x] **Phase 5 daily_update 자동 통합** (`run_adjusted_price_pipeline`) — gap > 15% 의심 종목만 LS 호출
- [x] **`ohlcv_intraday_adjusted` view** — LENS가 테이블명만 변경하면 자동 수정주가

### 결정 사항
- Source = LS sujung=Y (인포맥스/DART는 raw만)
- 일봉: raw + adj_* 컬럼 (4개) + adj_factor
- 분봉: raw + adj_factor 컬럼만 (75M row 디스크 절약, query 시 곱셈)
- 분봉 backfill = LS spec에 분봉 sujung 없음 → 일봉 factor를 분봉 raw에 곱셈 (옵션 D)
- vendor 일관성: exchgubun='K' (KOSPI 정규시장) — 'N'(NXT)은 인포맥스와 차이

### 후속 검증
- [ ] **5/19 (화) 04:30 첫 자동 Phase 5 실행 검증** — 모니터 `bohtmr5mm` watch 중
- [ ] **LENS에 view 사용법 안내 + 마이그**: bars.rs SELECT → `ohlcv_intraday_adjusted`
- [ ] **DART 공시 매칭으로 event_type 정확 분류** (현재 'UNKNOWN_FROM_FACTOR' 일괄) — 향후

---

## 🆕 2026-05-15 ~ 16 — 운영 사고 + LENS LS 계정 token contention 대응

### 완료
- [x] **scheduler SIGTERM/SIGINT graceful handler 추가** (`5368534`) ⚠️ BlockingScheduler에 가려 실제 작동 안 함, fix 필요 (아래 미완 참고)
- [x] **ETF PDF 2-pass (today + yesterday)** (`d330d8c`) — LENS 당일 PDF 즉시 사용
- [x] **분봉 일배치 cron 통합** (`2ceeb19`) — 23:00 별도 cron → 04:30 daily_update 끝에 직렬 통합
- [x] **stockfut cron 22:30 → 23:30** (`91dadc1`) — 사용자 야간 LENS 자유 시간 확보
- [x] **LS API 5xx 본문 debug 로깅** (`e436db6`) — 사고 본문 캡처에 결정적 역할
- [x] **LS API IGW00121 자동 처리** (`ed51570`) — 4 호출 사이트, 401과 같은 패턴
- [x] **5/14 daily_update 후속 단계 수동 복구** — PID 2672623 (지수/선물 일봉 + ETF + 배당)
- [x] **futures_master.json 5/14→5/15 수동 export** — LENS 5월물 만기 → 6월물 롤오버
- [x] **5/15 stockfut 5/15 누락 ON CONFLICT 자동 보충**
- [x] **CLAUDE.md 신규** — 시간/날짜 KST 확인 규칙 + 운영 cron 표 + scheduler 재시작 절대 체크
- [x] **memory feedback_time_check.md** — 메모리 시스템에도 영구 기록

### 진행 중 (5/16 ~12:50 KST)
- [-] **5/14, 5/15 분봉 일배치 수동 복구** (PID 2720521) — 종목별 갭 fill로 양일 자동 sweep
- [-] **5/15 외인지분율 `--missing-only` 보충** (PID 2720561)

### 미완 / 후속 작업
- [ ] **외인지분율 STEP 분리 + daily_update 끝에서 두 번째로 이동** — 04:30이 외인 안전선 5:30보다 일러서 부족 (1,577/2,646). `run_update` 내부 STEP 3 추출 + 별도 함수 + `main()` 순서 변경 필요
- [ ] **SIGTERM handler 실제 작동 fix** — BlockingScheduler 내부 SIGINT handler가 우리 거 가림. 다른 패턴 필요 (BackgroundScheduler 전환 또는 wrapper script)
- [ ] **scheduler 재시작 pre-flight wrapper script** (`scripts/scheduler_restart.sh`) — `pgrep -f daily_update` 자동 체크 + 진행 중 차단

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

- [ ] **5/14 22:30 stockfut + 23:00 분봉 일배치 첫 새 cron 실행 검증** — 모니터 `b44if6de0` watch 중
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
  - `job_stockfut_today` (**22:30 KST 평일**) 신규 — 주식선물 당일 적재
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
22:30 KST  stockfut_today     주식선물 t8406 당일 (historical 불가 — 매일 받기 필수)
일03:00    weekly_backup      DB 백업
```

### 알려진 한계
- 주식선물 historical 불가 → 22:30 cron 미실행 시 그날 영구 손실
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
