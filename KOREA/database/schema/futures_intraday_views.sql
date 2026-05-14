-- Phase 7 분봉 NEAR/NEXT 매핑 view (v2 — self-join 만기 정렬)
--
-- 문제: 인포맥스 NEXT 정의 ≠ LS NEXT 정의 (인포맥스는 long-dated annual을 NEXT로,
--       LS는 차월물을 NEXT로). 일별 contract_class를 그대로 매핑 join하면 view NEXT 비어있음.
--
-- 해결: 분봉에 적재된 contracts (= LS의 진짜 NEAR+NEXT)를 self-join 정렬.
--       각 (date, underlying)에 대해 만기 빠른 순 1=NEAR, 2=NEXT.
--
-- 만기 정렬 키: contract_code의 chars 4-5 (year, month)
--   A0166000 → ('6','6') = 2026 June
--   A0169000 → ('6','9') = 2026 Sep
--   A016C000 → ('6','C') = 2026 Dec
--   A0173000 → ('7','3') = 2027 March
--   ASCII alphabetical sort 가능 (월 코드: 1-9 → '1'-'9', 10-12 → 'A','B','C')
--
-- 한계: 2030년대 wraparound (year='0') 시 정렬 깨짐. 그때 수정 필요.

DROP VIEW IF EXISTS futures_intraday_near;
DROP VIEW IF EXISTS futures_intraday_next;
DROP VIEW IF EXISTS futures_intraday_with_class;

CREATE VIEW futures_intraday_with_class AS
WITH intraday_with_und AS (
    -- 일별 매핑 의존성 제거 — futures_code chars 2-3에서 underlying 직접 추출
    --   A0166000 → '01' (KP200)
    --   A0A65000 → '0A' (주식선물 종목)
    --   ABS61000 → 'BS' (주식선물 종목)
    -- WHERE: 단일선물(A로 시작)만, 스프레드(D로 시작) 옵션(C,P) 제외
    SELECT
        i.futures_code,
        i.time,
        i.interval_seconds,
        i.open, i.high, i.low, i.close,
        i.volume, i.trading_value, i.open_interest,
        substring(i.futures_code FROM 2 FOR 2) AS underlying_code,
        (i.time AT TIME ZONE 'Asia/Seoul')::date AS trade_date
    FROM futures_ohlcv_intraday i
    WHERE substring(i.futures_code FROM 1 FOR 1) = 'A'
),
ranked AS (
    SELECT *,
           DENSE_RANK() OVER (
               PARTITION BY underlying_code, trade_date
               ORDER BY substring(futures_code FROM 4 FOR 1),  -- year char
                        substring(futures_code FROM 5 FOR 1)   -- month char
           ) AS class_rank
    FROM intraday_with_und
)
SELECT
    futures_code, time, interval_seconds, underlying_code,
    open, high, low, close, volume, trading_value, open_interest,
    CASE class_rank WHEN 1 THEN 'NEAR' WHEN 2 THEN 'NEXT' ELSE 'OTHER' END AS contract_class
FROM ranked;

CREATE OR REPLACE VIEW futures_intraday_near AS
SELECT futures_code, time, interval_seconds, underlying_code,
       open, high, low, close, volume, trading_value, open_interest
FROM futures_intraday_with_class WHERE contract_class = 'NEAR';

CREATE OR REPLACE VIEW futures_intraday_next AS
SELECT futures_code, time, interval_seconds, underlying_code,
       open, high, low, close, volume, trading_value, open_interest
FROM futures_intraday_with_class WHERE contract_class = 'NEXT';

COMMENT ON VIEW futures_intraday_with_class IS
  'Phase 7 v2: 분봉 self-join 만기 정렬로 (date, underlying)별 NEAR/NEXT 자동 결정';
COMMENT ON VIEW futures_intraday_near IS
  'Phase 7: 각 (date, underlying)의 NEAR contract 30초봉';
COMMENT ON VIEW futures_intraday_next IS
  'Phase 7: 각 (date, underlying)의 NEXT contract 30초봉';
