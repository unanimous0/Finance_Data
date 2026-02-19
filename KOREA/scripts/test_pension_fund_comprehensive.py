"""
연기금 데이터 전수 조사
DB에 있는 전체 종목 중 연기금 데이터가 있는지 확인
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import requests
import psycopg2
from config.settings import settings
from utils.logger import logger

def get_all_stocks():
    """DB에서 전체 종목 코드 가져오기"""
    try:
        conn = psycopg2.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            dbname=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD
        )
        cursor = conn.cursor()

        # 전체 종목 조회
        cursor.execute("SELECT stock_code, stock_name FROM stocks ORDER BY stock_code")
        stocks = cursor.fetchall()

        cursor.close()
        conn.close()

        return stocks
    except Exception as e:
        logger.error(f"DB 조회 실패: {e}")
        return []

def test_pension_comprehensive(token):
    """전체 종목 연기금 데이터 조사"""

    session = requests.Session()
    session.verify = False

    api_url = 'https://infomaxy.einfomax.co.kr/api/stock/investor'

    # DB에서 전체 종목 가져오기
    stocks = get_all_stocks()

    if not stocks:
        logger.error("종목 목록을 가져올 수 없습니다.")
        return

    logger.info("="*80)
    logger.info("🔍 연기금 데이터 전수 조사")
    logger.info("="*80)
    logger.info(f"전체 종목 수: {len(stocks):,}개")
    logger.info(f"기간: 2026-02-01 ~ 2026-02-18")
    logger.info(f"조사 대상: '연기금' 및 '연기금 등' 파라미터")

    params = {
        "startDate": "20260201",
        "endDate": "20260218"
    }
    headers = {"Authorization": f'bearer {token}'}

    # 진행 상황 표시
    total = len(stocks)
    found_pension = []
    found_pension_etc = []

    # 1. "연기금" 테스트
    logger.info("\n" + "="*80)
    logger.info("📊 Step 1: '연기금' 파라미터 전수 조사")
    logger.info("="*80)

    params["investor"] = "연기금"

    for idx, (stock_code, stock_name) in enumerate(stocks, 1):
        params["code"] = stock_code

        # 진행률 출력 (매 100개마다)
        if idx % 100 == 0:
            logger.info(f"진행중... {idx:,}/{total:,} ({idx/total*100:.1f}%)")

        try:
            r = session.get(api_url, params=params, headers=headers, timeout=10)

            if r.status_code == 200:
                data = r.json()

                if data.get('success'):
                    results = data.get('results', [])

                    if len(results) > 0:
                        found_pension.append((stock_code, stock_name, len(results), results))
                        logger.success(f"✅ 발견! {stock_code} {stock_name} - {len(results)}건")

                        # 샘플 출력
                        sample = results[0]
                        net_value = sample.get('bid_value', 0) - sample.get('ask_value', 0)
                        logger.info(f"   └─ {sample.get('date')} | 순매수: {net_value:,}천원")

        except Exception as e:
            if idx % 100 == 0:
                logger.warning(f"  오류 발생: {stock_code} - {e}")
            continue

    logger.info(f"\n'연기금' 조사 완료: {total:,}개 종목 중 {len(found_pension)}개 발견")

    # 발견되면 여기서 종료
    if found_pension:
        logger.info("\n" + "="*80)
        logger.success("🎉 '연기금' 데이터 발견!")
        logger.info("="*80)

        for stock_code, stock_name, count, results in found_pension:
            logger.info(f"\n종목: {stock_code} {stock_name}")
            logger.info(f"데이터: {count}건")
            logger.info(f"\n최근 3일:")

            for i, row in enumerate(results[:3], 1):
                date = row.get('date')
                bid_value = row.get('bid_value', 0)
                ask_value = row.get('ask_value', 0)
                net_value = bid_value - ask_value

                logger.info(f"  {i}. {date} | 매수: {bid_value:,} | 매도: {ask_value:,} | 순매수: {net_value:,}천원")

        return True, "연기금", found_pension

    # 2. "연기금 등" 테스트
    logger.info("\n" + "="*80)
    logger.info("📊 Step 2: '연기금 등' 파라미터 전수 조사")
    logger.info("="*80)

    params["investor"] = "연기금 등"

    for idx, (stock_code, stock_name) in enumerate(stocks, 1):
        params["code"] = stock_code

        # 진행률 출력 (매 100개마다)
        if idx % 100 == 0:
            logger.info(f"진행중... {idx:,}/{total:,} ({idx/total*100:.1f}%)")

        try:
            r = session.get(api_url, params=params, headers=headers, timeout=10)

            if r.status_code == 200:
                data = r.json()

                if data.get('success'):
                    results = data.get('results', [])

                    if len(results) > 0:
                        found_pension_etc.append((stock_code, stock_name, len(results), results))
                        logger.success(f"✅ 발견! {stock_code} {stock_name} - {len(results)}건")

                        # 샘플 출력
                        sample = results[0]
                        net_value = sample.get('bid_value', 0) - sample.get('ask_value', 0)
                        logger.info(f"   └─ {sample.get('date')} | 순매수: {net_value:,}천원")

        except Exception as e:
            if idx % 100 == 0:
                logger.warning(f"  오류 발생: {stock_code} - {e}")
            continue

    logger.info(f"\n'연기금 등' 조사 완료: {total:,}개 종목 중 {len(found_pension_etc)}개 발견")

    # 결과 출력
    if found_pension_etc:
        logger.info("\n" + "="*80)
        logger.success("🎉 '연기금 등' 데이터 발견!")
        logger.info("="*80)

        for stock_code, stock_name, count, results in found_pension_etc:
            logger.info(f"\n종목: {stock_code} {stock_name}")
            logger.info(f"데이터: {count}건")
            logger.info(f"\n최근 3일:")

            for i, row in enumerate(results[:3], 1):
                date = row.get('date')
                bid_value = row.get('bid_value', 0)
                ask_value = row.get('ask_value', 0)
                net_value = bid_value - ask_value

                logger.info(f"  {i}. {date} | 매수: {bid_value:,} | 매도: {ask_value:,} | 순매수: {net_value:,}천원")

        return True, "연기금 등", found_pension_etc

    # 둘 다 없음
    logger.warning("\n" + "="*80)
    logger.warning("❌ 연기금 데이터 없음")
    logger.warning("="*80)
    logger.warning(f"전체 {total:,}개 종목을 조사했으나 연기금 데이터를 찾을 수 없습니다.")

    return False, None, []

if __name__ == "__main__":
    token = settings.INFOMAX_API_KEY

    if not token:
        logger.error("❌ API 토큰이 없습니다!")
        sys.exit(1)

    found, investor_type, stocks = test_pension_comprehensive(token)

    if found:
        logger.success(f"\n✅ 최종 결론: '{investor_type}' 파라미터로 연기금 데이터 제공됨!")
        logger.info(f"발견된 종목 수: {len(stocks)}개")
    else:
        logger.error("\n❌ 최종 결론: API가 연기금 데이터를 제공하지 않거나, 2월에 거래 기록이 없습니다.")
