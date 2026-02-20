"""
FnGuide 웹 크롤링 - 발행주식수(보통주), 유동주식수, 유동비율
대상: DB의 KOSPI/KOSDAQ 전 종목 (is_active=True)
저장: floating_shares 테이블, base_date = 2026-02-19

URL: https://comp.fnguide.com/SVO2/asp/SVD_Main.asp?pGB=1&gicode=A{code}&...
"""

import time
import sys
import psycopg2
import requests
from bs4 import BeautifulSoup
from datetime import date
from collections import deque

# ── 설정 ──────────────────────────────────────────────────────────
TARGET_DATE  = date(2026, 2, 19)
BASE_URL     = "https://comp.fnguide.com/SVO2/asp/SVD_Main.asp"
PARAMS_BASE  = {
    "pGB": "1", "cID": "", "MenuYn": "Y",
    "ReportGB": "", "NewMenuID": "101", "stkGb": "701",
}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
REQUEST_DELAY     = 0.5    # 초 (분당 약 120회)
MAX_RETRIES       = 3
RETRY_DELAY       = 8.0
BATCH_SIZE        = 100    # DB 저장 배치 크기
PRINT_EVERY       = 50     # 로그 출력 주기
BLOCK_CHECK_EVERY = 20     # 차단 감지 검사 주기 (연속 no-data 카운트)
BLOCK_THRESHOLD   = 15     # 연속 N개 no-data 이면 차단 의심


def log(msg: str):
    """즉시 flush 출력"""
    print(msg, flush=True)


# ── DB 연결 ───────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host="localhost", dbname="korea_stock_data",
        user="postgres", password="",
    )


def fetch_stocks(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT stock_code, stock_name
            FROM stocks
            WHERE is_active = TRUE
              AND market IN ('KOSPI', 'KOSDAQ')
            ORDER BY stock_code
        """)
        return cur.fetchall()


# ── 파싱 헬퍼 ─────────────────────────────────────────────────────
def parse_number(s: str):
    s = s.strip().split("/")[0].replace(",", "").strip()
    try:
        return int(s)
    except ValueError:
        return None


def parse_ratio(s: str):
    parts = s.strip().split("/")
    if len(parts) < 2:
        return None
    try:
        return float(parts[1].strip().replace(",", ""))
    except ValueError:
        return None


# ── 페이지 크롤링 ─────────────────────────────────────────────────
def crawl_one(session: requests.Session, stock_code: str):
    """
    Returns: (total_shares, floating_shares, floating_ratio_site, blocked)
    """
    params = {**PARAMS_BASE, "gicode": f"A{stock_code}"}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(BASE_URL, params=params, headers=HEADERS, timeout=15)

            # HTTP 오류
            if resp.status_code != 200:
                log(f"  !! HTTP {resp.status_code} for {stock_code} (시도 {attempt})")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                    continue
                return None, None, None, False

            # 실제 차단 키워드 감지 (블로킹=True 반환)
            block_keywords = ["access denied", "403 Forbidden", "captcha", "비정상적인 접근"]
            for kw in block_keywords:
                if kw.lower() in resp.text.lower():
                    log(f"  !! 차단 키워드 감지 '{kw}': {stock_code}")
                    return None, None, None, True

            # 응답 짧음 = 우선주/데이터없는 종목 → no_data 처리 (차단 아님)
            if len(resp.text) < 5000:
                return None, None, None, False

            break

        except requests.Timeout:
            log(f"  !! Timeout (시도 {attempt}/{MAX_RETRIES}): {stock_code}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        except requests.RequestException as e:
            log(f"  !! 요청 오류: {stock_code} - {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    else:
        return None, None, None, False

    soup = BeautifulSoup(resp.text, "lxml")
    total_shares = None
    floating_shares_val = None
    floating_ratio_site = None

    for th in soup.find_all("th"):
        th_text = th.get_text(strip=True)
        if "발행주식수" in th_text and ("보통주" in th_text or "우선주" in th_text):
            td = th.find_next_sibling("td")
            if td:
                total_shares = parse_number(td.get_text(strip=True))
            break

    for th in soup.find_all("th"):
        th_text = th.get_text(strip=True)
        if "유동주식수" in th_text and "비율" in th_text:
            td = th.find_next_sibling("td")
            if td:
                raw = td.get_text(strip=True)
                floating_shares_val = parse_number(raw)
                floating_ratio_site = parse_ratio(raw)
            break

    return total_shares, floating_shares_val, floating_ratio_site, False


# ── 검증 ─────────────────────────────────────────────────────────
def verify_ratio(total, floating, site_ratio):
    """
    직접 계산 비율 vs 사이트 비율 비교
    ※ FnGuide 분모 = 지수산정주식수 (≠ 발행주식수보통주)
       우선주 있는 종목 등은 구조적으로 불일치 → 허용 오차 ±0.5
    Returns: (calc_ratio, is_match)
    """
    if total is None or floating is None or total == 0:
        return None, False
    calc = round(floating * 100 / total, 2)
    match = site_ratio is not None and abs(calc - site_ratio) <= 0.5
    return calc, match


# ── DB 저장 ───────────────────────────────────────────────────────
UPSERT_SQL = """
INSERT INTO floating_shares (stock_code, base_date, total_shares, floating_shares, floating_ratio)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (stock_code, base_date)
DO UPDATE SET
    total_shares    = EXCLUDED.total_shares,
    floating_shares = EXCLUDED.floating_shares,
    floating_ratio  = EXCLUDED.floating_ratio
"""


def bulk_upsert(conn, rows: list):
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
    conn.commit()


# ── 메인 ─────────────────────────────────────────────────────────
def main():
    conn = get_conn()
    stocks = fetch_stocks(conn)
    total_count = len(stocks)
    log(f"대상 종목 수: {total_count}개 (KOSPI + KOSDAQ)")
    log(f"예상 소요 시간: 약 {total_count * REQUEST_DELAY / 60:.0f}분")
    log("=" * 60)

    session = requests.Session()

    rows_to_insert = []
    stats = {"success": 0, "no_data": 0, "mismatch": 0, "blocked": 0, "ratio_null": 0}
    mismatch_list = []

    # 최근 N개 no_data 연속 카운트 (차단 감지용)
    recent_results = deque(maxlen=BLOCK_CHECK_EVERY)
    consecutive_no_data = 0

    for i, (stock_code, stock_name) in enumerate(stocks, 1):
        total_s, float_s, ratio_site, blocked = crawl_one(session, stock_code)

        if blocked:
            stats["blocked"] += 1
            log(f"\n[{i:4}/{total_count}] ★ 차단 감지! {stock_code} {stock_name}")
            log("  → 60초 대기 후 재시도...")
            time.sleep(60)
            # 한 번 더 시도
            total_s, float_s, ratio_site, blocked2 = crawl_one(session, stock_code)
            if blocked2:
                log("  → 재시도도 실패. 크롤링을 중단합니다.")
                break

        # 데이터 없음 처리
        if total_s is None and float_s is None:
            stats["no_data"] += 1
            consecutive_no_data += 1
            recent_results.append(False)

            if i <= 10 or i % PRINT_EVERY == 0:
                log(f"[{i:4}/{total_count}] {stock_code} {stock_name[:12]:<12} → 데이터없음")
        else:
            consecutive_no_data = 0
            recent_results.append(True)

            calc_ratio, matched = verify_ratio(total_s, float_s, ratio_site)

            # floating_ratio: 사이트 값 우선, 없으면 계산값 사용
            # NUMERIC(5,2) 범위(0~999.99) 내만 저장
            if ratio_site is not None and 0 <= ratio_site <= 999.99:
                db_ratio = ratio_site
            elif calc_ratio is not None and 0 <= calc_ratio <= 999.99:
                db_ratio = calc_ratio
            else:
                db_ratio = None
                stats["ratio_null"] += 1

            if not matched and ratio_site is not None:
                stats["mismatch"] += 1
                mismatch_list.append((stock_code, stock_name, total_s, float_s, ratio_site, calc_ratio))

            rows_to_insert.append((stock_code, TARGET_DATE, total_s, float_s, db_ratio))
            stats["success"] += 1

            # 로그 출력 (처음 10개 + 매 N개마다)
            if i <= 10 or i % PRINT_EVERY == 0:
                match_str = "✓" if matched else f"✗(사이트:{ratio_site} 계산:{calc_ratio})"
                log(
                    f"[{i:4}/{total_count}] {stock_code} {stock_name[:12]:<12} "
                    f"| 발행:{total_s:>13,} 유동:{float_s:>13,} 비율:{db_ratio} {match_str}"
                )

        # ── 차단 감지 (연속 no-data 체크) ────────────────────────
        if consecutive_no_data >= BLOCK_THRESHOLD:
            log(f"\n★ 경고: 연속 {consecutive_no_data}개 데이터 없음 → 차단 의심!")
            log("  → 90초 대기 후 계속...")
            time.sleep(90)
            consecutive_no_data = 0  # 리셋하고 계속 진행

        # ── 주기적 진행 상황 요약 ─────────────────────────────────
        if i % 200 == 0:
            log(
                f"\n--- 진행 요약 [{i}/{total_count}] "
                f"성공:{stats['success']} 없음:{stats['no_data']} "
                f"불일치:{stats['mismatch']} 차단:{stats['blocked']} ---\n"
            )

        # ── 배치 DB 저장 ─────────────────────────────────────────
        if len(rows_to_insert) >= BATCH_SIZE:
            bulk_upsert(conn, rows_to_insert)
            rows_to_insert.clear()

        time.sleep(REQUEST_DELAY)

    # 잔여 저장
    if rows_to_insert:
        bulk_upsert(conn, rows_to_insert)

    # ── 최종 결과 요약 ────────────────────────────────────────────
    log("\n" + "=" * 60)
    log(f"크롤링 완료: {total_count}종목")
    log(f"  ✅ 성공:       {stats['success']:>5}개")
    log(f"  ❌ 데이터없음: {stats['no_data']:>5}개")
    log(f"  ⚠️  비율불일치: {stats['mismatch']:>5}개  (지수산정주식수 분모 차이)")
    log(f"  ⚠️  비율NULL:   {stats['ratio_null']:>5}개")
    log(f"  🚫 차단감지:   {stats['blocked']:>5}회")

    if mismatch_list:
        log(f"\n비율 불일치 샘플 (최대 10개):")
        for row in mismatch_list[:10]:
            code, name, tot, flt, r_site, r_calc = row
            log(f"  {code} {name}: 발행={tot:,} 유동={flt:,} 사이트={r_site}% 계산={r_calc}%")

    # DB 반영 결과 확인
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*), COUNT(floating_ratio)
            FROM floating_shares
            WHERE base_date = %s
        """, (TARGET_DATE,))
        total_rows, ratio_filled = cur.fetchone()
    log(f"\nDB 확인: base_date={TARGET_DATE} → 총 {total_rows}개 저장, 비율 채움 {ratio_filled}개")

    conn.close()


if __name__ == "__main__":
    main()
