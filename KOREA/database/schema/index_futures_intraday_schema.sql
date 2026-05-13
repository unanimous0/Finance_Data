-- ── 지수/선물 분봉 (30초/1분) Hypertable ─────────────────────────────
-- ohlcv_intraday(주식)와 동일 패턴, 다만 지수/선물은 별도 테이블로 분리
--   - 지수: index_ohlcv_intraday (t8418, /indtp/chart)
--   - 선물(지수+주식): futures_ohlcv_intraday (t8465 / t8406)

-- 1) 지수 분봉
CREATE TABLE IF NOT EXISTS index_ohlcv_intraday (
    index_code        VARCHAR(10)               NOT NULL,
    time              TIMESTAMP WITH TIME ZONE  NOT NULL,
    interval_seconds  SMALLINT                  NOT NULL,
    open              NUMERIC(14,4),
    high              NUMERIC(14,4),
    low               NUMERIC(14,4),
    close             NUMERIC(14,4)             NOT NULL,
    volume            BIGINT                    NOT NULL,
    trading_value     BIGINT,
    PRIMARY KEY (index_code, time, interval_seconds)
);

-- TimescaleDB hypertable
SELECT create_hypertable('index_ohlcv_intraday', 'time',
    chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_index_intraday_code_time
    ON index_ohlcv_intraday (index_code, time DESC);


-- 2) 선물 분봉 (지수선물 + 주식선물 통합)
CREATE TABLE IF NOT EXISTS futures_ohlcv_intraday (
    futures_code      VARCHAR(10)               NOT NULL,    -- LS shcode (8자, A로 시작)
    time              TIMESTAMP WITH TIME ZONE  NOT NULL,
    interval_seconds  SMALLINT                  NOT NULL,
    open              NUMERIC(14,4),
    high              NUMERIC(14,4),
    low               NUMERIC(14,4),
    close             NUMERIC(14,4)             NOT NULL,
    volume            BIGINT                    NOT NULL,
    trading_value     BIGINT,                                -- t8406은 NULL 가능 (누적 응답이라 봉 단위 변환 안 됨)
    open_interest     BIGINT,                                -- 미결제약정 (openyak)
    PRIMARY KEY (futures_code, time, interval_seconds)
);

SELECT create_hypertable('futures_ohlcv_intraday', 'time',
    chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_futures_intraday_code_time
    ON futures_ohlcv_intraday (futures_code, time DESC);


-- 검증 쿼리
COMMENT ON TABLE index_ohlcv_intraday IS
    '지수 30초/1분봉 (LS t8418, /indtp/chart). 2026-01-02부터 lookback 가능.';
COMMENT ON TABLE futures_ohlcv_intraday IS
    '선물 30초/1분봉. 지수선물=t8465(/futureoption/chart, lookback 가능). 주식선물=t8406(/futureoption/market-data, 당일만).';
