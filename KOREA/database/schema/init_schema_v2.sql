-- TimescaleDB 확장 확인
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ==========================================
-- 메타데이터 테이블
-- ==========================================

-- 종목 마스터 (수정됨: standard_code 추가, market NULL 허용)
CREATE TABLE IF NOT EXISTS stocks (
    stock_code VARCHAR(10) PRIMARY KEY,
    stock_name VARCHAR(100) NOT NULL,
    standard_code VARCHAR(12) UNIQUE,  -- 국제표준코드 (ISIN) 추가
    market VARCHAR(10),  -- NULL 허용으로 변경 (KOSPI, KOSDAQ, ETF)
    sector_id INTEGER,
    listing_date DATE,
    delisting_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stocks_market ON stocks(market);
CREATE INDEX IF NOT EXISTS idx_stocks_active ON stocks(is_active);
CREATE INDEX IF NOT EXISTS idx_stocks_standard_code ON stocks(standard_code);

-- 섹터 분류
CREATE TABLE IF NOT EXISTS sectors (
    sector_id SERIAL PRIMARY KEY,
    sector_name VARCHAR(100) NOT NULL,
    sector_code VARCHAR(20),
    parent_sector_id INTEGER REFERENCES sectors(sector_id),
    created_at TIMESTAMP DEFAULT NOW()
);

-- 지수 구성종목
CREATE TABLE IF NOT EXISTS index_components (
    id SERIAL PRIMARY KEY,
    index_name VARCHAR(50) NOT NULL,
    stock_code VARCHAR(10) REFERENCES stocks(stock_code),
    effective_date DATE NOT NULL,
    end_date DATE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(index_name, stock_code, effective_date)
);

CREATE INDEX IF NOT EXISTS idx_index_components_stock ON index_components(stock_code);
CREATE INDEX IF NOT EXISTS idx_index_components_date ON index_components(effective_date);

-- 유동주식
CREATE TABLE IF NOT EXISTS floating_shares (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) REFERENCES stocks(stock_code),
    base_date DATE NOT NULL,
    total_shares BIGINT,
    floating_shares BIGINT,
    floating_ratio DECIMAL(5,2),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(stock_code, base_date)
);

CREATE INDEX IF NOT EXISTS idx_floating_shares_date ON floating_shares(base_date DESC);

-- ETF 포트폴리오
CREATE TABLE IF NOT EXISTS etf_portfolios (
    id SERIAL PRIMARY KEY,
    etf_code VARCHAR(10) REFERENCES stocks(stock_code),
    component_code VARCHAR(10) REFERENCES stocks(stock_code),
    base_date DATE NOT NULL,
    weight DECIMAL(7,4),
    shares BIGINT,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(etf_code, component_code, base_date)
);

CREATE INDEX IF NOT EXISTS idx_etf_portfolios_etf ON etf_portfolios(etf_code, base_date DESC);

-- ==========================================
-- 시계열 테이블 (TimescaleDB Hypertables)
-- ==========================================

-- 일별 시가총액
CREATE TABLE IF NOT EXISTS market_cap_daily (
    time DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    market_cap BIGINT,
    shares_outstanding BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);

SELECT create_hypertable('market_cap_daily', 'time',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

CREATE INDEX IF NOT EXISTS idx_market_cap_stock ON market_cap_daily(stock_code, time DESC);

-- 일별 OHLCV
CREATE TABLE IF NOT EXISTS ohlcv_daily (
    time DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    open_price INTEGER,
    high_price INTEGER,
    low_price INTEGER,
    close_price INTEGER,
    volume BIGINT,
    trading_value BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);

SELECT create_hypertable('ohlcv_daily', 'time',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_stock ON ohlcv_daily(stock_code, time DESC);

-- 투자자별 수급
CREATE TABLE IF NOT EXISTS investor_trading (
    time DATE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    investor_type VARCHAR(20) NOT NULL,  -- FOREIGN, INSTITUTION, RETAIL, PENSION
    net_buy_volume BIGINT,
    net_buy_value BIGINT,
    buy_volume BIGINT,
    sell_volume BIGINT,
    buy_value BIGINT,
    sell_value BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);

SELECT create_hypertable('investor_trading', 'time',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

CREATE INDEX IF NOT EXISTS idx_investor_trading_stock ON investor_trading(stock_code, investor_type, time DESC);

-- ==========================================
-- 모니터링 테이블
-- ==========================================

-- 데이터 수집 로그
CREATE TABLE IF NOT EXISTS data_collection_logs (
    id SERIAL PRIMARY KEY,
    data_type VARCHAR(50) NOT NULL,
    collection_date DATE NOT NULL,
    source VARCHAR(50),
    status VARCHAR(20) NOT NULL,  -- SUCCESS, FAILED, PARTIAL
    records_count INTEGER,
    error_message TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_collection_logs_date ON data_collection_logs(collection_date DESC);

-- 데이터 품질 체크
CREATE TABLE IF NOT EXISTS data_quality_checks (
    id SERIAL PRIMARY KEY,
    table_name VARCHAR(50) NOT NULL,
    check_date DATE NOT NULL,
    check_type VARCHAR(50) NOT NULL,
    issue_count INTEGER DEFAULT 0,
    details JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quality_checks_table ON data_quality_checks(table_name, check_date DESC);
