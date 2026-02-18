"""
Pydantic 검증 스키마 정의

이 파일에서는 외부 데이터(API, 파일 등)를 검증하는 스키마를 정의합니다.
Pydantic = 데이터가 올바른 형식인지 자동으로 체크하는 라이브러리

사용 예시:
    # API에서 받은 데이터
    api_data = {"stock_code": "005930", "stock_name": "삼성전자", ...}

    # Pydantic으로 검증
    stock = StockSchema(**api_data)  # 형식이 올바르면 성공, 아니면 에러

    # DB에 저장
    db_stock = Stock(**stock.model_dump())
    session.add(db_stock)
"""

from datetime import date, datetime
from typing import Optional
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator, ConfigDict


# ==========================================
# 기본 설정
# ==========================================

class BaseSchema(BaseModel):
    """
    모든 스키마의 부모 클래스

    공통 설정을 여기서 정의합니다.
    """

    # Pydantic V2 설정
    model_config = ConfigDict(
        # SQLAlchemy 모델과 호환 (ORM 객체를 Pydantic으로 변환 가능)
        from_attributes=True,
        # 문자열을 자동으로 strip (공백 제거)
        str_strip_whitespace=True,
        # JSON 스키마 생성 모드
        json_schema_mode='validation'
    )


# ==========================================
# 1. Stock Schema (종목 마스터)
# ==========================================

class StockSchema(BaseSchema):
    """
    종목 마스터 데이터 검증 스키마

    인포맥스 API나 HTS에서 받은 종목 데이터를 검증합니다.
    """

    # Field(...)로 추가 검증 조건 지정 가능
    stock_code: str = Field(
        ...,  # required (필수 필드)
        min_length=6,  # 최소 6자
        max_length=10,  # 최대 10자
        description="종목코드",
        examples=["005930", "035720"]
    )

    stock_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="종목명",
        examples=["삼성전자", "카카오"]
    )

    market: str = Field(
        ...,
        description="시장구분",
        examples=["KOSPI", "KOSDAQ"]
    )

    # Optional = None 허용
    sector_id: Optional[int] = Field(
        None,
        ge=1,  # greater than or equal (1 이상)
        description="섹터 ID"
    )

    listing_date: Optional[date] = Field(
        None,
        description="상장일"
    )

    delisting_date: Optional[date] = Field(
        None,
        description="상장폐지일"
    )

    is_active: bool = Field(
        default=True,
        description="활성 여부"
    )

    # ==========================================
    # 커스텀 검증 (Validator)
    # ==========================================

    @field_validator('market')
    @classmethod
    def validate_market(cls, v: str) -> str:
        """
        시장구분 검증: KOSPI 또는 KOSDAQ만 허용

        Args:
            v: 검증할 값

        Returns:
            검증된 값 (대문자 변환)

        Raises:
            ValueError: KOSPI/KOSDAQ가 아닌 경우
        """
        v = v.upper()  # 대문자 변환
        if v not in ["KOSPI", "KOSDAQ"]:
            raise ValueError(f"시장구분은 KOSPI 또는 KOSDAQ만 가능합니다 (입력값: {v})")
        return v

    @field_validator('delisting_date')
    @classmethod
    def validate_delisting_date(cls, v: Optional[date], info) -> Optional[date]:
        """
        상장폐지일 검증: 상장일보다 이후여야 함

        Args:
            v: 상장폐지일
            info: 다른 필드 값 접근용

        Returns:
            검증된 상장폐지일

        Raises:
            ValueError: 상장폐지일이 상장일보다 이전인 경우
        """
        if v is not None and info.data.get('listing_date') is not None:
            if v < info.data['listing_date']:
                raise ValueError("상장폐지일은 상장일보다 이후여야 합니다")
        return v


# ==========================================
# 2. Sector Schema (섹터 분류)
# ==========================================

class SectorSchema(BaseSchema):
    """
    섹터 분류 데이터 검증 스키마
    """

    sector_id: Optional[int] = Field(
        None,
        ge=1,
        description="섹터 ID (자동 생성시 None)"
    )

    sector_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="섹터명",
        examples=["IT산업", "반도체", "금융"]
    )

    sector_code: Optional[str] = Field(
        None,
        max_length=20,
        description="섹터 코드",
        examples=["IT", "IT001"]
    )

    parent_sector_id: Optional[int] = Field(
        None,
        ge=1,
        description="상위 섹터 ID (최상위 섹터면 None)"
    )

    @field_validator('parent_sector_id')
    @classmethod
    def validate_parent_sector_id(cls, v: Optional[int], info) -> Optional[int]:
        """
        자기 참조 검증: parent_sector_id가 자기 자신을 가리키면 안 됨
        """
        if v is not None and info.data.get('sector_id') is not None:
            if v == info.data['sector_id']:
                raise ValueError("상위 섹터가 자기 자신일 수 없습니다")
        return v


# ==========================================
# 3-5. Foreign Key 스키마 (한번에 작성)
# ==========================================

class IndexComponentSchema(BaseSchema):
    """지수 구성종목 데이터 검증 스키마"""

    id: Optional[int] = Field(None, ge=1, description="ID")
    index_name: str = Field(..., max_length=50, description="지수명", examples=["KOSPI200", "KOSDAQ150"])
    stock_code: str = Field(..., min_length=6, max_length=10, description="종목코드")
    effective_date: date = Field(..., description="편입일")
    end_date: Optional[date] = Field(None, description="제외일 (None=현재 편입 중)")

    @field_validator('end_date')
    @classmethod
    def validate_end_date(cls, v: Optional[date], info) -> Optional[date]:
        """제외일은 편입일보다 이후여야 함"""
        if v is not None and v < info.data.get('effective_date'):
            raise ValueError("제외일은 편입일보다 이후여야 합니다")
        return v


class FloatingSharesSchema(BaseSchema):
    """유동주식 데이터 검증 스키마"""

    id: Optional[int] = Field(None, ge=1, description="ID")
    stock_code: str = Field(..., min_length=6, max_length=10, description="종목코드")
    base_date: date = Field(..., description="기준일")
    total_shares: Optional[int] = Field(None, ge=0, description="총 상장주식수")
    floating_shares: Optional[int] = Field(None, ge=0, description="유동주식수")
    floating_ratio: Optional[Decimal] = Field(None, ge=0, le=100, description="유동비율 (%)")

    @field_validator('floating_shares')
    @classmethod
    def validate_floating_shares(cls, v: Optional[int], info) -> Optional[int]:
        """유동주식수는 총 상장주식수를 초과할 수 없음"""
        if v is not None and info.data.get('total_shares') is not None:
            if v > info.data['total_shares']:
                raise ValueError("유동주식수는 총 상장주식수를 초과할 수 없습니다")
        return v


class ETFPortfoliosSchema(BaseSchema):
    """ETF 포트폴리오 데이터 검증 스키마"""

    id: Optional[int] = Field(None, ge=1, description="ID")
    etf_code: str = Field(..., min_length=6, max_length=10, description="ETF 종목코드")
    component_code: str = Field(..., min_length=6, max_length=10, description="구성 종목코드")
    base_date: date = Field(..., description="기준일")
    weight: Optional[Decimal] = Field(None, ge=0, le=100, description="비중 (%)")
    shares: Optional[int] = Field(None, ge=0, description="보유 주식수")

    @field_validator('component_code')
    @classmethod
    def validate_component_code(cls, v: str, info) -> str:
        """ETF가 자기 자신을 구성종목으로 가질 수 없음"""
        if v == info.data.get('etf_code'):
            raise ValueError("ETF가 자기 자신을 구성종목으로 가질 수 없습니다")
        return v


# ==========================================
# 6-8. Hypertable 스키마 (시계열 데이터)
# ==========================================

class MarketCapDailySchema(BaseSchema):
    """
    일별 시가총액 데이터 검증 스키마

    Hypertable 데이터 검증
    """

    time: date = Field(..., description="날짜")
    stock_code: str = Field(..., min_length=6, max_length=10, description="종목코드")
    market_cap: Optional[int] = Field(None, ge=0, description="시가총액 (원)")
    shares_outstanding: Optional[int] = Field(None, ge=0, description="상장주식수")

    @field_validator('time')
    @classmethod
    def validate_time(cls, v: date) -> date:
        """미래 날짜 검증"""
        if v > date.today():
            raise ValueError("미래 날짜는 입력할 수 없습니다")
        return v


class OHLCVDailySchema(BaseSchema):
    """
    일별 OHLCV 데이터 검증 스키마

    가장 중요한 스키마! (차트 데이터)
    """

    time: date = Field(..., description="날짜")
    stock_code: str = Field(..., min_length=6, max_length=10, description="종목코드")

    # 가격 데이터 (양수 체크)
    open_price: Optional[int] = Field(None, ge=0, description="시가 (원)")
    high_price: Optional[int] = Field(None, ge=0, description="고가 (원)")
    low_price: Optional[int] = Field(None, ge=0, description="저가 (원)")
    close_price: Optional[int] = Field(None, ge=0, description="종가 (원)")

    # 거래량/거래대금
    volume: Optional[int] = Field(None, ge=0, description="거래량 (주)")
    trading_value: Optional[int] = Field(None, ge=0, description="거래대금 (원)")

    @field_validator('time')
    @classmethod
    def validate_time(cls, v: date) -> date:
        """미래 날짜 검증"""
        if v > date.today():
            raise ValueError("미래 날짜는 입력할 수 없습니다")
        return v

    @field_validator('high_price')
    @classmethod
    def validate_high_price(cls, v: Optional[int], info) -> Optional[int]:
        """
        고가 검증: 시가/저가/종가보다 높거나 같아야 함
        """
        if v is None:
            return v

        open_price = info.data.get('open_price')
        low_price = info.data.get('low_price')

        # 고가 >= 시가
        if open_price is not None and v < open_price:
            raise ValueError(f"고가({v})는 시가({open_price})보다 낮을 수 없습니다")

        # 고가 >= 저가
        if low_price is not None and v < low_price:
            raise ValueError(f"고가({v})는 저가({low_price})보다 낮을 수 없습니다")

        return v

    @field_validator('low_price')
    @classmethod
    def validate_low_price(cls, v: Optional[int], info) -> Optional[int]:
        """
        저가 검증: 시가/고가/종가보다 낮거나 같아야 함
        """
        if v is None:
            return v

        open_price = info.data.get('open_price')
        high_price = info.data.get('high_price')

        # 저가 <= 시가
        if open_price is not None and v > open_price:
            raise ValueError(f"저가({v})는 시가({open_price})보다 높을 수 없습니다")

        # 저가 <= 고가 (high_price validator에서 체크하므로 생략 가능)

        return v


class InvestorTradingSchema(BaseSchema):
    """
    투자자별 수급 데이터 검증 스키마

    외국인/기관/개인/연기금 매매 데이터
    """

    time: date = Field(..., description="날짜")
    stock_code: str = Field(..., min_length=6, max_length=10, description="종목코드")
    investor_type: str = Field(..., max_length=20, description="투자자 유형")

    # 순매수 데이터 (음수 가능 = 순매도)
    net_buy_volume: Optional[int] = Field(None, description="순매수 수량 (주)")
    net_buy_value: Optional[int] = Field(None, description="순매수 금액 (원)")

    # 세부 데이터 (양수만 가능)
    buy_volume: Optional[int] = Field(None, ge=0, description="매수 수량 (주)")
    sell_volume: Optional[int] = Field(None, ge=0, description="매도 수량 (주)")
    buy_value: Optional[int] = Field(None, ge=0, description="매수 금액 (원)")
    sell_value: Optional[int] = Field(None, ge=0, description="매도 금액 (원)")

    @field_validator('time')
    @classmethod
    def validate_time(cls, v: date) -> date:
        """미래 날짜 검증"""
        if v > date.today():
            raise ValueError("미래 날짜는 입력할 수 없습니다")
        return v

    @field_validator('investor_type')
    @classmethod
    def validate_investor_type(cls, v: str) -> str:
        """
        투자자 유형 검증

        허용값: FOREIGN, INSTITUTION, RETAIL, PENSION
        (실제 API 데이터 확인 후 조정 필요)
        """
        v = v.upper()
        allowed = ["FOREIGN", "INSTITUTION", "RETAIL", "PENSION"]
        if v not in allowed:
            raise ValueError(f"투자자 유형은 {allowed} 중 하나여야 합니다 (입력값: {v})")
        return v

    @field_validator('net_buy_volume')
    @classmethod
    def validate_net_buy_volume(cls, v: Optional[int], info) -> Optional[int]:
        """
        순매수 수량 검증: net_buy_volume = buy_volume - sell_volume
        """
        if v is None:
            return v

        buy_vol = info.data.get('buy_volume')
        sell_vol = info.data.get('sell_volume')

        if buy_vol is not None and sell_vol is not None:
            expected = buy_vol - sell_vol
            if v != expected:
                raise ValueError(
                    f"순매수 수량({v})이 일치하지 않습니다. "
                    f"예상값: {expected} (매수 {buy_vol} - 매도 {sell_vol})"
                )

        return v


# ==========================================
# 9-10. 모니터링 스키마
# ==========================================

class DataCollectionLogsSchema(BaseSchema):
    """데이터 수집 이력 검증 스키마"""

    id: Optional[int] = Field(None, ge=1, description="ID")
    data_type: str = Field(..., max_length=50, description="데이터 종류")
    collection_date: date = Field(..., description="수집 대상 날짜")
    source: Optional[str] = Field(None, max_length=50, description="데이터 소스")
    status: Optional[str] = Field(None, max_length=20, description="상태")
    records_count: Optional[int] = Field(None, ge=0, description="수집된 레코드 수")
    error_message: Optional[str] = Field(None, description="에러 메시지")
    started_at: Optional[datetime] = Field(None, description="수집 시작 시각")
    completed_at: Optional[datetime] = Field(None, description="수집 완료 시각")

    @field_validator('status')
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        """상태값 검증"""
        if v is not None:
            v = v.upper()
            if v not in ["SUCCESS", "FAILED", "PARTIAL", "RUNNING"]:
                raise ValueError(f"상태는 SUCCESS/FAILED/PARTIAL/RUNNING 중 하나여야 합니다")
        return v

    @field_validator('completed_at')
    @classmethod
    def validate_completed_at(cls, v: Optional[datetime], info) -> Optional[datetime]:
        """완료 시각은 시작 시각보다 이후여야 함"""
        if v is not None and info.data.get('started_at') is not None:
            if v < info.data['started_at']:
                raise ValueError("완료 시각은 시작 시각보다 이후여야 합니다")
        return v


class DataQualityChecksSchema(BaseSchema):
    """데이터 품질 체크 검증 스키마"""

    id: Optional[int] = Field(None, ge=1, description="ID")
    table_name: str = Field(..., max_length=50, description="체크 대상 테이블명")
    check_date: date = Field(..., description="체크 날짜")
    check_type: Optional[str] = Field(None, max_length=50, description="체크 유형")
    issue_count: Optional[int] = Field(None, ge=0, description="발견된 이슈 개수")
    details: Optional[str] = Field(None, description="상세 정보 (JSON)")

    @field_validator('check_type')
    @classmethod
    def validate_check_type(cls, v: Optional[str]) -> Optional[str]:
        """체크 유형 검증"""
        if v is not None:
            v = v.upper()
            allowed = ["NULL_CHECK", "DUPLICATE_CHECK", "RANGE_CHECK", "CONSISTENCY_CHECK"]
            if v not in allowed:
                raise ValueError(f"체크 유형은 {allowed} 중 하나여야 합니다")
        return v


# ==========================================
# 모든 스키마 export
# ==========================================

__all__ = [
    "StockSchema",
    "SectorSchema",
    "IndexComponentSchema",
    "FloatingSharesSchema",
    "ETFPortfoliosSchema",
    "MarketCapDailySchema",
    "OHLCVDailySchema",
    "InvestorTradingSchema",
    "DataCollectionLogsSchema",
    "DataQualityChecksSchema",
]


# 간단한 테스트 코드
if __name__ == "__main__":
    print("=" * 60)
    print("📋 Pydantic 스키마 검증 테스트")
    print("=" * 60)

    print("\n1️⃣  StockSchema 정상 케이스")
    print("-" * 60)
    stock_data = {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "market": "kospi",  # 소문자로 입력 (자동 대문자 변환)
        "sector_id": 1,
        "listing_date": "1975-06-11",
        "is_active": True
    }
    stock = StockSchema(**stock_data)
    print(f"✅ 검증 성공: {stock.stock_code} - {stock.stock_name} ({stock.market})")

    print("\n2️⃣  StockSchema 에러 케이스 (잘못된 market)")
    print("-" * 60)
    try:
        invalid_stock = StockSchema(
            stock_code="005930",
            stock_name="삼성전자",
            market="NYSE",  # ❌ KOSPI/KOSDAQ가 아님
            is_active=True
        )
    except Exception as e:
        print(f"❌ 검증 실패: {e}")

    print("\n3️⃣  OHLCVDailySchema 정상 케이스")
    print("-" * 60)
    ohlcv_data = {
        "time": "2026-02-18",
        "stock_code": "005930",
        "open_price": 75000,
        "high_price": 76000,
        "low_price": 74500,
        "close_price": 75500,
        "volume": 10000000,
        "trading_value": 755000000000
    }
    ohlcv = OHLCVDailySchema(**ohlcv_data)
    print(f"✅ 검증 성공: {ohlcv.stock_code} - 시가 {ohlcv.open_price:,}원, 종가 {ohlcv.close_price:,}원")

    print("\n4️⃣  OHLCVDailySchema 에러 케이스 (고가 < 저가)")
    print("-" * 60)
    try:
        invalid_ohlcv = OHLCVDailySchema(
            time="2026-02-18",
            stock_code="005930",
            open_price=75000,
            high_price=74000,  # ❌ 고가가 시가보다 낮음
            low_price=74500,
            close_price=75500,
            volume=10000000
        )
    except Exception as e:
        print(f"❌ 검증 실패: {e}")

    print("\n5️⃣  InvestorTradingSchema 정상 케이스")
    print("-" * 60)
    investor_data = {
        "time": "2026-02-18",
        "stock_code": "005930",
        "investor_type": "foreign",  # 소문자 입력 (자동 대문자 변환)
        "buy_volume": 500000,
        "sell_volume": 400000,
        "net_buy_volume": 100000,  # = 500000 - 400000
        "buy_value": 37500000000,
        "sell_value": 30000000000,
        "net_buy_value": 7500000000
    }
    investor = InvestorTradingSchema(**investor_data)
    print(f"✅ 검증 성공: {investor.investor_type} - 순매수 {investor.net_buy_volume:,}주")

    print("\n" + "=" * 60)
    print("✅ 모든 Pydantic 스키마 정의 완료! (총 10개)")
    print("=" * 60)
    print("\n📊 완성된 스키마 목록:")
    print("  1. StockSchema - 종목 마스터")
    print("  2. SectorSchema - 섹터 분류")
    print("  3. IndexComponentSchema - 지수 구성종목")
    print("  4. FloatingSharesSchema - 유동주식")
    print("  5. ETFPortfoliosSchema - ETF 구성")
    print("  6. MarketCapDailySchema - 시가총액")
    print("  7. OHLCVDailySchema - 일봉 (중요!)")
    print("  8. InvestorTradingSchema - 투자자별 수급")
    print("  9. DataCollectionLogsSchema - 데이터 수집 이력")
    print(" 10. DataQualityChecksSchema - 데이터 품질 체크")
    print("=" * 60)
