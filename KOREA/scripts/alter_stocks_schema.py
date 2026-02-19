"""
stocks 테이블 스키마 수정 스크립트
"""
import sys
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from database.connection import engine
from utils.logger import logger

def alter_stocks_table():
    """stocks 테이블 스키마 수정"""

    try:
        with engine.connect() as conn:
            logger.info("stocks 테이블 스키마 수정 시작...")

            # 1. standard_code 컬럼 추가
            logger.info("1. standard_code 컬럼 추가 중...")
            try:
                conn.execute(text("""
                    ALTER TABLE stocks
                    ADD COLUMN standard_code VARCHAR(12) UNIQUE
                """))
                conn.commit()
                logger.success("✅ standard_code 컬럼 추가 완료")
            except Exception as e:
                if "already exists" in str(e).lower():
                    logger.warning("⚠️  standard_code 컬럼이 이미 존재합니다")
                else:
                    raise

            # 2. market 컬럼 NULL 허용으로 변경
            logger.info("2. market 컬럼 NULL 허용으로 변경 중...")
            try:
                conn.execute(text("""
                    ALTER TABLE stocks
                    ALTER COLUMN market DROP NOT NULL
                """))
                conn.commit()
                logger.success("✅ market 컬럼 NULL 허용 완료")
            except Exception as e:
                if "does not exist" in str(e).lower():
                    logger.warning("⚠️  market 컬럼이 이미 NULL 허용 상태입니다")
                else:
                    raise

            # 3. 수정 결과 확인
            logger.info("\n3. 수정 결과 확인 중...")
            result = conn.execute(text("""
                SELECT
                    column_name,
                    data_type,
                    character_maximum_length,
                    is_nullable
                FROM information_schema.columns
                WHERE table_name = 'stocks'
                ORDER BY ordinal_position
            """))

            logger.info("\n" + "=" * 80)
            logger.info("📋 stocks 테이블 스키마 (수정 후)")
            logger.info("=" * 80)
            for row in result:
                nullable = "NULL" if row.is_nullable == "YES" else "NOT NULL"
                length = f"({row.character_maximum_length})" if row.character_maximum_length else ""
                logger.info(f"  {row.column_name:<20} {row.data_type}{length:<15} {nullable}")
            logger.info("=" * 80)

            logger.success("\n✅ stocks 테이블 스키마 수정 완료!")

    except Exception as e:
        logger.error(f"❌ 스키마 수정 실패: {e}")
        raise

if __name__ == "__main__":
    alter_stocks_table()
