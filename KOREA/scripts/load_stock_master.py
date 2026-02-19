"""
종목 마스터 데이터 적재 스크립트
raw_data/1-종목코드_종목명.xlsx (3개 시트: KOSPI, KOSDAQ, ETF) → stocks 테이블
"""
import sys
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
from sqlalchemy import text
from database.connection import engine
from utils.logger import logger

def load_stock_master():
    """종목 마스터 데이터를 DB에 적재 (3개 시트: KOSPI, KOSDAQ, ETF)"""

    # 1. Excel 파일 읽기
    file_path = project_root / "raw_data" / "1-종목코드_종목명.xlsx"

    logger.info(f"📂 파일 읽기: {file_path}")

    if not file_path.exists():
        logger.error(f"❌ 파일을 찾을 수 없습니다: {file_path}")
        return False

    # 2. 3개 시트 읽기
    logger.info("📊 3개 시트 읽기 중...")

    sheets_data = []

    for market in ['KOSPI', 'KOSDAQ', 'ETF']:
        df = pd.read_excel(file_path, sheet_name=market)
        logger.info(f"  - {market}: {len(df):,}개")

        # 컬럼명 매핑 + market 컬럼 추가
        df_mapped = pd.DataFrame({
            'stock_code': df['코드'].astype(str).str.strip(),
            'stock_name': df['종목명'].astype(str).str.strip(),
            'standard_code': df['국제표준코드'].astype(str).str.strip(),
            'market': market
        })

        sheets_data.append(df_mapped)

    # 3개 시트 합치기
    df_mapped = pd.concat(sheets_data, ignore_index=True)
    logger.info(f"✅ 전체 데이터: {len(df_mapped):,}개 레코드")

    # 3. 데이터 변환 확인
    logger.info("\n🔄 데이터 검증 중...")

    # NULL 값 확인
    logger.info(f"  - stock_code NULL: {df_mapped['stock_code'].isnull().sum()}개")
    logger.info(f"  - stock_name NULL: {df_mapped['stock_name'].isnull().sum()}개")
    logger.info(f"  - standard_code NULL: {df_mapped['standard_code'].isnull().sum()}개")
    logger.info(f"  - market NULL: {df_mapped['market'].isnull().sum()}개")

    # market별 개수
    logger.info("\n  market별 분포:")
    for market, count in df_mapped['market'].value_counts().items():
        logger.info(f"    {market}: {count:,}개")

    # 3. DB에 적재
    logger.info("💾 데이터베이스 적재 중...")

    try:
        with engine.connect() as conn:
            # 기존 데이터 개수 확인
            result = conn.execute(text("SELECT COUNT(*) FROM stocks"))
            before_count = result.fetchone()[0]
            logger.info(f"  기존 레코드 수: {before_count:,}개")

            # 데이터 적재 (pandas to_sql 사용)
            df_mapped.to_sql(
                'stocks',
                conn,
                if_exists='append',  # 추가 모드
                index=False,
                method='multi',
                chunksize=500
            )

            conn.commit()

            # 적재 후 개수 확인
            result = conn.execute(text("SELECT COUNT(*) FROM stocks"))
            after_count = result.fetchone()[0]
            inserted_count = after_count - before_count

            logger.success(f"✅ 데이터 적재 완료!")
            logger.info(f"  삽입된 레코드: {inserted_count:,}개")
            logger.info(f"  전체 레코드 수: {after_count:,}개")

            # 샘플 데이터 조회
            logger.info("\n📋 샘플 데이터 (처음 5개):")
            result = conn.execute(text("""
                SELECT stock_code, stock_name, standard_code, market
                FROM stocks
                ORDER BY stock_code
                LIMIT 5
            """))

            for row in result:
                logger.info(f"  {row.stock_code} | {row.stock_name} | {row.standard_code} | {row.market or 'NULL'}")

            return True

    except Exception as e:
        logger.error(f"❌ 데이터 적재 실패: {e}")

        # 중복 키 에러인 경우
        if "duplicate key" in str(e).lower() or "unique constraint" in str(e).lower():
            logger.warning("⚠️  중복 데이터가 있습니다. 기존 데이터를 삭제하고 다시 시도하시겠습니까?")
            logger.info("  삭제 명령: psql -U postgres -d korea_stock_data -c 'TRUNCATE stocks CASCADE;'")

        return False

def show_statistics():
    """적재된 데이터 통계 표시"""

    logger.info("\n" + "=" * 80)
    logger.info("📊 데이터 통계")
    logger.info("=" * 80)

    try:
        with engine.connect() as conn:
            # 전체 개수
            result = conn.execute(text("SELECT COUNT(*) FROM stocks"))
            total_count = result.fetchone()[0]
            logger.info(f"전체 종목 수: {total_count:,}개")

            # market별 개수 (NULL 포함)
            logger.info("\nmarket별 분포:")
            result = conn.execute(text("""
                SELECT
                    COALESCE(market, 'NULL') as market,
                    COUNT(*) as count
                FROM stocks
                GROUP BY market
                ORDER BY count DESC
            """))
            for row in result:
                logger.info(f"  {row.market}: {row.count:,}개")

            # standard_code NULL 개수
            result = conn.execute(text("""
                SELECT COUNT(*) FROM stocks WHERE standard_code IS NULL
            """))
            null_count = result.fetchone()[0]
            logger.info(f"\nstandard_code NULL: {null_count:,}개")

    except Exception as e:
        logger.error(f"❌ 통계 조회 실패: {e}")

if __name__ == "__main__":
    logger.info("=" * 80)
    logger.info("🚀 종목 마스터 데이터 적재 시작")
    logger.info("=" * 80)

    success = load_stock_master()

    if success:
        show_statistics()
        logger.success("\n✅ 모든 작업 완료!")
    else:
        logger.error("\n❌ 작업 실패")
        sys.exit(1)
