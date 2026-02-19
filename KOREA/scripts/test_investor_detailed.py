"""
투자자별 수급 API 상세 테스트
전체 투자자 타입 확인
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import requests
from config.settings import settings
from utils.logger import logger

def test_all_investors(token):
    """전체 투자자 타입 테스트"""

    session = requests.Session()
    session.verify = False

    api_url = 'https://infomaxy.einfomax.co.kr/api/stock/investor'

    # PDF에서 확인한 전체 투자자 타입
    investor_types = [
        "기관계",
        "금융투자",
        "보험",
        "투신",
        "사모",
        "은행",
        "종금저축",  # 종금저축은행
        "연기금 등",  # "연기금" 대신 "연기금 등"으로 테스트
        "정부",
        "기타법인",
        "개인",
        "외국인합",
        "외국인",
        "기타외국인"
    ]

    params = {
        "code": "005930",
        "startDate": "20260201",
        "endDate": "20260218"
    }
    headers = {"Authorization": f'bearer {token}'}

    logger.info("="*80)
    logger.info("📊 전체 투자자 타입 테스트 (삼성전자)")
    logger.info("="*80)

    results = []

    for investor in investor_types:
        params["investor"] = investor

        try:
            r = session.get(api_url, params=params, headers=headers, timeout=30)

            if r.status_code == 200:
                data = r.json()

                if data.get('success'):
                    count = len(data.get('results', []))
                    status = "✅" if count > 0 else "⚠️ "

                    results.append({
                        'investor': investor,
                        'count': count,
                        'success': True
                    })

                    logger.info(f"{status} {investor:<15} {count:>3}건")

                    # 샘플 데이터 (첫 번째만)
                    if count > 0:
                        sample = data['results'][0]
                        net_value = sample.get('bid_value', 0) - sample.get('ask_value', 0)
                        logger.info(f"   └─ 샘플: {sample.get('date')} | 순매수: {net_value:,}천원")
                else:
                    logger.error(f"❌ {investor:<15} API 오류: {data.get('message')}")
                    results.append({
                        'investor': investor,
                        'count': 0,
                        'success': False,
                        'error': data.get('message')
                    })
            else:
                logger.error(f"❌ {investor:<15} HTTP {r.status_code}")
                results.append({
                    'investor': investor,
                    'count': 0,
                    'success': False
                })

        except Exception as e:
            logger.error(f"❌ {investor:<15} 오류: {e}")
            results.append({
                'investor': investor,
                'count': 0,
                'success': False
            })

    # 요약
    logger.info("\n" + "="*80)
    logger.info("📊 요약")
    logger.info("="*80)

    has_data = [r for r in results if r['count'] > 0]
    no_data = [r for r in results if r['success'] and r['count'] == 0]

    logger.info(f"\n✅ 데이터 있음: {len(has_data)}개 타입")
    for r in has_data:
        logger.info(f"  - {r['investor']}: {r['count']:,}건")

    logger.info(f"\n⚠️  데이터 없음: {len(no_data)}개 타입")
    for r in no_data:
        logger.info(f"  - {r['investor']}")

    return results

if __name__ == "__main__":
    token = settings.INFOMAX_API_KEY

    if not token:
        logger.error("❌ API 토큰이 없습니다!")
        sys.exit(1)

    results = test_all_investors(token)

    success_count = sum(1 for r in results if r['success'])
    logger.info(f"\n✅ 테스트 완료: {success_count}/{len(results)} 성공")
