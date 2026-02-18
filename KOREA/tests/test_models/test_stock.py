"""
Stock 모델 CRUD 테스트

CRUD = Create, Read, Update, Delete
데이터베이스 기본 작업이 제대로 동작하는지 테스트합니다.
"""

import pytest
from datetime import date
from sqlalchemy.exc import IntegrityError

from database.models import Stock, Sector


class TestStockCRUD:
    """Stock 모델 기본 CRUD 테스트"""

    def test_create_stock(self, db_session, sample_sector):
        """CREATE: 종목 생성"""
        # Given: 종목 데이터 준비
        stock = Stock(
            stock_code="005930",
            stock_name="삼성전자",
            market="KOSPI",
            sector_id=sample_sector.sector_id,
            listing_date=date(1975, 6, 11),
            is_active=True
        )

        # When: DB에 저장
        db_session.add(stock)
        db_session.commit()
        db_session.refresh(stock)

        # Then: 저장된 데이터 확인
        assert stock.stock_code == "005930"
        assert stock.stock_name == "삼성전자"
        assert stock.market == "KOSPI"
        assert stock.is_active is True

    def test_read_stock(self, db_session, sample_stock):
        """READ: 종목 조회"""
        # When: 종목코드로 조회
        stock = db_session.query(Stock).filter_by(stock_code="005930").first()

        # Then: 조회 성공
        assert stock is not None
        assert stock.stock_name == "삼성전자"
        assert stock.market == "KOSPI"

    def test_update_stock(self, db_session, sample_stock):
        """UPDATE: 종목 수정"""
        # When: 종목명 변경
        sample_stock.stock_name = "삼성전자(주)"
        db_session.commit()
        db_session.refresh(sample_stock)

        # Then: 변경사항 반영 확인
        stock = db_session.query(Stock).filter_by(stock_code="005930").first()
        assert stock.stock_name == "삼성전자(주)"

    def test_delete_stock(self, db_session, sample_stock):
        """DELETE: 종목 삭제"""
        # When: 종목 삭제
        db_session.delete(sample_stock)
        db_session.commit()

        # Then: 삭제 확인
        stock = db_session.query(Stock).filter_by(stock_code="005930").first()
        assert stock is None

    def test_duplicate_stock_code(self, db_session, sample_stock):
        """에러 케이스: 중복된 종목코드"""
        # When: 같은 종목코드로 새 종목 생성 시도
        duplicate_stock = Stock(
            stock_code="005930",  # ❌ 이미 존재하는 종목코드
            stock_name="중복테스트",
            market="KOSPI",
            is_active=True
        )

        # Then: IntegrityError 발생 (Primary Key 중복)
        with pytest.raises(IntegrityError):
            db_session.add(duplicate_stock)
            db_session.commit()


class TestStockRelationships:
    """Stock 모델 관계(Relationship) 테스트"""

    def test_stock_sector_relationship(self, db_session, sample_sector):
        """Stock ↔ Sector 관계 테스트"""
        # Given: 종목 생성
        stock = Stock(
            stock_code="005930",
            stock_name="삼성전자",
            market="KOSPI",
            sector_id=sample_sector.sector_id,
            is_active=True
        )
        db_session.add(stock)
        db_session.commit()
        db_session.refresh(stock)

        # When: Relationship를 통해 섹터 접근
        sector = stock.sector

        # Then: 올바른 섹터 반환
        assert sector is not None
        assert sector.sector_id == sample_sector.sector_id
        assert sector.sector_name == "IT산업"

    def test_sector_stocks_relationship(self, db_session, sample_sector):
        """Sector → Stock 역방향 관계 테스트"""
        # Given: 같은 섹터에 여러 종목 추가
        stocks_data = [
            ("005930", "삼성전자"),
            ("000660", "SK하이닉스"),
            ("035420", "NAVER")
        ]

        for code, name in stocks_data:
            stock = Stock(
                stock_code=code,
                stock_name=name,
                market="KOSPI",
                sector_id=sample_sector.sector_id,
                is_active=True
            )
            db_session.add(stock)

        db_session.commit()

        # When: Sector에서 종목 리스트 조회
        stocks = sample_sector.stocks.all()

        # Then: 3개 종목 모두 조회됨
        assert len(stocks) == 3
        stock_codes = [s.stock_code for s in stocks]
        assert "005930" in stock_codes
        assert "000660" in stock_codes
        assert "035420" in stock_codes


class TestStockQueries:
    """Stock 모델 복잡한 쿼리 테스트"""

    def test_filter_by_market(self, db_session, sample_sector):
        """시장별 종목 필터링"""
        # Given: KOSPI와 KOSDAQ 종목 각각 추가
        kospi_stock = Stock(
            stock_code="005930",
            stock_name="삼성전자",
            market="KOSPI",
            sector_id=sample_sector.sector_id,
            is_active=True
        )
        kosdaq_stock = Stock(
            stock_code="035720",
            stock_name="카카오",
            market="KOSDAQ",
            sector_id=sample_sector.sector_id,
            is_active=True
        )
        db_session.add_all([kospi_stock, kosdaq_stock])
        db_session.commit()

        # When: KOSPI 종목만 조회
        kospi_stocks = db_session.query(Stock).filter_by(market="KOSPI").all()

        # Then: KOSPI 종목만 반환
        assert len(kospi_stocks) == 1
        assert kospi_stocks[0].stock_code == "005930"

    def test_filter_active_stocks(self, db_session, sample_sector):
        """활성 종목 필터링"""
        # Given: 활성/비활성 종목 추가
        active_stock = Stock(
            stock_code="005930",
            stock_name="삼성전자",
            market="KOSPI",
            sector_id=sample_sector.sector_id,
            is_active=True
        )
        inactive_stock = Stock(
            stock_code="999999",
            stock_name="상장폐지종목",
            market="KOSPI",
            sector_id=sample_sector.sector_id,
            is_active=False,
            delisting_date=date(2025, 12, 31)
        )
        db_session.add_all([active_stock, inactive_stock])
        db_session.commit()

        # When: 활성 종목만 조회
        active_stocks = db_session.query(Stock).filter_by(is_active=True).all()

        # Then: 활성 종목만 반환
        assert len(active_stocks) == 1
        assert active_stocks[0].stock_code == "005930"

    def test_count_stocks_by_sector(self, db_session):
        """섹터별 종목 수 카운트"""
        # Given: 2개 섹터 생성
        it_sector = Sector(sector_name="IT", sector_code="IT")
        finance_sector = Sector(sector_name="금융", sector_code="FIN")
        db_session.add_all([it_sector, finance_sector])
        db_session.commit()

        # IT 섹터에 2개 종목
        db_session.add_all([
            Stock(stock_code="005930", stock_name="삼성전자", market="KOSPI",
                  sector_id=it_sector.sector_id, is_active=True),
            Stock(stock_code="000660", stock_name="SK하이닉스", market="KOSPI",
                  sector_id=it_sector.sector_id, is_active=True)
        ])

        # 금융 섹터에 1개 종목
        db_session.add(
            Stock(stock_code="055550", stock_name="신한지주", market="KOSPI",
                  sector_id=finance_sector.sector_id, is_active=True)
        )
        db_session.commit()

        # When: 섹터별 종목 수 조회
        it_count = it_sector.stocks.count()
        finance_count = finance_sector.stocks.count()

        # Then: 올바른 개수 반환
        assert it_count == 2
        assert finance_count == 1


class TestStockValidation:
    """Stock 모델 데이터 검증 테스트"""

    def test_stock_without_required_fields(self, db_session):
        """에러 케이스: 필수 필드 누락"""
        # When: stock_name 없이 생성 시도
        stock = Stock(
            stock_code="005930",
            # stock_name 누락! ❌
            market="KOSPI",
            is_active=True
        )

        # Then: IntegrityError 발생 (NOT NULL 제약 위반)
        with pytest.raises(IntegrityError):
            db_session.add(stock)
            db_session.commit()

    def test_stock_with_invalid_sector_id(self, db_session):
        """에러 케이스: 존재하지 않는 섹터 ID"""
        # When: 존재하지 않는 섹터 ID로 종목 생성
        stock = Stock(
            stock_code="005930",
            stock_name="삼성전자",
            market="KOSPI",
            sector_id=99999,  # ❌ 존재하지 않는 섹터
            is_active=True
        )

        # Then: IntegrityError 발생 (Foreign Key 제약 위반)
        with pytest.raises(IntegrityError):
            db_session.add(stock)
            db_session.commit()
