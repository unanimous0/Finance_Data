-- Phase 1: 일봉/분봉 수정주가(adjusted price) 컬럼 추가
--
-- 배경:
--   - 현재 ohlcv_daily / ohlcv_intraday는 raw 가격만 저장
--   - 액면분할/병합/무증/유증 등 corporate action 미반영
--   - 예: LS일렉트릭(010120) 4/12 raw 788,000 → 4/13 182,200 (-77% 점프, 실제는 1:5 분할)
--
-- 수정 (이 마이그):
--   - ohlcv_daily 에 adj_* 컬럼 추가 — LS sujung=Y로 받기
--   - ohlcv_intraday 에 adj_factor 컬럼 추가 — 일봉의 (adj_close / close_price) 비율을
--     그 일자 분봉 모든 봉에 적용 (옵션 D — 일봉 기반 분봉 보정)
--
-- raw 컬럼은 영원히 변경 없음 (감사/검증/원본 보존). adj_* 컬럼만 corporate action 발생 시
-- UPDATE 실행.

-- ── 일봉 adjusted 컬럼 ─────────────────────────────────────────────────────
ALTER TABLE ohlcv_daily
  ADD COLUMN IF NOT EXISTS adj_open       NUMERIC(12, 2),
  ADD COLUMN IF NOT EXISTS adj_high       NUMERIC(12, 2),
  ADD COLUMN IF NOT EXISTS adj_low        NUMERIC(12, 2),
  ADD COLUMN IF NOT EXISTS adj_close      NUMERIC(12, 2),
  ADD COLUMN IF NOT EXISTS adj_factor     NUMERIC(20, 10),
  ADD COLUMN IF NOT EXISTS adj_updated_at TIMESTAMPTZ;

COMMENT ON COLUMN ohlcv_daily.adj_close IS
  'LS sujung=Y 수정 종가. 분할/병합/무증/유증/배당 종합 반영. raw close_price 유지';
COMMENT ON COLUMN ohlcv_daily.adj_factor IS
  '수정 비율 = adj_close / close_price. 분할 1:5 → 0.2. 분봉 계산에도 활용.';

-- ── 분봉 adjusted factor 컬럼 ─────────────────────────────────────────────
-- 분봉 자체엔 adj_open/high/low/close 안 만들고 factor 1개만 저장.
-- query 시 close_price * adj_factor → adjusted (호환 view 추후 추가 가능).
ALTER TABLE ohlcv_intraday
  ADD COLUMN IF NOT EXISTS adj_factor     NUMERIC(20, 10),
  ADD COLUMN IF NOT EXISTS adj_updated_at TIMESTAMPTZ;

COMMENT ON COLUMN ohlcv_intraday.adj_factor IS
  '그 일자 일봉 (adj_close/close_price) 비율. 분봉 수정가 = open*adj_factor 등. NULL=1 가정.';

-- ── corporate_actions 메타 테이블 (Phase 3에서 적재 시작) ─────────────────
CREATE TABLE IF NOT EXISTS corporate_actions (
  stock_code    VARCHAR(10) NOT NULL,
  event_date    DATE        NOT NULL,           -- 권리락일 (ex-date)
  event_type    VARCHAR(20) NOT NULL,           -- SPLIT|REVERSE_SPLIT|RIGHTS|STOCK_DIV|CAPITAL_RED|MERGER|SPINOFF|CASH_DIV
  ratio_before  NUMERIC(20,10),                 -- 옛 주식수 (or 기준 수치)
  ratio_after   NUMERIC(20,10),                 -- 새 주식수
  price_factor  NUMERIC(20,10) NOT NULL,        -- adjusted = raw × factor. 분할 1:5 = 0.2
  share_factor  NUMERIC(20,10),                 -- 주식수 배율 = 1/price_factor (volume 보정용)
  cash_amount   NUMERIC(20,4),                  -- 현금배당 (CASH_DIV)
  new_code      VARCHAR(10),                    -- 합병/spinoff 후 신규 코드
  source        VARCHAR(20) NOT NULL,           -- DART|KRX|LS|MANUAL
  rcept_no      VARCHAR(20),                    -- DART 공시 ID (idempotency)
  description   TEXT,
  applied       BOOLEAN DEFAULT FALSE,          -- 분봉/일봉 보정 완료 플래그
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (stock_code, event_date, event_type)
);
CREATE INDEX IF NOT EXISTS idx_corp_actions_event_date ON corporate_actions(event_date DESC);
CREATE INDEX IF NOT EXISTS idx_corp_actions_rcept ON corporate_actions(rcept_no) WHERE rcept_no IS NOT NULL;

COMMENT ON TABLE corporate_actions IS
  'Phase 3: 액면분할/병합/무증/유증/감자/합병/분할/현금배당 메타. DART primary + LS sujung 검증.';
