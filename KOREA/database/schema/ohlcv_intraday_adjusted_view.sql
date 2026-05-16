-- 분봉 수정주가 자동 적용 view — LENS 등 분석 사용처용
-- 사용: SELECT * FROM ohlcv_intraday_adjusted WHERE stock_code='010120';
--      모든 가격 컬럼이 raw × adj_factor 자동 계산 (NULL=1).
--      volume은 raw 유지 (절대값 분석 영향 회피).

CREATE OR REPLACE VIEW ohlcv_intraday_adjusted AS
SELECT
    stock_code,
    time,
    exchange,
    interval_seconds,
    (open  * COALESCE(adj_factor, 1.0))::numeric(12,2) AS open,
    (high  * COALESCE(adj_factor, 1.0))::numeric(12,2) AS high,
    (low   * COALESCE(adj_factor, 1.0))::numeric(12,2) AS low,
    (close * COALESCE(adj_factor, 1.0))::numeric(12,2) AS close,
    volume,              -- raw 그대로 (절대값 거래량 분석용)
    trading_value,       -- raw 그대로 (대금)
    adj_factor,          -- 사용자 인지 가능하게 노출
    open  AS raw_open,   -- 원본 가격 비교용
    high  AS raw_high,
    low   AS raw_low,
    close AS raw_close
FROM ohlcv_intraday;

COMMENT ON VIEW ohlcv_intraday_adjusted IS
  '분봉 수정주가 view — open/high/low/close 컬럼이 raw × adj_factor. volume은 raw. raw_* 컬럼 별도 노출.';
