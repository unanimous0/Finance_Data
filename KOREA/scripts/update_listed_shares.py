"""
상장주식수 + 유동비율 갱신 스크립트 — 주 1회 (일요일 03:30 KST cron)

Phase 1 (상장주식수): LS t1102 listing × 1000 → floating_shares.total_shares
  - 대상: DB 활성 종목 전체 (KOSPI + KOSDAQ + ETF + KONEX)
  - 용도: daily_update market_cap = close × total_shares  (핵심 — 먼저 커밋)

Phase 2 (유동비율): Naver 기업개요 iframe = wisereport(navercomp.wisereport.co.kr)
  '발행주식수/유동비율' → floating_ratio + floating_shares(=total_shares×비율)
  - 대상: 비ETF 활성 종목 (ETF는 유동 개념 부적합, 2월 원본도 비ETF)
  - 용도: LENS float 분석. 2026-02 이후 갱신 끊겼던 것 복구 (2026-07 재구축)
  - None 가드: 유동비율 못 받으면 기존 값 안 덮음

단위: t1102 listing = 천주 → × 1000 = 주
"""

from __future__ import annotations

import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
import requests
from bs4 import BeautifulSoup

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from collectors.ls_api import LsApiClient
from config.settings import settings

KST = ZoneInfo("Asia/Seoul")

UPSERT_SQL = """
INSERT INTO floating_shares (stock_code, base_date, total_shares)
VALUES %s
ON CONFLICT (stock_code, base_date) DO UPDATE SET
    total_shares = EXCLUDED.total_shares
"""

# Phase 2 — 유동비율 (wisereport = Naver 기업개요 데이터 소스)
WISEREPORT_URL = "https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={code}"
FLOAT_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
FLOAT_DELAY = 0.3   # wisereport 예의상 딜레이

# 유동비율/유동주식수 갱신 (floating_shares = total_shares × 비율). 오늘 base_date 행 대상.
FLOAT_UPDATE_SQL = """
UPDATE floating_shares
SET floating_ratio  = %s,
    floating_shares = ROUND(total_shares * %s / 100.0)
WHERE stock_code = %s AND base_date = %s
"""


def fetch_float_ratio(code: str) -> float | None:
    """wisereport에서 유동비율(%) 추출. '발행주식수/유동비율' 셀의 '… / NN.NN%'.
    실패 시 None (호출자가 None 가드로 기존 값 보존)."""
    try:
        r = requests.get(WISEREPORT_URL.format(code=code), headers=FLOAT_HEADERS, timeout=15)
        r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, "html.parser")
        for el in soup.find_all(["th", "td", "span", "dt", "dd"]):
            if el.get_text(strip=True) == "발행주식수/유동비율":
                nxt = el.find_next(["td", "dd", "span"])
                m = re.search(r"/\s*([\d.]+)\s*%", nxt.get_text(" ", strip=True) if nxt else "")
                if m:
                    ratio = float(m.group(1))
                    if 0 < ratio <= 100:
                        return ratio
                return None
    except Exception:
        pass
    return None


def get_conn():
    return psycopg2.connect(
        host=settings.DB_HOST, dbname=settings.DB_NAME,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
    )


def get_all_stocks(conn) -> list[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT stock_code, stock_name FROM stocks
            WHERE is_active = TRUE
              AND market IN ('KOSPI', 'KOSDAQ', 'ETF', 'KONEX')
            ORDER BY stock_code
        """)
        return cur.fetchall()


def main():
    today = date.today()
    conn = get_conn()
    ls = LsApiClient()
    url = f"{settings.LS_BASE_URL}/stock/market-data"

    try:
        stocks = get_all_stocks(conn)
        total = len(stocks)
        print(f"[상장주식수 갱신] {today} / {total}개 종목")

        batch = []
        ok = skip = err = 0

        for i, (code, name) in enumerate(stocks, 1):
            try:
                data = ls._post_generic("t1102", url, "t1102InBlock", {"shcode": code})
                out = data.get("t1102OutBlock") or {}
                listing = out.get("listing")
                if listing and int(listing) > 0:
                    total_shares = int(listing) * 1000
                    batch.append((code, today, total_shares))
                    ok += 1
                else:
                    skip += 1
            except Exception as e:
                print(f"  err {code} {name}: {e}", flush=True)
                err += 1

            if len(batch) >= 500:
                with conn:
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_values(cur, UPSERT_SQL, batch, page_size=200)
                batch.clear()

            if i % 500 == 0 or i == total:
                print(f"  [{i:>5}/{total}] ok={ok} skip={skip} err={err}", flush=True)

        if batch:
            with conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(cur, UPSERT_SQL, batch, page_size=200)

        print(f"[Phase1 상장주식수 완료] ok={ok} / skip={skip} / err={err}")

        # ── Phase 2: 유동비율 (wisereport) — 비ETF 활성 종목만 ──
        with conn.cursor() as cur:
            cur.execute("""
                SELECT stock_code, stock_name FROM stocks
                WHERE is_active = TRUE AND market IN ('KOSPI', 'KOSDAQ', 'KONEX')
                ORDER BY stock_code
            """)
            nonetf = cur.fetchall()
        print(f"[Phase2 유동비율] {len(nonetf)}개 (비ETF), 소스=wisereport")
        f_ok = f_none = f_err = 0
        for i, (code, name) in enumerate(nonetf, 1):
            try:
                ratio = fetch_float_ratio(code)
                if ratio is not None:
                    with conn:
                        with conn.cursor() as cur:
                            cur.execute(FLOAT_UPDATE_SQL, (ratio, ratio, code, today))
                    f_ok += 1
                else:
                    f_none += 1   # None 가드 — 기존 값 안 덮음
            except Exception as e:
                print(f"  float err {code} {name}: {e}", flush=True)
                f_err += 1
            time.sleep(FLOAT_DELAY)
            if i % 500 == 0 or i == len(nonetf):
                print(f"  [{i:>5}/{len(nonetf)}] ok={f_ok} none={f_none} err={f_err}", flush=True)

        print(f"[Phase2 유동비율 완료] ok={f_ok} / none={f_none} / err={f_err}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
