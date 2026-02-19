"""
인포맥스 투자자별 수급 API 테스트
"""
import sys
from pathlib import Path
from datetime import datetime

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import requests
from config.settings import settings
from utils.logger import logger

def test_investor_api(token, code="005930", start_date="20260201", end_date="20260218"):
    """투자자별 수급 API 테스트"""

    logger.info("="*80)
    logger.info("🔍 투자자별 수급 API 테스트")
    logger.info("="*80)

    session = requests.Session()
    session.verify = False

    api_url = 'https://infomaxy.einfomax.co.kr/api/stock/investor'

    # 투자자 구분 코드 테스트
    # PDF에서: 기관계, 금융투자, 보험, 투신, 사모, 은행, 종금저축은행, 기타금융, 연기금, 기타법인, 개인, 외국인, 기타외국인

    test_cases = [
        ("", "전체 (미입력)"),
        ("외국인", "외국인"),
        ("기관계", "기관계"),
        ("개인", "개인"),
        ("연기금", "연기금"),
        ("금융투자", "금융투자"),
        ("보험", "보험"),
        ("투신", "투신"),
    ]

    logger.info(f"\n종목: {code} (삼성전자)")
    logger.info(f"기간: {start_date} ~ {end_date}\n")

    results_summary = []

    for investor_code, investor_name in test_cases:
        logger.info(f"\n{'='*80}")
        logger.info(f"📊 투자자 구분: {investor_name} (코드: '{investor_code}')")
        logger.info("="*80)

        params = {
            "code": code,
            "investor": investor_code,
            "startDate": start_date,
            "endDate": end_date
        }
        headers = {"Authorization": f'bearer {token}'}

        try:
            r = session.get(api_url, params=params, headers=headers, timeout=30)

            if r.status_code == 200:
                data = r.json()

                if data.get('success'):
                    results = data.get('results', [])
                    logger.success(f"✅ 조회 성공: {len(results)}건")

                    # 샘플 데이터 (최근 3건)
                    if results:
                        logger.info(f"\n샘플 데이터 (최근 3건):")
                        for i, row in enumerate(results[:3], 1):
                            date = row.get('date')
                            investor = row.get('investor')
                            bid_value = row.get('bid_value', 0)
                            ask_value = row.get('ask_value', 0)
                            net_value = bid_value - ask_value

                            logger.info(f"  {i}. {date} | {investor} | 순매수: {net_value:,}천원")

                            # 첫 번째 케이스는 전체 필드 출력
                            if investor_code == "" and i == 1:
                                logger.info(f"\n     📋 전체 필드:")
                                for key, value in row.items():
                                    logger.info(f"       {key}: {value}")

                        results_summary.append({
                            'investor_name': investor_name,
                            'investor_code': investor_code,
                            'count': len(results),
                            'success': True
                        })
                    else:
                        logger.warning("⚠️  데이터 없음")
                        results_summary.append({
                            'investor_name': investor_name,
                            'investor_code': investor_code,
                            'count': 0,
                            'success': True
                        })
                else:
                    logger.error(f"❌ API 오류: {data.get('message')}")
                    results_summary.append({
                        'investor_name': investor_name,
                        'investor_code': investor_code,
                        'count': 0,
                        'success': False,
                        'error': data.get('message')
                    })
            else:
                logger.error(f"❌ HTTP 오류: {r.status_code}")
                results_summary.append({
                    'investor_name': investor_name,
                    'investor_code': investor_code,
                    'count': 0,
                    'success': False,
                    'error': f"HTTP {r.status_code}"
                })

        except Exception as e:
            logger.error(f"❌ 요청 실패: {e}")
            results_summary.append({
                'investor_name': investor_name,
                'investor_code': investor_code,
                'count': 0,
                'success': False,
                'error': str(e)
            })

    # 요약
    logger.info("\n" + "="*80)
    logger.info("📊 테스트 결과 요약")
    logger.info("="*80)

    logger.info(f"\n{'투자자 구분':<15} {'코드':<10} {'건수':<10} {'상태':<10}")
    logger.info("-"*80)

    for result in results_summary:
        status = "✅ 성공" if result['success'] else "❌ 실패"
        count = f"{result['count']:,}건" if result['success'] else "-"
        logger.info(f"{result['investor_name']:<15} {result['investor_code']:<10} {count:<10} {status}")

    logger.info("\n" + "="*80)
    logger.info("💡 DB 매핑 제안")
    logger.info("="*80)

    mapping = [
        ("외국인", "FOREIGN", "외국인 전체"),
        ("기관계", "INSTITUTION", "기관 전체 (연기금 포함)"),
        ("연기금", "PENSION", "연기금만 별도"),
        ("개인", "RETAIL", "개인"),
    ]

    logger.info(f"\n{'API 코드':<15} {'DB 값':<15} {'설명':<30}")
    logger.info("-"*80)
    for api_code, db_value, desc in mapping:
        logger.info(f"{api_code:<15} {db_value:<15} {desc:<30}")

    logger.info("\n⚠️  주의:")
    logger.info("  - 기관계: 연기금 포함")
    logger.info("  - DB에 저장시: 기관계 - 연기금 = 순수 기관")

    return results_summary

def main():
    """메인 함수"""
    token = settings.INFOMAX_API_KEY

    if not token:
        logger.error("❌ API 토큰이 설정되지 않았습니다!")
        return False

    logger.info(f"✅ API 토큰: {token[:10]}... (총 {len(token)}자)\n")

    # 테스트 실행
    results = test_investor_api(token)

    # 성공 여부
    success_count = sum(1 for r in results if r['success'])
    total_count = len(results)

    logger.info("\n" + "="*80)
    if success_count == total_count:
        logger.success(f"✅ 모든 테스트 성공! ({success_count}/{total_count})")
        return True
    else:
        logger.warning(f"⚠️  일부 테스트 실패 ({success_count}/{total_count})")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
