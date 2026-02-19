"""
인포맥스 API 테스트 및 2026년 2월 데이터 수집
영업일만 수집 (주말 제외)
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta
import json

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import requests
from config.settings import settings
from utils.logger import logger

def is_business_day(date):
    """영업일 판별 (주말 제외, 공휴일은 추후 추가)"""
    # 토요일(5), 일요일(6) 제외
    return date.weekday() < 5

def get_business_days(start_date, end_date):
    """기간 내 영업일 목록 반환"""
    business_days = []
    current = start_date

    while current <= end_date:
        if is_business_day(current):
            business_days.append(current)
        current += timedelta(days=1)

    return business_days

def test_api_connection(token):
    """API 연결 테스트"""
    logger.info("🔍 API 연결 테스트 중...")

    session = requests.Session()
    session.verify = False  # SSL 인증 무효화

    api_url = 'https://infomaxy.einfomax.co.kr/api/stock/hist'
    params = {
        "code": "005930",  # 삼성전자
        "startDate": "20260203",  # 2월 3일 (월)
        "endDate": "20260203"
    }
    headers = {"Authorization": f'bearer {token}'}

    try:
        r = session.get(api_url, params=params, headers=headers, timeout=10)

        logger.info(f"  응답 코드: {r.status_code}")

        if r.status_code == 200:
            data = r.json()
            logger.info(f"  응답 성공: {data.get('success')}")

            if data.get('success'):
                logger.success("✅ API 연결 성공!")

                # 샘플 데이터 출력
                if data.get('results'):
                    logger.info(f"\n📊 샘플 데이터 (1건):")
                    sample = data['results'][0]
                    for key, value in sample.items():
                        logger.info(f"  {key}: {value}")
                    return True
            else:
                logger.error(f"❌ API 오류: {data.get('message')}")
                return False
        else:
            logger.error(f"❌ HTTP 오류: {r.status_code}")
            logger.error(f"  응답: {r.text}")
            return False

    except Exception as e:
        logger.error(f"❌ 연결 실패: {e}")
        return False

def fetch_daily_ohlcv(token, code, start_date, end_date):
    """일봉 OHLCV 데이터 조회"""
    session = requests.Session()
    session.verify = False

    api_url = 'https://infomaxy.einfomax.co.kr/api/stock/hist'
    params = {
        "code": code,
        "startDate": start_date.strftime("%Y%m%d"),
        "endDate": end_date.strftime("%Y%m%d")
    }
    headers = {"Authorization": f'bearer {token}'}

    try:
        r = session.get(api_url, params=params, headers=headers, timeout=30)

        if r.status_code == 200:
            data = r.json()
            if data.get('success'):
                return data.get('results', [])

        return None

    except Exception as e:
        logger.error(f"  조회 실패: {e}")
        return None

def collect_february_data(token):
    """2026년 2월 데이터 수집"""
    logger.info("\n" + "="*80)
    logger.info("📅 2026년 2월 영업일 데이터 수집")
    logger.info("="*80)

    # 기간 설정
    start_date = datetime(2026, 2, 1)
    end_date = datetime(2026, 2, 18)  # 어제까지 (오늘은 장 마감 후)

    # 영업일 목록
    business_days = get_business_days(start_date, end_date)
    logger.info(f"\n영업일 수: {len(business_days)}일")
    logger.info(f"기간: {start_date.date()} ~ {end_date.date()}")
    logger.info(f"\n영업일 목록:")
    for day in business_days:
        logger.info(f"  {day.strftime('%Y-%m-%d (%a)')}")

    # 테스트 종목
    test_stocks = ["005930"]  # 삼성전자

    logger.info(f"\n테스트 종목: {', '.join(test_stocks)}")
    logger.info("\n" + "="*80)

    # 데이터 수집
    all_data = []

    for stock_code in test_stocks:
        logger.info(f"\n📊 {stock_code} 데이터 수집 중...")

        # API 호출 (전체 기간 한 번에)
        results = fetch_daily_ohlcv(token, stock_code, start_date, end_date)

        if results:
            logger.success(f"  ✅ {len(results)}건 조회 성공")

            # 샘플 출력 (처음 3개)
            logger.info(f"\n  샘플 데이터 (처음 3개):")
            for i, row in enumerate(results[:3], 1):
                logger.info(f"    {i}. {row.get('date')} - 종가: {row.get('close_price'):,}원, 거래량: {row.get('trading_volume'):,}주")

            all_data.extend(results)
        else:
            logger.error(f"  ❌ 조회 실패")

    logger.info("\n" + "="*80)
    logger.info(f"📊 총 수집 데이터: {len(all_data)}건")
    logger.info("="*80)

    return all_data

def main():
    """메인 함수"""
    logger.info("="*80)
    logger.info("🚀 인포맥스 API 테스트")
    logger.info("="*80)

    # API 토큰 확인
    token = settings.INFOMAX_API_KEY

    if not token:
        logger.error("\n❌ API 토큰이 설정되지 않았습니다!")
        logger.info("\n.env 파일에 다음을 추가하세요:")
        logger.info("  INFOMAX_API_KEY=your_token_here")
        logger.info("\n토큰 발급:")
        logger.info("  - 인포맥스 단말기 9000번 화면")
        logger.info("  - 또는 api_infomax@yna.co.kr 문의")
        return False

    logger.info(f"\n✅ API 토큰: {token[:10]}... (총 {len(token)}자)")

    # 1. API 연결 테스트
    if not test_api_connection(token):
        logger.error("\n❌ API 연결 테스트 실패")
        return False

    # 2. 2월 데이터 수집
    data = collect_february_data(token)

    if data:
        logger.success(f"\n✅ 테스트 완료! {len(data)}건 수집 성공")
        return True
    else:
        logger.error("\n❌ 데이터 수집 실패")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
