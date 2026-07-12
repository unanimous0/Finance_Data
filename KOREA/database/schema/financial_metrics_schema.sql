-- 분기 재무지표 (FnGuide XML 기반)
-- 단위: 금액은 원(KRW), 비율은 %, 주당 항목은 원
-- 출처: https://comp.fnguide.com/SVO2/xml/Snapshot_all/{stock_code}.xml

CREATE TYPE financial_data_type AS ENUM ('actual', 'preliminary', 'estimate');

CREATE TABLE financial_metrics_quarterly (
    stock_code          VARCHAR(6)          NOT NULL REFERENCES stocks(stock_code),
    period_end          DATE                NOT NULL,  -- 분기말: 2026-03-31
    fs_type             VARCHAR(3)          NOT NULL,  -- CFS=연결, OFS=별도
    data_type           financial_data_type NOT NULL,  -- actual/preliminary/estimate

    -- 손익계산서 (억원 → 원 변환 저장)
    revenue             BIGINT,   -- 매출액
    operating_profit    BIGINT,   -- 영업이익
    net_income          BIGINT,   -- 당기순이익
    controlling_ni      BIGINT,   -- 지배주주순이익

    -- 재무상태표
    total_assets        BIGINT,   -- 자산총계
    total_equity        BIGINT,   -- 자본총계
    controlling_equity  BIGINT,   -- 지배주주지분

    -- 주당 지표 (원)
    eps                 INTEGER,  -- 주당순이익
    bps                 INTEGER,  -- 주당순자산
    dps                 INTEGER,  -- 주당배당금

    -- 비율
    per                 NUMERIC(10, 2),  -- 주가수익비율
    pbr                 NUMERIC(10, 2),  -- 주가순자산비율
    roe                 NUMERIC(10, 2),  -- 자기자본이익률(%)
    roa                 NUMERIC(10, 2),  -- 총자산이익률(%)
    operating_margin    NUMERIC(10, 2),  -- 영업이익률(%)

    -- 기타
    shares_outstanding  BIGINT,          -- 발행주식수 (천주 → 주 변환)

    collected_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (stock_code, period_end, fs_type)
);

COMMENT ON TABLE financial_metrics_quarterly IS 'FnGuide XML 분기 재무지표. data_type: actual=확정, preliminary=잠정(P), estimate=추정(E)';
COMMENT ON COLUMN financial_metrics_quarterly.period_end IS '분기말 날짜 (e.g. 2026-03-31 for 2026/03)';
COMMENT ON COLUMN financial_metrics_quarterly.fs_type IS 'CFS=연결(IFRS), OFS=별도(IFRS)';
