"""
pytest 설정 및 공통 fixture 정의

fixture = 테스트에 필요한 준비물 (DB 세션, 테스트 데이터 등)
여러 테스트에서 재사용 가능

사용 예시:
    def test_create_stock(db_session):  # ← fixture 주입
        stock = Stock(stock_code="005930", ...)
        db_session.add(stock)
        db_session.commit()
"""

import pytest
from datetime import datetime, date
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from database.models import Base, Stock, Sector, OHLCVDaily, InvestorTrading
from config.settings import settings


# ==========================================
# 1. 테스트용 DB 엔진 생성
# ==========================================

@pytest.fixture(scope="session")
def engine():
    """
    테스트용 DB 엔진 생성 (전체 테스트 세션 동안 유지)

    scope="session": 모든 테스트가 끝날 때까지 1번만 생성
    """
    # 실제 DB 사용 (테스트용 DB를 따로 만들 수도 있음)
    test_engine = create_engine(
        settings.database_url,
        echo=False,  # SQL 로그 출력 안 함 (테스트 중에는 조용하게)
    )

    yield test_engine

    # 테스트 종료 후 엔진 정리
    test_engine.dispose()


# ==========================================
# 2. 테스트용 DB 테이블 생성/삭제
# ==========================================

@pytest.fixture(scope="session")
def tables(engine):
    """
    테스트 시작 전 테이블 생성, 종료 후 삭제

    주의: 실제 운영 환경에서는 테스트용 DB를 따로 만드는 것이 안전!
    현재는 개발 환경이므로 같은 DB 사용
    """
    # 테이블이 이미 존재하면 생성 안 함
    # (init_schema.sql로 이미 만들어져 있음)
    # Base.metadata.create_all(bind=engine)

    yield

    # 테스트 종료 후 테이블 삭제 (선택사항)
    # Base.metadata.drop_all(bind=engine)
    # → 실제로는 삭제하지 않고 데이터만 정리


# ==========================================
# 3. 테스트용 세션 팩토리
# ==========================================

@pytest.fixture(scope="session")
def SessionLocal(engine, tables):
    """
    테스트용 세션 팩토리 생성
    """
    return sessionmaker(bind=engine)


# ==========================================
# 4. 각 테스트마다 독립적인 DB 세션 제공
# ==========================================

@pytest.fixture(scope="function")
def db_session(SessionLocal):
    """
    각 테스트 함수마다 독립적인 DB 세션 제공

    중요: 트랜잭션 롤백!
    - 테스트가 끝나면 모든 변경사항을 되돌림
    - 테스트 간 데이터 간섭 방지

    scope="function": 테스트 함수마다 새로 생성

    사용 예시:
        def test_something(db_session):
            stock = Stock(...)
            db_session.add(stock)
            db_session.commit()
            # 테스트 종료 후 자동으로 롤백됨!
    """
    session = SessionLocal()

    # 트랜잭션 시작
    session.begin_nested()

    yield session

    # 테스트 종료 후 롤백 (변경사항 모두 취소)
    session.rollback()
    session.close()


# ==========================================
# 5. 샘플 데이터 fixture
# ==========================================

@pytest.fixture
def sample_sector(db_session):
    """
    테스트용 샘플 섹터 생성

    다른 테스트에서 사용 가능:
        def test_stock(db_session, sample_sector):
            stock = Stock(sector_id=sample_sector.sector_id, ...)
    """
    sector = Sector(
        sector_name="IT산업",
        sector_code="IT",
        parent_sector_id=None
    )
    db_session.add(sector)
    db_session.commit()
    db_session.refresh(sector)  # DB에서 자동 생성된 ID 가져오기

    return sector


@pytest.fixture
def sample_stock(db_session, sample_sector):
    """
    테스트용 샘플 종목 생성
    """
    stock = Stock(
        stock_code="005930",
        stock_name="삼성전자",
        market="KOSPI",
        sector_id=sample_sector.sector_id,
        listing_date=date(1975, 6, 11),
        is_active=True
    )
    db_session.add(stock)
    db_session.commit()
    db_session.refresh(stock)

    return stock


@pytest.fixture
def sample_ohlcv(db_session, sample_stock):
    """
    테스트용 샘플 OHLCV 데이터 생성
    """
    ohlcv = OHLCVDaily(
        time=date(2026, 2, 18),
        stock_code=sample_stock.stock_code,
        open_price=75000,
        high_price=76000,
        low_price=74500,
        close_price=75500,
        volume=10000000,
        trading_value=755000000000
    )
    db_session.add(ohlcv)
    db_session.commit()
    db_session.refresh(ohlcv)

    return ohlcv


@pytest.fixture
def sample_investor_trading(db_session, sample_stock):
    """
    테스트용 샘플 투자자별 수급 데이터 생성
    """
    trading = InvestorTrading(
        time=date(2026, 2, 18),
        stock_code=sample_stock.stock_code,
        investor_type="FOREIGN",
        buy_volume=500000,
        sell_volume=400000,
        net_buy_volume=100000,
        buy_value=37500000000,
        sell_value=30000000000,
        net_buy_value=7500000000
    )
    db_session.add(trading)
    db_session.commit()
    db_session.refresh(trading)

    return trading


# ==========================================
# 6. pytest 설정
# ==========================================

def pytest_configure(config):
    """
    pytest 시작 전 설정
    """
    # 커스텀 마커 등록 (선택사항)
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )


# ==========================================
# 7. 테스트 헬퍼 함수
# ==========================================

def clear_table(session: Session, model):
    """
    특정 테이블의 모든 데이터 삭제

    사용 예시:
        clear_table(db_session, Stock)
    """
    session.query(model).delete()
    session.commit()


def count_records(session: Session, model):
    """
    특정 테이블의 레코드 수 반환

    사용 예시:
        count = count_records(db_session, Stock)
    """
    return session.query(model).count()
