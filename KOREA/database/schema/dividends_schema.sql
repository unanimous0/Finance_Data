-- ==========================================
-- 배당 데이터 스키마 (dividends)
-- ==========================================
-- 설계 원칙:
--   1) LENS export 형식(외부 contract)과 별도로, 내부는 더 풍부한 컬럼 보유
--   2) 정정공시 이력 보존 → surrogate PK + version 컬럼
--   3) 추정값과 확정값 동시 보관 가능 (confirmed 플래그)
--   4) 데이터 소스 추적 (DART/SEIBro/KRX/ESTIMATE)
--   5) 정관변경 분류 (A: 변경, B: 미변경) → 배당기준일 예측 신뢰도
--
-- Hypertable 미적용:
--   - 데이터량이 작음 (≈ 종목수 × 분기 × 연수 × 정정버전 → 수만~수십만 행)
--   - PK가 id (surrogate)라 hypertable 제약(time 포함 PK)과 부적합
--   - 접근 패턴이 시간범위 스캔보다 (code, fiscal_year, period) 조회 위주
-- ==========================================

CREATE TABLE IF NOT EXISTS dividends (
    -- ── 식별자 ──────────────────────────────
    id              BIGSERIAL PRIMARY KEY,
    code            VARCHAR(10)  NOT NULL,           -- 종목코드 (Stock.stock_code 와 같은 형식, A 접두 X)
    fiscal_year     INTEGER      NOT NULL,           -- 회계연도 (record_date NULL인 추정값도 식별 가능)
    period          VARCHAR(8)   NOT NULL,           -- 'Q1'|'Q2'|'Q3'|'Q4'|'H1'|'ANNUAL'
    version         INTEGER      NOT NULL DEFAULT 1, -- 1=원공시, 2+=정정공시
    is_latest       BOOLEAN      NOT NULL DEFAULT TRUE, -- 같은 (code, fy, period) 그룹의 최신 행

    -- ── 일정 ──────────────────────────────
    board_resolution_date  DATE,                     -- 이사회 결의일
    announced_at           TIMESTAMP,                -- 공시일시 (DART 접수일시 등)
    record_date            DATE,                     -- 배당기준일 (NULL 가능: 공시 전 추정)
    ex_date                DATE,                     -- 배당락일 (record_date의 직전 영업일)
    pay_date               DATE,                     -- 지급예정일

    -- ── 금액 ──────────────────────────────
    amount          NUMERIC(18, 4) NOT NULL,         -- 1주당 배당금 (원). 추정값도 항상 채움
    yield_pct       NUMERIC(7, 3),                   -- 시가배당률 (%)
    dividend_type   VARCHAR(10) NOT NULL DEFAULT 'CASH', -- 'CASH'|'STOCK'|'SPECIAL' (현재는 CASH만)

    -- ── 메타 ──────────────────────────────
    confirmed         BOOLEAN     NOT NULL DEFAULT FALSE,
    estimation_basis  VARCHAR(200),                  -- 추정 근거 (UI 툴팁용, 한국어 짧은 설명)
    charter_group     CHAR(1),                       -- 'A'=정관변경, 'B'=미변경 (NULL=미분류)
    source            VARCHAR(10) NOT NULL,          -- 'DART'|'SEIBro'|'KRX'|'ESTIMATE'
    dart_rcp_no       VARCHAR(20),                   -- DART 접수번호 (raw_text_url 생성용 + 중복 방지)
    raw_text_url      TEXT,                          -- 공시 원문 URL
    raw_text          TEXT,                          -- 공시 원문 (파싱 검증/재처리용, 선택)

    -- ── 타임스탬프 ─────────────────────────
    created_at        TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMP   NOT NULL DEFAULT NOW(),

    -- ── 제약조건 ──────────────────────────
    CONSTRAINT uq_dividends_version
        UNIQUE (code, fiscal_year, period, version),

    CONSTRAINT chk_dividends_period
        CHECK (period IN ('Q1','Q2','Q3','Q4','H1','ANNUAL')),

    CONSTRAINT chk_dividends_dividend_type
        CHECK (dividend_type IN ('CASH','STOCK','SPECIAL')),

    CONSTRAINT chk_dividends_source
        CHECK (source IN ('DART','SEIBro','KRX','ESTIMATE')),

    CONSTRAINT chk_dividends_charter_group
        CHECK (charter_group IS NULL OR charter_group IN ('A','B')),

    CONSTRAINT chk_dividends_version_positive
        CHECK (version >= 1)
);

-- ==========================================
-- 인덱스
-- ==========================================

-- 기본 조회: 종목별 배당 이력
CREATE INDEX IF NOT EXISTS idx_dividends_code_fy
    ON dividends(code, fiscal_year DESC, period);

-- 종목차익 매칭: ex_date 기반 (NULL 제외)
CREATE INDEX IF NOT EXISTS idx_dividends_ex_date
    ON dividends(ex_date)
    WHERE ex_date IS NOT NULL;

-- 종목별 배당락일 조회 (선물 월물 매칭용)
CREATE INDEX IF NOT EXISTS idx_dividends_code_ex
    ON dividends(code, ex_date)
    WHERE ex_date IS NOT NULL;

-- LENS export 디폴트 조회: 최신 행만
CREATE INDEX IF NOT EXISTS idx_dividends_latest
    ON dividends(code, fiscal_year, period)
    WHERE is_latest = TRUE;

-- 공시 시각 기반 정렬/필터
CREATE INDEX IF NOT EXISTS idx_dividends_announced
    ON dividends(announced_at DESC)
    WHERE announced_at IS NOT NULL;

-- DART 접수번호 중복 체크용
CREATE INDEX IF NOT EXISTS idx_dividends_dart_rcp
    ON dividends(dart_rcp_no)
    WHERE dart_rcp_no IS NOT NULL;

-- ==========================================
-- 트리거: updated_at 자동 갱신
-- ==========================================

CREATE OR REPLACE FUNCTION trg_dividends_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS dividends_updated_at ON dividends;
CREATE TRIGGER dividends_updated_at
    BEFORE UPDATE ON dividends
    FOR EACH ROW
    EXECUTE FUNCTION trg_dividends_updated_at();

-- ==========================================
-- COMMENT (DB 레벨 문서화)
-- ==========================================

COMMENT ON TABLE  dividends IS '배당 이벤트 (현금/주식/특별). 정정공시 이력은 version 컬럼으로 보존';
COMMENT ON COLUMN dividends.charter_group     IS 'A=정관변경(이사회 결의로 배당기준일 지정) / B=정관미변경(결산일=배당기준일)';
COMMENT ON COLUMN dividends.is_latest         IS '같은 (code, fiscal_year, period)에서 가장 최근 version만 TRUE';
COMMENT ON COLUMN dividends.confirmed         IS 'TRUE=공시 확정값 / FALSE=추정값';
COMMENT ON COLUMN dividends.estimation_basis  IS '추정 근거 (한국어 짧은 설명, UI 툴팁 표시용)';
COMMENT ON COLUMN dividends.source            IS '데이터 출처. DART(공시), SEIBro(예탁원), KRX(거래소), ESTIMATE(자체추정)';
COMMENT ON COLUMN dividends.dart_rcp_no       IS 'DART 접수번호. raw_text_url 생성 + 중복 수집 방지에 사용';
