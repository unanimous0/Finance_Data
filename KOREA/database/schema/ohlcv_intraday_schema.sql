-- ==========================================
-- 분봉 OHLCV (ohlcv_intraday) — Phase 6
-- ==========================================
-- 데이터 소스: LS증권 OpenAPI t8452 (통합 주식차트 N분)
-- 봉 단위 분기:
--   - 백필 (2026-01-02 ~ 2026-04-26): 1분봉 (ncnt=1, interval_seconds=60)
--   - 백필 + 일배치 (2026-04-27~):    30초봉 (ncnt=0, interval_seconds=30)
--   - 30초봉 가용 시작점 = 2026-04-27 (실측 확정)
-- 종목 스코프 (Phase 6): KOSPI200 + KOSDAQ150 + 한국 ETF + ETF PDF union ≈ 2,000종목
--
-- 컬럼 결정:
--   - exchange: 향후 NXT 확장 대비. 현재 'K' (KRX)만
--   - interval_seconds: 30 (ncnt=0) | 60 (ncnt=1). 백테스트 시 WHERE 절로 분리 가능
--   - trading_value: t8452 응답의 'value' (백만원 단위) × 1,000,000 (원 단위 변환)
--   - mdvolume/msvolume 없음: t8452 OutBlock1에 매도/매수 분리 거래량 미제공 (t1302와 다름)
-- ==========================================

CREATE TABLE IF NOT EXISTS ohlcv_intraday (
    stock_code        VARCHAR(10)    NOT NULL,
    time              TIMESTAMPTZ    NOT NULL,
    exchange          CHAR(1)        NOT NULL DEFAULT 'K',  -- K=KRX / N=NXT
    interval_seconds  SMALLINT       NOT NULL,              -- 30 or 60

    open              NUMERIC(12, 2) NOT NULL,
    high              NUMERIC(12, 2) NOT NULL,
    low               NUMERIC(12, 2) NOT NULL,
    close             NUMERIC(12, 2) NOT NULL,
    volume            BIGINT         NOT NULL,              -- 봉 단위 거래량 (jdiff_vol)
    trading_value     BIGINT,                               -- 거래대금 (value × 백만원 → 원)

    PRIMARY KEY (stock_code, time, exchange, interval_seconds)
);

-- TimescaleDB Hypertable
SELECT create_hypertable(
    'ohlcv_intraday', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_intraday_stock
    ON ohlcv_intraday (stock_code, time DESC);

COMMENT ON TABLE  ohlcv_intraday IS 'LS t8452 분봉 OHLCV (백필=1분봉, 일배치=30초봉). 30초 가용시작=2026-04-27';
COMMENT ON COLUMN ohlcv_intraday.interval_seconds IS '30 (ncnt=0 t8452 30초봉) | 60 (ncnt=1 1분봉)';
COMMENT ON COLUMN ohlcv_intraday.exchange         IS 'K=KRX (현재만 사용) | N=NXT (향후 확장)';
COMMENT ON COLUMN ohlcv_intraday.volume           IS '봉 단위 거래량 (t8452 jdiff_vol)';
COMMENT ON COLUMN ohlcv_intraday.trading_value    IS '거래대금 원 단위 (t8452 value 백만원 × 1,000,000)';
