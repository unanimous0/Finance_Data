-- Phase 7 분봉 NEAR/NEXT 매핑 view
--
-- 정책: 분봉(per-contract)을 일별 (futures_ohlcv_daily)의 NEAR/NEXT 매핑으로 join,
--       각 날짜의 NEAR/NEXT 30초봉을 자동 식별. 일별이 SSoT.
--
-- 사용:
--   SELECT * FROM futures_intraday_near WHERE underlying_code='01' AND time::date='2026-05-13';
--   -- spread = NEAR.close - NEXT.close (만기 근처 활용)
--   SELECT n.time, n.close - x.close AS spread
--   FROM futures_intraday_near n
--   JOIN futures_intraday_next x
--     ON x.underlying_code = n.underlying_code AND x.time = n.time
--    AND x.interval_seconds = n.interval_seconds
--   WHERE n.underlying_code='01' AND n.time::date='2026-05-13';
--
-- 주의: 일별의 contract_class 매핑 신뢰. 분봉에 해당 contract 없으면 view에서 row 0
--      (예: 1/2~3/12 KP200 NEAR contract = A0163000은 LS historical 미제공 → 그 구간 NEAR view 비어있음).

CREATE OR REPLACE VIEW futures_intraday_near AS
SELECT
    i.futures_code,
    i.time,
    i.interval_seconds,
    d.underlying_code,
    i.open, i.high, i.low, i.close,
    i.volume, i.trading_value, i.open_interest
FROM futures_ohlcv_intraday i
JOIN futures_ohlcv_daily d
  ON d.contract_code = i.futures_code
 AND d.time = (i.time AT TIME ZONE 'Asia/Seoul')::date
 AND d.contract_class = 'NEAR';

CREATE OR REPLACE VIEW futures_intraday_next AS
SELECT
    i.futures_code,
    i.time,
    i.interval_seconds,
    d.underlying_code,
    i.open, i.high, i.low, i.close,
    i.volume, i.trading_value, i.open_interest
FROM futures_ohlcv_intraday i
JOIN futures_ohlcv_daily d
  ON d.contract_code = i.futures_code
 AND d.time = (i.time AT TIME ZONE 'Asia/Seoul')::date
 AND d.contract_class = 'NEXT';

COMMENT ON VIEW futures_intraday_near IS
  'Phase 7: 각 (date, underlying)의 NEAR contract 30초봉. 일별 (futures_ohlcv_daily) 매핑 join.';
COMMENT ON VIEW futures_intraday_next IS
  'Phase 7: 각 (date, underlying)의 NEXT contract 30초봉. 일별 (futures_ohlcv_daily) 매핑 join.';
