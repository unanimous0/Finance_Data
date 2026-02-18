"""
Pydantic 스키마 검증 테스트

각 스키마의 검증 로직이 제대로 동작하는지 테스트합니다.

테스트 패턴:
1. 정상 케이스: 올바른 데이터가 통과하는지
2. 에러 케이스: 잘못된 데이터가 거부되는지
"""

import pytest
from datetime import date, datetime
from decimal import Decimal
from pydantic import ValidationError

from validators.schemas import (
    StockSchema,
    SectorSchema,
    IndexComponentSchema,
    FloatingSharesSchema,
    ETFPortfoliosSchema,
    MarketCapDailySchema,
    OHLCVDailySchema,
    InvestorTradingSchema,
    DataCollectionLogsSchema,
    DataQualityChecksSchema,
)


# ==========================================
# StockSchema 테스트
# ==========================================

class TestStockSchema:
    """Stock 스키마 검증 테스트"""

    def test_valid_stock(self):
        """정상 케이스: 올바른 종목 데이터"""
        data = {
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "market": "KOSPI",
            "sector_id": 1,
            "listing_date": "1975-06-11",
            "is_active": True
        }
        stock = StockSchema(**data)

        assert stock.stock_code == "005930"
        assert stock.stock_name == "삼성전자"
        assert stock.market == "KOSPI"
        assert stock.sector_id == 1

    def test_market_uppercase_conversion(self):
        """시장구분 대문자 자동 변환"""
        data = {
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "market": "kospi",  # 소문자 입력
            "is_active": True
        }
        stock = StockSchema(**data)

        assert stock.market == "KOSPI"  # 대문자로 변환됨

    def test_invalid_market(self):
        """에러 케이스: 잘못된 시장구분"""
        data = {
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "market": "NYSE",  # ❌ KOSPI/KOSDAQ가 아님
            "is_active": True
        }

        with pytest.raises(ValidationError) as exc_info:
            StockSchema(**data)

        # 에러 메시지 확인
        assert "시장구분은 KOSPI 또는 KOSDAQ만 가능합니다" in str(exc_info.value)

    def test_stock_code_length_validation(self):
        """에러 케이스: 종목코드 길이 초과"""
        data = {
            "stock_code": "12345678901",  # ❌ 10자 초과
            "stock_name": "삼성전자",
            "market": "KOSPI",
            "is_active": True
        }

        with pytest.raises(ValidationError) as exc_info:
            StockSchema(**data)

        assert "stock_code" in str(exc_info.value)

    def test_delisting_date_validation(self):
        """에러 케이스: 상장폐지일이 상장일보다 이전"""
        data = {
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "market": "KOSPI",
            "listing_date": "2020-01-01",
            "delisting_date": "2019-12-31",  # ❌ 상장일보다 이전
            "is_active": True
        }

        with pytest.raises(ValidationError) as exc_info:
            StockSchema(**data)

        assert "상장폐지일은 상장일보다 이후여야 합니다" in str(exc_info.value)


# ==========================================
# SectorSchema 테스트
# ==========================================

class TestSectorSchema:
    """Sector 스키마 검증 테스트"""

    def test_valid_top_level_sector(self):
        """정상 케이스: 최상위 섹터"""
        data = {
            "sector_name": "IT산업",
            "sector_code": "IT",
            "parent_sector_id": None  # 최상위
        }
        sector = SectorSchema(**data)

        assert sector.sector_name == "IT산업"
        assert sector.parent_sector_id is None

    def test_valid_child_sector(self):
        """정상 케이스: 하위 섹터"""
        data = {
            "sector_id": 2,
            "sector_name": "반도체",
            "sector_code": "IT001",
            "parent_sector_id": 1  # IT산업의 하위
        }
        sector = SectorSchema(**data)

        assert sector.sector_name == "반도체"
        assert sector.parent_sector_id == 1

    def test_self_reference_validation(self):
        """에러 케이스: 자기 자신을 부모로 참조"""
        data = {
            "sector_id": 1,
            "sector_name": "IT산업",
            "sector_code": "IT",
            "parent_sector_id": 1  # ❌ 자기 자신
        }

        with pytest.raises(ValidationError) as exc_info:
            SectorSchema(**data)

        assert "상위 섹터가 자기 자신일 수 없습니다" in str(exc_info.value)


# ==========================================
# OHLCVDailySchema 테스트 (가장 중요!)
# ==========================================

class TestOHLCVDailySchema:
    """OHLCV 스키마 검증 테스트"""

    def test_valid_ohlcv(self):
        """정상 케이스: 올바른 OHLCV 데이터"""
        data = {
            "time": "2026-02-18",
            "stock_code": "005930",
            "open_price": 75000,
            "high_price": 76000,
            "low_price": 74500,
            "close_price": 75500,
            "volume": 10000000,
            "trading_value": 755000000000
        }
        ohlcv = OHLCVDailySchema(**data)

        assert ohlcv.stock_code == "005930"
        assert ohlcv.high_price == 76000
        assert ohlcv.low_price == 74500

    def test_high_price_less_than_open(self):
        """에러 케이스: 고가 < 시가"""
        data = {
            "time": "2026-02-18",
            "stock_code": "005930",
            "open_price": 75000,
            "high_price": 74000,  # ❌ 시가보다 낮음
            "low_price": 73000,
            "close_price": 74500,
            "volume": 10000000
        }

        with pytest.raises(ValidationError) as exc_info:
            OHLCVDailySchema(**data)

        assert "고가" in str(exc_info.value)
        assert "시가보다 낮을 수 없습니다" in str(exc_info.value)

    def test_high_price_less_than_low(self):
        """에러 케이스: 고가 < 저가"""
        data = {
            "time": "2026-02-18",
            "stock_code": "005930",
            "open_price": 75000,
            "high_price": 74000,
            "low_price": 75000,  # ❌ 고가보다 높음
            "close_price": 74500,
            "volume": 10000000
        }

        with pytest.raises(ValidationError) as exc_info:
            OHLCVDailySchema(**data)

        assert "고가" in str(exc_info.value) or "저가" in str(exc_info.value)

    def test_negative_price(self):
        """에러 케이스: 음수 가격"""
        data = {
            "time": "2026-02-18",
            "stock_code": "005930",
            "open_price": -1000,  # ❌ 음수
            "high_price": 76000,
            "low_price": 74500,
            "close_price": 75500,
            "volume": 10000000
        }

        with pytest.raises(ValidationError) as exc_info:
            OHLCVDailySchema(**data)

        assert "open_price" in str(exc_info.value)

    def test_future_date(self):
        """에러 케이스: 미래 날짜"""
        future_date = date(2030, 12, 31)
        data = {
            "time": future_date,
            "stock_code": "005930",
            "open_price": 75000,
            "high_price": 76000,
            "low_price": 74500,
            "close_price": 75500,
            "volume": 10000000
        }

        with pytest.raises(ValidationError) as exc_info:
            OHLCVDailySchema(**data)

        assert "미래 날짜는 입력할 수 없습니다" in str(exc_info.value)


# ==========================================
# InvestorTradingSchema 테스트
# ==========================================

class TestInvestorTradingSchema:
    """InvestorTrading 스키마 검증 테스트"""

    def test_valid_investor_trading(self):
        """정상 케이스: 올바른 투자자별 수급 데이터"""
        data = {
            "time": "2026-02-18",
            "stock_code": "005930",
            "investor_type": "FOREIGN",
            "buy_volume": 500000,
            "sell_volume": 400000,
            "net_buy_volume": 100000,
            "buy_value": 37500000000,
            "sell_value": 30000000000,
            "net_buy_value": 7500000000
        }
        trading = InvestorTradingSchema(**data)

        assert trading.investor_type == "FOREIGN"
        assert trading.net_buy_volume == 100000

    def test_investor_type_uppercase_conversion(self):
        """투자자 유형 대문자 자동 변환"""
        data = {
            "time": "2026-02-18",
            "stock_code": "005930",
            "investor_type": "foreign",  # 소문자 입력
            "net_buy_volume": 100000
        }
        trading = InvestorTradingSchema(**data)

        assert trading.investor_type == "FOREIGN"  # 대문자로 변환됨

    def test_invalid_investor_type(self):
        """에러 케이스: 잘못된 투자자 유형"""
        data = {
            "time": "2026-02-18",
            "stock_code": "005930",
            "investor_type": "UNKNOWN",  # ❌ 허용되지 않는 유형
            "net_buy_volume": 100000
        }

        with pytest.raises(ValidationError) as exc_info:
            InvestorTradingSchema(**data)

        assert "투자자 유형은" in str(exc_info.value)

    def test_net_buy_volume_validation(self):
        """에러 케이스: 순매수 수량 불일치"""
        data = {
            "time": "2026-02-18",
            "stock_code": "005930",
            "investor_type": "FOREIGN",
            "buy_volume": 500000,
            "sell_volume": 400000,
            "net_buy_volume": 200000,  # ❌ 실제 = 100000 (500000 - 400000)
        }

        with pytest.raises(ValidationError) as exc_info:
            InvestorTradingSchema(**data)

        assert "순매수 수량" in str(exc_info.value)
        assert "일치하지 않습니다" in str(exc_info.value)


# ==========================================
# FloatingSharesSchema 테스트
# ==========================================

class TestFloatingSharesSchema:
    """FloatingShares 스키마 검증 테스트"""

    def test_valid_floating_shares(self):
        """정상 케이스: 올바른 유동주식 데이터"""
        data = {
            "stock_code": "005930",
            "base_date": "2026-02-18",
            "total_shares": 5969783000,
            "floating_shares": 4000000000,
            "floating_ratio": Decimal("67.02")
        }
        floating = FloatingSharesSchema(**data)

        assert floating.stock_code == "005930"
        assert floating.floating_shares == 4000000000

    def test_floating_shares_exceeds_total(self):
        """에러 케이스: 유동주식수 > 총주식수"""
        data = {
            "stock_code": "005930",
            "base_date": "2026-02-18",
            "total_shares": 1000000,
            "floating_shares": 2000000,  # ❌ 총주식수보다 많음
        }

        with pytest.raises(ValidationError) as exc_info:
            FloatingSharesSchema(**data)

        assert "유동주식수는 총 상장주식수를 초과할 수 없습니다" in str(exc_info.value)


# ==========================================
# ETFPortfoliosSchema 테스트
# ==========================================

class TestETFPortfoliosSchema:
    """ETFPortfolios 스키마 검증 테스트"""

    def test_valid_etf_portfolio(self):
        """정상 케이스: 올바른 ETF 포트폴리오 데이터"""
        data = {
            "etf_code": "102110",
            "component_code": "005930",
            "base_date": "2026-02-18",
            "weight": Decimal("25.5"),
            "shares": 1000000
        }
        portfolio = ETFPortfoliosSchema(**data)

        assert portfolio.etf_code == "102110"
        assert portfolio.component_code == "005930"

    def test_etf_self_reference(self):
        """에러 케이스: ETF가 자기 자신을 구성종목으로 가짐"""
        data = {
            "etf_code": "102110",
            "component_code": "102110",  # ❌ 자기 자신
            "base_date": "2026-02-18",
            "weight": Decimal("100.0")
        }

        with pytest.raises(ValidationError) as exc_info:
            ETFPortfoliosSchema(**data)

        assert "ETF가 자기 자신을 구성종목으로 가질 수 없습니다" in str(exc_info.value)


# ==========================================
# DataCollectionLogsSchema 테스트
# ==========================================

class TestDataCollectionLogsSchema:
    """DataCollectionLogs 스키마 검증 테스트"""

    def test_valid_collection_log(self):
        """정상 케이스: 올바른 수집 로그"""
        data = {
            "data_type": "OHLCV",
            "collection_date": "2026-02-18",
            "source": "INFOMAX",
            "status": "SUCCESS",
            "records_count": 3000,
            "started_at": datetime(2026, 2, 18, 18, 0, 0),
            "completed_at": datetime(2026, 2, 18, 18, 5, 0)
        }
        log = DataCollectionLogsSchema(**data)

        assert log.data_type == "OHLCV"
        assert log.status == "SUCCESS"

    def test_completed_at_before_started_at(self):
        """에러 케이스: 완료 시각이 시작 시각보다 이전"""
        data = {
            "data_type": "OHLCV",
            "collection_date": "2026-02-18",
            "status": "SUCCESS",
            "started_at": datetime(2026, 2, 18, 18, 5, 0),
            "completed_at": datetime(2026, 2, 18, 18, 0, 0),  # ❌ 시작보다 이전
        }

        with pytest.raises(ValidationError) as exc_info:
            DataCollectionLogsSchema(**data)

        assert "완료 시각은 시작 시각보다 이후여야 합니다" in str(exc_info.value)


# ==========================================
# 통합 테스트
# ==========================================

class TestSchemaIntegration:
    """여러 스키마를 함께 사용하는 통합 테스트"""

    def test_stock_to_orm_conversion(self):
        """Pydantic 스키마 → dict → ORM 모델 변환"""
        stock_data = {
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "market": "KOSPI",
            "is_active": True
        }

        # Pydantic 검증
        stock_schema = StockSchema(**stock_data)

        # dict로 변환 (ORM에 넣을 준비)
        stock_dict = stock_schema.model_dump()

        assert stock_dict["stock_code"] == "005930"
        assert stock_dict["market"] == "KOSPI"
        assert "is_active" in stock_dict

    def test_multiple_schemas(self):
        """여러 스키마 동시 검증"""
        # Stock 검증
        stock = StockSchema(
            stock_code="005930",
            stock_name="삼성전자",
            market="KOSPI",
            is_active=True
        )

        # OHLCV 검증
        ohlcv = OHLCVDailySchema(
            time="2026-02-18",
            stock_code=stock.stock_code,  # Stock의 종목코드 사용
            open_price=75000,
            high_price=76000,
            low_price=74500,
            close_price=75500,
            volume=10000000
        )

        # InvestorTrading 검증
        trading = InvestorTradingSchema(
            time="2026-02-18",
            stock_code=stock.stock_code,  # Stock의 종목코드 사용
            investor_type="FOREIGN",
            net_buy_volume=100000
        )

        # 모두 같은 종목코드 사용
        assert stock.stock_code == ohlcv.stock_code == trading.stock_code
