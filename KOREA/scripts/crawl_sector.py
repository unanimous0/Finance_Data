"""
FnGuide FICS 업종(섹터) 크롤링 스크립트

대상: KOSPI + KOSDAQ 활성 종목 (ETF 제외)
소스: https://comp.fnguide.com (FICS = FnGuide Industry Classification Standard, GICS 기반)
결과: stock_sectors 테이블 UPSERT

실행:
    python scripts/crawl_sector.py             # 전체 수집 (초기 or 갱신)
    python scripts/crawl_sector.py --missing   # stock_sectors에 없는 종목만 수집 (신규 상장 후)

소요 시간: 약 70분 (2,720개 × 1.5초)
업데이트 주기: 분기 1회 (FICS 변경은 드묾)
"""

import sys
import time
import re
import argparse
from pathlib import Path
from datetime import datetime

import psycopg2
import requests
from bs4 import BeautifulSoup
from loguru import logger

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings


# ── 설정 ──────────────────────────────────────────────────────────────────────
DELAY      = 1.5    # 요청 간 대기 (초) — FnGuide 차단 방지
RETRY      = 3      # 실패 시 재시도 횟수
TIMEOUT    = 15     # 요청 타임아웃 (초)
LOG_EVERY  = 100    # N개마다 진행 현황 출력

FNGUIDE_URL = (
    "https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp"
    "?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=101&stkGb=701"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://comp.fnguide.com/",
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_sectors (
    stock_code  VARCHAR(10) PRIMARY KEY REFERENCES stocks(stock_code),
    fics_sector VARCHAR(100),
    updated_at  TIMESTAMP DEFAULT NOW()
);
COMMENT ON TABLE  stock_sectors               IS 'FnGuide FICS 업종 분류 (GICS 기반)';
COMMENT ON COLUMN stock_sectors.stock_code    IS '종목코드';
COMMENT ON COLUMN stock_sectors.fics_sector   IS 'FICS 업종명 (예: 반도체 및 관련장비)';
COMMENT ON COLUMN stock_sectors.updated_at    IS '마지막 수집일시';
"""

UPSERT_SQL = """
INSERT INTO stock_sectors (stock_code, fics_sector, updated_at)
VALUES (%s, %s, NOW())
ON CONFLICT (stock_code)
DO UPDATE SET
    fics_sector = EXCLUDED.fics_sector,
    updated_at  = NOW();
"""


# ── DB ────────────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


# ── 크롤링 ────────────────────────────────────────────────────────────────────
def extract_fics_sector(soup: BeautifulSoup) -> str | None:
    """BeautifulSoup 객체에서 FICS 업종명 추출.

    FnGuide 페이지 텍스트에 'FICS 반도체 및 관련장비' 형태로 존재.
    """
    try:
        text = soup.get_text()
        match = re.search(r'FICS\s+([^|\n\r\t]+)', text)
        if match:
            sector = match.group(1).strip()
            sector = re.sub(r'\s+', ' ', sector)
            if 0 < len(sector) < 100:
                return sector
    except Exception:
        pass
    return None


def fetch_fics_sector(code: str) -> str | None:
    """FnGuide에서 종목코드의 FICS 업종명 크롤링 (재시도 포함)."""
    url = FNGUIDE_URL.format(code=code)

    for attempt in range(RETRY):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            return extract_fics_sector(soup)

        except requests.exceptions.RequestException as e:
            if attempt < RETRY - 1:
                wait = 2 * (attempt + 1)
                logger.debug(f"[{code}] 재시도 {attempt+2}/{RETRY} ({wait}초 후): {e}")
                time.sleep(wait)
            else:
                logger.warning(f"[{code}] 요청 실패 (3회 모두 실패): {e}")

        except Exception as e:
            logger.warning(f"[{code}] 파싱 오류: {e}")
            break

    return None


# ── 메인 ──────────────────────────────────────────────────────────────────────
def main(missing_only: bool = False) -> None:
    start_time = datetime.now()

    logger.info("=" * 60)
    logger.info("FnGuide FICS 업종 크롤링 시작")
    logger.info(f"  모드    : {'NULL 종목만 (--missing)' if missing_only else '전체'}")
    logger.info(f"  요청간격: {DELAY}초 / 재시도: {RETRY}회")
    logger.info("=" * 60)

    conn = get_conn()
    cur = conn.cursor()

    # 테이블 생성 (없으면)
    cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    logger.info("stock_sectors 테이블 준비 완료")

    # 수집 대상 종목 조회
    if missing_only:
        cur.execute("""
            SELECT s.stock_code, s.stock_name
            FROM stocks s
            LEFT JOIN stock_sectors ss ON s.stock_code = ss.stock_code
            WHERE s.is_active = TRUE
              AND s.market IN ('KOSPI', 'KOSDAQ')
              AND ss.stock_code IS NULL
            ORDER BY s.market, s.stock_code
        """)
    else:
        cur.execute("""
            SELECT stock_code, stock_name
            FROM stocks
            WHERE is_active = TRUE
              AND market IN ('KOSPI', 'KOSDAQ')
            ORDER BY market, stock_code
        """)

    stocks = cur.fetchall()
    total = len(stocks)

    if total == 0:
        logger.info("수집할 종목이 없습니다.")
        conn.close()
        return

    estimated_min = total * DELAY / 60
    logger.info(f"수집 대상: {total:,}개 종목")
    logger.info(f"예상 소요: 약 {estimated_min:.0f}분")
    logger.info("-" * 60)

    # 크롤링 루프
    cnt_ok   = 0   # 섹터 정상 수집
    cnt_null = 0   # 섹터 없음 (스팩/리츠 등)
    null_list: list[tuple[str, str]] = []
    recent: list[tuple[str, str, str | None]] = []  # 중간 샘플 체크용 (최근 5건)

    for i, (code, name) in enumerate(stocks, 1):
        sector = fetch_fics_sector(code)

        cur.execute(UPSERT_SQL, (code, sector))
        conn.commit()

        if sector:
            cnt_ok += 1
        else:
            cnt_null += 1
            null_list.append((code, name))

        recent = (recent + [(code, name, sector)])[-5:]

        # 진행 현황 로그
        if i % LOG_EVERY == 0 or i == total:
            elapsed = (datetime.now() - start_time).total_seconds()
            rate = i / elapsed if elapsed > 0 else 0
            remain = (total - i) / rate / 60 if rate > 0 else 0
            logger.info(
                f"[{i:>4}/{total}] "
                f"섹터OK: {cnt_ok} | NULL: {cnt_null} | "
                f"경과: {elapsed/60:.1f}분 | 잔여: {remain:.1f}분"
            )

            # 첫 번째 체크포인트: 실제 수집 데이터 샘플 확인
            if i == LOG_EVERY:
                logger.info("── 중간 샘플 체크 ──")
                for sc, sn, ss in recent:
                    logger.info(f"  {sc}  {sn[:12]:<12}  →  {ss if ss else 'NULL'}")
                garbled = sum(1 for _, _, s in recent if s and not re.search(r"[가-힣]", s))
                if garbled > 0:
                    logger.warning(
                        f"⚠️  비한글 섹터 {garbled}/{len(recent)}건 감지 — "
                        f"인코딩 문제 의심! 수집을 중단하고 확인하세요."
                    )

        if i < total:
            time.sleep(DELAY)

    conn.close()

    # 결과 요약
    elapsed_total = (datetime.now() - start_time).total_seconds()
    logger.info("=" * 60)
    logger.info("크롤링 완료")
    logger.info(f"  총 소요: {elapsed_total/60:.1f}분")
    logger.info(f"  섹터 확인: {cnt_ok:,}개")
    logger.info(f"  섹터 NULL: {cnt_null:,}개")

    if null_list:
        logger.info(f"\n섹터 미확인 종목 ({len(null_list)}개) — 스팩/리츠/신규상장 등:")
        for code, name in null_list[:30]:
            logger.info(f"  {code}  {name}")
        if len(null_list) > 30:
            logger.info(f"  ... 외 {len(null_list) - 30}개")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FnGuide FICS 업종 크롤링 → stock_sectors 테이블 저장"
    )
    parser.add_argument(
        "--missing",
        action="store_true",
        help="stock_sectors에 없는 종목만 수집 (신규 상장 후 사용)",
    )
    args = parser.parse_args()

    main(missing_only=args.missing)
