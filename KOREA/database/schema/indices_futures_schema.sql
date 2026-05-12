-- ==========================================
-- 지수 + 선물 마스터/일별 OHLCV
-- ==========================================
-- 데이터 소스:
--   indices              ← /api/index/code (type=K/Q/X/T/N)
--   index_ohlcv_daily    ← /api/index/hist (1000행 한도, chunks 호출)
--   futures_underlyings  ← /api/future/code 의 distinct underlying_code
--   futures_ohlcv_daily  ← /api/future/active|2active (근월/원월 연결시계열)
-- ==========================================

-- 지수 마스터
CREATE TABLE IF NOT EXISTS indices (
    code         VARCHAR(10) PRIMARY KEY,    -- KGG01P, K2G01P, KGS04P 등
    kr_name      VARCHAR(100),
    en_name      VARCHAR(100),
    index_type   CHAR(1),                     -- K=코스피 | Q=코스닥 | X=KRX | T=일반상품 | N=코넥스
    return_type  VARCHAR(5),                  -- PR | TR | NTR
    is_sector    BOOLEAN DEFAULT FALSE,       -- 섹터지수 여부 (kr_name 휴리스틱)
    created_at   TIMESTAMP DEFAULT NOW()
);

-- 지수 일별 OHLCV
CREATE TABLE IF NOT EXISTS index_ohlcv_daily (
    code           VARCHAR(10)   NOT NULL,
    time           DATE          NOT NULL,
    open           NUMERIC(14,4),
    high           NUMERIC(14,4),
    low            NUMERIC(14,4),
    close          NUMERIC(14,4) NOT NULL,
    change_pct     NUMERIC(7,3),
    volume         BIGINT,                   -- 거래량 (천주)
    trading_value  BIGINT,                   -- 거래대금 (백만원)
    marketcap      BIGINT,                   -- 시가총액 (백만원)
    constituents   INTEGER,                  -- 구성종목 수
    PRIMARY KEY (code, time)
);
CREATE INDEX IF NOT EXISTS idx_index_ohlcv_time ON index_ohlcv_daily(time DESC);

-- 선물 기초자산 마스터
CREATE TABLE IF NOT EXISTS futures_underlyings (
    underlying_code  VARCHAR(10) PRIMARY KEY,  -- 01, 06, GN, 11 등
    kr_name          VARCHAR(100),
    underlying_type  CHAR(1),                  -- F=지수 | L=개별주식 | C=금리/FX
    stock_code       VARCHAR(10),              -- L 타입이면 매칭되는 주식 stock_code (nullable)
    created_at       TIMESTAMP DEFAULT NOW()
);

-- 선물 일별 OHLCV (근월/원월 연결시계열)
CREATE TABLE IF NOT EXISTS futures_ohlcv_daily (
    underlying_code   VARCHAR(10) NOT NULL,
    contract_class    VARCHAR(4)  NOT NULL,   -- NEAR=근월 | NEXT=원월(차근월)
    time              DATE        NOT NULL,
    contract_code     VARCHAR(20),            -- 그 시점 실제 만기 종목코드 (A0166000 등)
    open              NUMERIC(14,4),
    high              NUMERIC(14,4),
    low               NUMERIC(14,4),
    close             NUMERIC(14,4),
    settle_price      NUMERIC(14,4),
    volume            BIGINT,                 -- 거래량
    trading_value     BIGINT,                 -- 거래대금
    open_interest     BIGINT,                 -- 미결제약정
    theoretical_price NUMERIC(14,4),          -- 이론가
    underlying_basis  NUMERIC(14,4),          -- 시장 베이시스 (현물대비)
    theoretical_basis NUMERIC(14,4),          -- 이론 베이시스
    PRIMARY KEY (underlying_code, contract_class, time)
);
CREATE INDEX IF NOT EXISTS idx_fut_ohlcv_time ON futures_ohlcv_daily(time DESC);

COMMENT ON TABLE indices              IS '지수 마스터 (/api/index/code)';
COMMENT ON TABLE index_ohlcv_daily    IS '지수 일별 OHLCV (/api/index/hist)';
COMMENT ON TABLE futures_underlyings  IS '선물 기초자산 마스터 (/api/future/code distinct)';
COMMENT ON TABLE futures_ohlcv_daily  IS '선물 일별 OHLCV — active(NEAR)/2active(NEXT) 연결시계열';
