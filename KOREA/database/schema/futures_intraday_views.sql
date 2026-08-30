-- Phase 7 분봉 NEAR/NEXT 매핑 view (v3 — 인포맥스 일봉 contract_code 조인)
--
-- ── v2(dense_rank)를 버린 이유 ──────────────────────────────────────────
-- v2는 "분봉 테이블에 있는 contract들을 만기순 정렬해 1=NEAR, 2=NEXT"였다.
-- 전제: 분봉에 적재된 게 곧 LS의 진짜 NEAR+NEXT.
-- 실제로는 **진짜 근월물이 분봉에 없으면 차근월을 NEAR로 잘못 찍는다**.
--   2026-01-02~03-12: 진짜 NEAR = 3월물(A0163000)인데 만기(3/12) 뒤에 백필이
--   돌아 아예 미수집 → 뷰가 6월물(당시 NEXT)을 NEAR로 라벨링.
--   2026-06-11: 진짜 NEAR = 6월물인데 그날 분봉 미수집 → 9월물이 NEAR로.
-- 검증 결과 KOSPI200 161 영업일 중 **47일(29%)이 오태깅**이었다.
-- 거래량 400k짜리 근월물 자리에 거래량 300짜리 원월물이 앉는 형태라
-- 그대로 쓰면 시세가 아니라 잡음을 분석하게 된다.
--
-- ── v3 방식 ────────────────────────────────────────────────────────────
-- 날짜별 NEAR/NEXT를 추측하지 않고 **인포맥스가 확정한 값**을 그대로 쓴다.
-- futures_ohlcv_daily(underlying_code, contract_class, time, contract_code)가
-- 벤더 기준 날짜별 근월/차근월 매핑이고, contract_code 형식이 분봉
-- futures_ohlcv_intraday.futures_code와 동일하다(2026-08-28 552/552 매칭 확인).
--
-- v2 헤더에 적혀 있던 "인포맥스 NEXT는 long-dated annual이라 조인하면 NEXT가
-- 빈다"는 제약은 collectors/infomax.pick_nearest_deferred() 도입으로 해소됐다
-- (날짜별 만기 최소 1건만 남김). 지금은 일봉 NEXT도 진짜 차근월이다.
--
-- 부수 효과(의도된 것): 진짜 근월물의 분봉이 없는 날은 NEAR 행이 **비어 있다**.
-- 잘못된 계약을 NEAR로 채우는 것보다 없는 게 낫다 — 소비 측에서 갭이 보인다.

DROP VIEW IF EXISTS futures_daily_with_class;
DROP VIEW IF EXISTS futures_intraday_near;
DROP VIEW IF EXISTS futures_intraday_next;
DROP VIEW IF EXISTS futures_intraday_with_class;

CREATE VIEW futures_intraday_with_class AS
SELECT
    i.futures_code,
    i.time,
    i.interval_seconds,
    d.underlying_code::text AS underlying_code,
    i.open, i.high, i.low, i.close,
    i.volume, i.trading_value, i.open_interest,
    d.contract_class::text AS contract_class
FROM futures_ohlcv_intraday i
JOIN futures_ohlcv_daily d
  ON d.contract_code = i.futures_code
 AND d.time = (i.time AT TIME ZONE 'Asia/Seoul')::date;

CREATE VIEW futures_intraday_near AS
SELECT futures_code, time, interval_seconds, underlying_code,
       open, high, low, close, volume, trading_value, open_interest
FROM futures_intraday_with_class WHERE contract_class = 'NEAR';

CREATE VIEW futures_intraday_next AS
SELECT futures_code, time, interval_seconds, underlying_code,
       open, high, low, close, volume, trading_value, open_interest
FROM futures_intraday_with_class WHERE contract_class = 'NEXT';

-- 일별 연결 선물 — NEAR/NEXT 둘 다 인포맥스 일봉 테이블 하나에서 (TODO 완료분).
-- 이전엔 NEXT를 30초봉 집계로 유도했는데, 그 30초봉의 NEAR/NEXT 태깅이 위
-- v2 버그를 타고 있었고 2026+ 구간만 존재했다. 일봉 기반으로 바꾸면
-- 2022+ 전체이력 + NEAR/NEXT 소스 일관 + settle/이론가/베이시스까지 채워진다.
CREATE VIEW futures_daily_with_class AS
SELECT underlying_code, time, contract_class, contract_code,
       open, high, low, close, settle_price,
       volume, trading_value, open_interest,
       theoretical_price, underlying_basis, theoretical_basis,
       'infomax'::text AS source
FROM futures_ohlcv_daily;

COMMENT ON VIEW futures_intraday_with_class IS
  'Phase 7 v3: 인포맥스 일봉 contract_code 조인으로 날짜별 NEAR/NEXT 확정 (추측 없음)';
COMMENT ON VIEW futures_intraday_near IS
  'Phase 7: 각 (date, underlying)의 NEAR contract 30초봉. 근월물 분봉이 없는 날은 빈다';
COMMENT ON VIEW futures_intraday_next IS
  'Phase 7: 각 (date, underlying)의 NEXT contract 30초봉';
COMMENT ON VIEW futures_daily_with_class IS
  '일별 연결 선물 (NEAR/NEXT). 전부 인포맥스 일봉 기반 — 2022+ 전체이력';
