"""
연기금 데이터 제공 여부 확인
여러 종목에서 연기금 거래 데이터 검색
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import requests
from config.settings import settings
from utils.logger import logger

def test_pension_fund_data(token):
    """여러 종목에서 연기금 데이터 검색"""

    session = requests.Session()
    session.verify = False

    api_url = 'https://infomaxy.einfomax.co.kr/api/stock/investor'

    # 시가총액 상위 종목 + 거래량 많은 종목
    test_stocks = [
        ("005930", "삼성전자"),
        ("000660", "SK하이닉스"),
        ("373220", "LG에너지솔루션"),
        ("207940", "삼성바이오로직스"),
        ("005380", "현대차"),
        ("005490", "POSCO홀딩스"),
        ("035420", "NAVER"),
        ("051910", "LG화학"),
        ("006400", "삼성SDI"),
        ("105560", "KB금융"),
        ("035720", "카카오"),
        ("012330", "현대모비스"),
        ("028260", "삼성물산"),
        ("068270", "셀트리온"),
        ("055550", "신한지주"),
    ]

    params = {
        "startDate": "20260201",
        "endDate": "20260218"
    }
    headers = {"Authorization": f'bearer {token}'}

    logger.info("="*80)
    logger.info("🔍 연기금 데이터 제공 여부 확인")
    logger.info("="*80)
    logger.info(f"기간: 2026-02-01 ~ 2026-02-18")
    logger.info(f"테스트 종목: {len(test_stocks)}개")

    # 1. "연기금" 테스트
    logger.info("\n" + "="*80)
    logger.info("📊 Step 1: '연기금' 파라미터 테스트")
    logger.info("="*80)

    params["investor"] = "연기금"
    found_stocks = []

    for stock_code, stock_name in test_stocks:
        params["code"] = stock_code

        try:
            r = session.get(api_url, params=params, headers=headers, timeout=30)

            if r.status_code == 200:
                data = r.json()

                if data.get('success'):
                    results = data.get('results', [])
                    count = len(results)

                    if count > 0:
                        found_stocks.append((stock_code, stock_name, count, results))
                        logger.success(f"✅ {stock_code} {stock_name:<15} {count:>3}건 발견!")

                        # 샘플 출력
                        sample = results[0]
                        net_value = sample.get('bid_value', 0) - sample.get('ask_value', 0)
                        logger.info(f"   └─ {sample.get('date')} | 순매수: {net_value:,}천원")
                    else:
                        logger.info(f"⚠️  {stock_code} {stock_name:<15} 0건")
        except Exception as e:
            logger.error(f"❌ {stock_code} {stock_name:<15} 오류: {e}")

    if found_stocks:
        logger.info("\n" + "="*80)
        logger.info("🎉 '연기금' 데이터 발견!")
        logger.info("="*80)

        for stock_code, stock_name, count, results in found_stocks:
            logger.info(f"\n종목: {stock_code} {stock_name}")
            logger.info(f"데이터: {count}건")
            logger.info(f"\n최근 3일 데이터:")

            for i, row in enumerate(results[:3], 1):
                date = row.get('date')
                bid_value = row.get('bid_value', 0)
                ask_value = row.get('ask_value', 0)
                net_value = bid_value - ask_value

                logger.info(f"  {i}. {date} | 매수: {bid_value:,}천원 | 매도: {ask_value:,}천원 | 순매수: {net_value:,}천원")

        return True, "연기금"

    # 2. "연기금 등" 테스트
    logger.info("\n" + "="*80)
    logger.info("📊 Step 2: '연기금 등' 파라미터 테스트")
    logger.info("="*80)

    params["investor"] = "연기금 등"
    found_stocks_etc = []

    for stock_code, stock_name in test_stocks:
        params["code"] = stock_code

        try:
            r = session.get(api_url, params=params, headers=headers, timeout=30)

            if r.status_code == 200:
                data = r.json()

                if data.get('success'):
                    results = data.get('results', [])
                    count = len(results)

                    if count > 0:
                        found_stocks_etc.append((stock_code, stock_name, count, results))
                        logger.success(f"✅ {stock_code} {stock_name:<15} {count:>3}건 발견!")

                        # 샘플 출력
                        sample = results[0]
                        net_value = sample.get('bid_value', 0) - sample.get('ask_value', 0)
                        logger.info(f"   └─ {sample.get('date')} | 순매수: {net_value:,}천원")
                    else:
                        logger.info(f"⚠️  {stock_code} {stock_name:<15} 0건")
        except Exception as e:
            logger.error(f"❌ {stock_code} {stock_name:<15} 오류: {e}")

    if found_stocks_etc:
        logger.info("\n" + "="*80)
        logger.info("🎉 '연기금 등' 데이터 발견!")
        logger.info("="*80)

        for stock_code, stock_name, count, results in found_stocks_etc:
            logger.info(f"\n종목: {stock_code} {stock_name}")
            logger.info(f"데이터: {count}건")
            logger.info(f"\n최근 3일 데이터:")

            for i, row in enumerate(results[:3], 1):
                date = row.get('date')
                bid_value = row.get('bid_value', 0)
                ask_value = row.get('ask_value', 0)
                net_value = bid_value - ask_value

                logger.info(f"  {i}. {date} | 매수: {bid_value:,}천원 | 매도: {ask_value:,}천원 | 순매수: {net_value:,}천원")

        return True, "연기금 등"

    # 둘 다 없음
    logger.warning("\n" + "="*80)
    logger.warning("❌ 연기금 데이터 없음")
    logger.warning("="*80)
    logger.warning(f"테스트한 {len(test_stocks)}개 종목 모두에서 연기금 데이터가 없습니다.")
    logger.warning("API에서 연기금 데이터를 제공하지 않거나, 이 기간에 거래가 없는 것으로 보입니다.")

    return False, None

if __name__ == "__main__":
    token = settings.INFOMAX_API_KEY

    if not token:
        logger.error("❌ API 토큰이 없습니다!")
        sys.exit(1)

    found, investor_type = test_pension_fund_data(token)

    if found:
        logger.success(f"\n✅ 결론: API는 '{investor_type}' 파라미터로 연기금 데이터를 제공합니다!")
    else:
        logger.error("\n❌ 결론: 2월 데이터에서 연기금 거래를 찾을 수 없습니다.")
