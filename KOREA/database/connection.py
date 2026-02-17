"""
데이터베이스 연결 관리 모듈

SQLAlchemy를 사용한 PostgreSQL 연결 풀 및 세션 관리
"""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool

from config.settings import settings


# SQLAlchemy 엔진 생성 (연결 풀 포함)
engine = create_engine(
    settings.database_url,
    poolclass=QueuePool,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,  # 연결 유효성 자동 확인
    echo=settings.is_development,  # 개발 환경에서만 SQL 로그 출력
)

# 세션 팩토리
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    데이터베이스 세션을 생성하고 관리하는 컨텍스트 매니저

    사용 예시:
        with get_session() as session:
            result = session.execute(text("SELECT * FROM stocks"))
            ...

    Yields:
        Session: SQLAlchemy 세션 객체
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        raise
    finally:
        session.close()


def test_connection() -> bool:
    """
    데이터베이스 연결 테스트

    Returns:
        bool: 연결 성공 여부
    """
    try:
        with get_session() as session:
            result = session.execute(text("SELECT version();"))
            version = result.fetchone()[0]
            print(f"✅ PostgreSQL 연결 성공!")
            print(f"   버전: {version}")

            # TimescaleDB 확장 확인
            result = session.execute(
                text(
                    "SELECT extname, extversion FROM pg_extension WHERE extname = 'timescaledb';"
                )
            )
            ts_info = result.fetchone()
            if ts_info:
                print(f"✅ TimescaleDB 확장 확인!")
                print(f"   버전: {ts_info[1]}")
            else:
                print("⚠️  TimescaleDB 확장이 설치되지 않았습니다.")

            # Hypertable 목록 확인
            result = session.execute(
                text(
                    "SELECT hypertable_name FROM timescaledb_information.hypertables;"
                )
            )
            hypertables = result.fetchall()
            if hypertables:
                print(f"✅ Hypertable 개수: {len(hypertables)}")
                for ht in hypertables:
                    print(f"   - {ht[0]}")
            else:
                print("ℹ️  생성된 Hypertable이 없습니다.")

            return True
    except Exception as e:
        print(f"❌ 데이터베이스 연결 실패: {e}")
        return False


def get_table_count(table_name: str) -> int:
    """
    특정 테이블의 레코드 수 조회

    Args:
        table_name: 테이블 이름

    Returns:
        int: 레코드 수
    """
    with get_session() as session:
        result = session.execute(text(f"SELECT COUNT(*) FROM {table_name};"))
        return result.fetchone()[0]


if __name__ == "__main__":
    # 연결 테스트
    test_connection()
