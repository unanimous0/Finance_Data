"""
30초봉 수집 종목 스코프 (Phase 6)

union 3 소스:
  1. KOSPI200 + KOSDAQ150 active (index_components SCD2)
  2. 한국 ETF (해외 키워드 제외) — LENS etf_top200_korea.txt 동일 룰
  3. 한국 ETF의 PDF 구성종목 union (etf_portfolios SCD2 active) — 잡주 포함

→ 약 2,500~2,700 종목 추정

backfill_30sec_bars.py / daily_update STEP 5 / 임의 수집 도구 공통 호출.
"""

SCOPE_QUERY = """
SELECT DISTINCT stock_code FROM (
    -- 1) KOSPI200 + KOSDAQ150 active 멤버
    SELECT stock_code FROM index_components
     WHERE index_name IN ('KOSPI200','KOSDAQ150') AND end_date IS NULL

    UNION

    -- 2) 한국 ETF (해외 키워드 제외)
    SELECT stock_code FROM stocks
     WHERE market = 'ETF' AND is_active = TRUE
       AND NOT (
           stock_name ~ '(미국|나스닥|NASDAQ|S&P|필라델피아|차이나|항셍|일본|베트남|인도|유럽|뉴욕|INDXX|SOLACTIVE|WTI|원유|은선물|천연가스|옥수수|대두|엔비디아|테슬라|구글|팔란티어|마이크로소프트|아마존|애플|메타)'
           OR (stock_name LIKE '%(H)%' AND stock_name NOT LIKE '%KRX%')
           OR (stock_name LIKE '%글로벌%' AND stock_name NOT LIKE '%K-글로벌%' AND stock_name NOT LIKE '%K글로벌%')
       )

    UNION

    -- 3) 한국 ETF PDF 구성종목 union — stocks 매칭만 (외국주식/채권/의사코드 제외)
    SELECT epd.component_code AS stock_code
      FROM etf_portfolio_daily epd
      JOIN stocks s ON s.stock_code = epd.component_code
     WHERE epd.snapshot_date = (SELECT MAX(snapshot_date) FROM etf_portfolio_daily)
       AND epd.is_cash = FALSE
       AND s.is_active = TRUE
) sub
ORDER BY 1
"""


def fetch_minute_scope(conn) -> list[str]:
    """30초봉 수집 대상 종목코드 list 반환 (정렬됨, 중복 제거됨)."""
    with conn.cursor() as cur:
        cur.execute(SCOPE_QUERY)
        return [r[0] for r in cur.fetchall()]
