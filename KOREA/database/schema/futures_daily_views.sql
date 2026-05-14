-- Phase 7 일별 NEAR/NEXT 통합 view (절충안)
--
-- 문제: 인포맥스 일별 NEXT 매핑이 우리 정책(차월물)과 다름 (인포맥스는 long-dated annual).
--       그래서 일별 NEXT 데이터 거의 비어있음 (5/6~5/13 6일만, 그것도 28년 12월물로 매핑).
--
-- 해결: 일별 NEAR는 인포맥스 그대로 (롤오버 매핑 정확).
--       일별 NEXT는 **분봉 NEXT view에서 일별 OHLCV로 집계** — LS의 진짜 차월물 기준.
--
-- 한계: 분봉 NEXT 데이터 있는 기간만 NEXT 일봉 가능 (지수선물 = 1/2 ~ 5/13 KP/KQ 9월물,
--       단 9월물 useful 시작이 3/13이라 실질 3/13~).
--       분봉 NEAR도 비어있는 기간(예: 1/2~3/12 KP200 3월물 = LS historical 미제공)은
--       일별 NEAR가 인포맥스로부터 정상 적재돼있어 view 결과 정확.

DROP VIEW IF EXISTS futures_daily_with_class;

CREATE VIEW futures_daily_with_class AS
-- NEAR: 인포맥스 일별 그대로 (롤오버 매핑 정확)
SELECT
    underlying_code, time, contract_class, contract_code,
    open, high, low, close, settle_price,
    volume, trading_value, open_interest,
    theoretical_price, underlying_basis, theoretical_basis,
    'infomax'::text AS source
FROM futures_ohlcv_daily
WHERE contract_class = 'NEAR'

UNION ALL

-- NEXT: 분봉 NEXT view에서 일별 OHLCV 집계
SELECT
    underlying_code,
    (time AT TIME ZONE 'Asia/Seoul')::date AS time,
    'NEXT'::varchar AS contract_class,
    futures_code AS contract_code,
    (array_agg(open ORDER BY time ASC))[1] AS open,
    MAX(high) AS high,
    MIN(low) AS low,
    (array_agg(close ORDER BY time DESC))[1] AS close,
    NULL::numeric AS settle_price,
    SUM(volume)::bigint AS volume,
    SUM(trading_value)::bigint AS trading_value,
    (array_agg(open_interest ORDER BY time DESC))[1] AS open_interest,
    NULL::numeric AS theoretical_price,
    NULL::numeric AS underlying_basis,
    NULL::numeric AS theoretical_basis,
    'derived_from_intraday'::text AS source
FROM futures_intraday_next
GROUP BY underlying_code, (time AT TIME ZONE 'Asia/Seoul')::date, futures_code;

COMMENT ON VIEW futures_daily_with_class IS
  'Phase 7: 일별 NEAR/NEXT 통합 view. NEAR=인포맥스 raw, NEXT=분봉 NEXT view 일별 집계 (LS 차월 기준).';
