"""
SQLAlchemy ORM 모델 정의

이 파일에서는 데이터베이스 테이블을 Python 클래스로 표현합니다.
각 클래스 = 하나의 테이블
"""

from datetime import datetime
from sqlalchemy import Column, String, Integer, Date, Boolean, TIMESTAMP, BigInteger, Numeric, Text, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

# Base 클래스 생성 (모든 모델의 부모 클래스)
# 이 Base를 상속받으면 SQLAlchemy가 "아, 이건 DB 테이블이구나!" 라고 인식함
Base = declarative_base()


# ==========================================
# 1. 가장 간단한 모델: Stock (종목 마스터)
# ==========================================

class Stock(Base):
    """
    종목 마스터 테이블 모델

    이 클래스는 'stocks' 테이블을 나타냅니다.
    각 인스턴스(객체)는 테이블의 한 행(row)입니다.
    """

    # 테이블 이름 지정 (DB에서 실제 테이블명)
    __tablename__ = "stocks"

    # 컬럼(열) 정의 - 각 줄이 테이블의 한 컬럼
    # Column(타입, 제약조건...)

    stock_code = Column(
        String(10),           # 타입: 최대 10글자 문자열
        primary_key=True,     # 제약: 이 컬럼이 Primary Key (고유 식별자)
        comment="종목코드"     # 설명 (선택사항)
    )

    stock_name = Column(
        String(100),          # 최대 100글자
        nullable=False,       # 제약: NULL 불가 (반드시 값이 있어야 함)
        comment="종목명"
    )

    standard_code = Column(
        String(12),           # 국제표준코드 (ISIN) 12자리
        unique=True,          # 제약: 고유값 (중복 불가)
        nullable=True,        # NULL 허용
        comment="국제표준코드 (ISIN)"
    )

    market = Column(
        String(10),
        nullable=True,        # NULL 허용으로 변경
        comment="시장구분 (KOSPI/KOSDAQ/ETF)"
    )

    sector_id = Column(
        Integer,              # 타입: 정수
        ForeignKey('sectors.sector_id'),  # ← Sector 테이블 참조!
        nullable=True,        # NULL 허용 (섹터 미분류 종목도 있을 수 있으니)
        comment="섹터 ID"
    )

    listing_date = Column(
        Date,                 # 타입: 날짜 (YYYY-MM-DD)
        nullable=True,
        comment="상장일"
    )

    delisting_date = Column(
        Date,
        nullable=True,
        comment="상장폐지일 (NULL = 상장 중)"
    )

    is_active = Column(
        Boolean,              # 타입: True/False
        default=True,         # 기본값: True (새로 만들면 자동으로 True)
        comment="활성 여부"
    )

    created_at = Column(
        TIMESTAMP,            # 타입: 날짜+시간
        default=datetime.now, # 기본값: 현재 시간 (자동 입력)
        comment="생성일시"
    )

    updated_at = Column(
        TIMESTAMP,
        default=datetime.now,
        onupdate=datetime.now,  # 수정될 때마다 자동으로 현재 시간으로 업데이트
        comment="수정일시"
    )

    # ==========================================
    # Relationship 정의
    # ==========================================

    # Sector와의 관계
    # self.sector → 이 종목의 섹터 객체
    sector = relationship(
        "Sector",
        back_populates="stocks"  # Sector 모델의 stocks와 연결
    )

    # __repr__: 이 객체를 print() 했을 때 어떻게 보일지 정의
    # 디버깅할 때 유용함
    def __repr__(self):
        return f"<Stock(code={self.stock_code}, name={self.stock_name}, market={self.market})>"


# ==========================================
# 2. 자기 참조 Foreign Key: Sector (섹터 분류)
# ==========================================

class Sector(Base):
    """
    섹터(업종) 분류 테이블 모델

    계층 구조를 표현:
    - parent_sector_id = NULL: 최상위 섹터 (예: IT, 금융)
    - parent_sector_id = 숫자: 하위 섹터 (예: 반도체는 IT의 하위)
    """

    __tablename__ = "sectors"

    sector_id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,   # 자동 증가 (1, 2, 3, ...)
        comment="섹터 ID"
    )

    sector_name = Column(
        String(100),
        nullable=False,
        comment="섹터명"
    )

    sector_code = Column(
        String(20),
        nullable=True,
        comment="섹터 코드 (예: IT001)"
    )

    # ★ 자기 참조 Foreign Key
    # 같은 테이블(sectors)의 sector_id를 가리킴!
    parent_sector_id = Column(
        Integer,
        ForeignKey('sectors.sector_id'),  # ← 자기 테이블 참조!
        nullable=True,                     # NULL = 최상위 섹터
        comment="상위 섹터 ID"
    )

    created_at = Column(
        TIMESTAMP,
        default=datetime.now,
        comment="생성일시"
    )

    # ==========================================
    # Relationship 정의
    # ==========================================

    # 1. 부모 섹터 접근
    # self.parent → 상위 섹터 객체
    parent = relationship(
        "Sector",                    # 같은 Sector 모델 참조
        remote_side=[sector_id],     # 부모쪽 컬럼 지정 (중요!)
        foreign_keys=[parent_sector_id],  # Foreign Key 명시
        backref="children"           # 역방향: parent.children → 자식 섹터들
    )

    # 2. 이 섹터에 속한 종목들
    # self.stocks → 이 섹터의 종목 리스트
    stocks = relationship(
        "Stock",
        back_populates="sector",     # Stock 모델의 sector와 연결
        lazy="dynamic"               # 필요할 때만 조회 (성능 최적화)
    )

    def __repr__(self):
        parent_name = self.parent.sector_name if self.parent else "최상위"
        return f"<Sector(id={self.sector_id}, name={self.sector_name}, parent={parent_name})>"


# ==========================================
# 3. Foreign Key 모델: IndexComponent (지수 구성종목)
# ==========================================

class IndexComponent(Base):
    """
    지수 구성종목 테이블 모델

    KOSPI200, KOSDAQ150 등 지수에 어떤 종목이 포함되는지 관리
    """

    __tablename__ = "index_components"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="ID"
    )

    index_name = Column(
        String(50),
        nullable=False,
        comment="지수명 (예: KOSPI200, KOSDAQ150)"
    )

    stock_code = Column(
        String(10),
        ForeignKey('stocks.stock_code'),  # ← Stock 참조
        nullable=False,
        comment="종목코드"
    )

    effective_date = Column(
        Date,
        nullable=False,
        comment="편입일"
    )

    end_date = Column(
        Date,
        nullable=True,  # NULL = 현재 편입 중
        comment="제외일 (NULL이면 현재 편입 중)"
    )

    created_at = Column(
        TIMESTAMP,
        default=datetime.now,
        comment="생성일시"
    )

    # Relationship
    stock = relationship("Stock", backref="index_memberships")

    def __repr__(self):
        status = "편입 중" if self.end_date is None else f"{self.end_date}에 제외"
        return f"<IndexComponent({self.index_name}, {self.stock_code}, {status})>"


# ==========================================
# 4. Foreign Key 모델: FloatingShares (유동주식)
# ==========================================

class FloatingShares(Base):
    """
    유동주식 테이블 모델

    종목별 총 상장주식수 및 유동주식수 관리
    """

    __tablename__ = "floating_shares"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="ID"
    )

    stock_code = Column(
        String(10),
        ForeignKey('stocks.stock_code'),  # ← Stock 참조
        nullable=False,
        comment="종목코드"
    )

    base_date = Column(
        Date,
        nullable=False,
        comment="기준일"
    )

    total_shares = Column(
        BigInteger,
        nullable=True,
        comment="총 상장주식수"
    )

    floating_shares = Column(
        BigInteger,
        nullable=True,
        comment="유동주식수 (실제 거래 가능한 주식 수)"
    )

    floating_ratio = Column(
        Numeric(5, 2),  # 소수점 2자리 (예: 65.43%)
        nullable=True,
        comment="유동비율 (%)"
    )

    created_at = Column(
        TIMESTAMP,
        default=datetime.now,
        comment="생성일시"
    )

    # Relationship
    stock = relationship("Stock", backref="floating_shares_history")

    def __repr__(self):
        return f"<FloatingShares({self.stock_code}, {self.base_date}, {self.floating_ratio}%)>"


# ==========================================
# 5. 같은 테이블 2번 참조: ETFPortfolios (ETF 구성)
# ==========================================

class ETFPortfolios(Base):
    """
    ETF 포트폴리오 테이블 모델

    특이점: Stock을 2번 참조!
    - etf_code: ETF 자체 (예: TIGER 반도체 ETF)
    - component_code: ETF에 포함된 종목 (예: 삼성전자)
    """

    __tablename__ = "etf_portfolios"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="ID"
    )

    etf_code = Column(
        String(10),
        ForeignKey('stocks.stock_code'),  # ← Stock 참조 (1번째)
        nullable=False,
        comment="ETF 종목코드"
    )

    component_code = Column(
        String(10),
        ForeignKey('stocks.stock_code'),  # ← Stock 참조 (2번째, 같은 테이블!)
        nullable=False,
        comment="구성 종목코드"
    )

    base_date = Column(
        Date,
        nullable=False,
        comment="기준일"
    )

    weight = Column(
        Numeric(7, 4),  # 소수점 4자리 (예: 12.3456%)
        nullable=True,
        comment="비중 (%)"
    )

    shares = Column(
        BigInteger,
        nullable=True,
        comment="보유 주식수"
    )

    created_at = Column(
        TIMESTAMP,
        default=datetime.now,
        comment="생성일시"
    )

    # ★ 중요: 같은 테이블을 2번 참조할 때는 foreign_keys 명시 필요!

    # ETF 자체 (예: TIGER 반도체)
    etf = relationship(
        "Stock",
        foreign_keys=[etf_code],  # ← 어떤 FK를 사용할지 명시!
        backref="etf_portfolios"
    )

    # ETF 구성 종목 (예: 삼성전자)
    component = relationship(
        "Stock",
        foreign_keys=[component_code],  # ← 어떤 FK를 사용할지 명시!
        backref="in_etfs"
    )

    def __repr__(self):
        return f"<ETFPortfolios(ETF={self.etf_code}, 구성={self.component_code}, 비중={self.weight}%)>"


# ==========================================
# 6. Hypertable 모델: MarketCapDaily (시가총액)
# ==========================================

class MarketCapDaily(Base):
    """
    일별 시가총액 테이블 모델

    ★ TimescaleDB Hypertable (시계열 데이터)
    - DB에서 자동 파티셔닝됨 (time 기준)
    - SQLAlchemy에서는 일반 테이블처럼 정의
    """

    __tablename__ = "market_cap_daily"

    # ★ 복합 Primary Key (time + stock_code)
    # Hypertable은 시간 컬럼을 포함한 복합키 사용
    time = Column(
        Date,
        primary_key=True,  # ← 복합키 1번째
        nullable=False,
        comment="날짜"
    )

    stock_code = Column(
        String(10),
        primary_key=True,  # ← 복합키 2번째
        nullable=False,
        comment="종목코드"
    )

    market_cap = Column(
        BigInteger,
        nullable=True,
        comment="시가총액 (원)"
    )

    shares_outstanding = Column(
        BigInteger,
        nullable=True,
        comment="상장주식수"
    )

    created_at = Column(
        TIMESTAMP,
        default=datetime.now,
        comment="생성일시"
    )

    def __repr__(self):
        cap_billion = self.market_cap // 100000000 if self.market_cap else 0
        return f"<MarketCapDaily({self.stock_code}, {self.time}, {cap_billion:,}억원)>"


# ==========================================
# 7. Hypertable 모델: OHLCVDaily (일봉)
# ==========================================

class OHLCVDaily(Base):
    """
    일별 OHLCV 테이블 모델

    OHLCV = Open, High, Low, Close, Volume
    주식 차트의 기본 데이터
    """

    __tablename__ = "ohlcv_daily"

    # 복합 Primary Key
    time = Column(
        Date,
        primary_key=True,
        nullable=False,
        comment="날짜"
    )

    stock_code = Column(
        String(10),
        primary_key=True,
        nullable=False,
        comment="종목코드"
    )

    open_price = Column(
        Integer,
        nullable=True,
        comment="시가 (원)"
    )

    high_price = Column(
        Integer,
        nullable=True,
        comment="고가 (원)"
    )

    low_price = Column(
        Integer,
        nullable=True,
        comment="저가 (원)"
    )

    close_price = Column(
        Integer,
        nullable=True,
        comment="종가 (원)"
    )

    volume = Column(
        BigInteger,
        nullable=True,
        comment="거래량 (주)"
    )

    trading_value = Column(
        BigInteger,
        nullable=True,
        comment="거래대금 (원)"
    )

    created_at = Column(
        TIMESTAMP,
        default=datetime.now,
        comment="생성일시"
    )

    def __repr__(self):
        return f"<OHLCVDaily({self.stock_code}, {self.time}, 종가={self.close_price:,}원)>"


# ==========================================
# 8. Hypertable 모델: InvestorTrading (투자자별 수급)
# ==========================================

class InvestorTrading(Base):
    """
    투자자별 수급 테이블 모델

    외국인, 기관, 개인, 연기금의 매매 데이터
    """

    __tablename__ = "investor_trading"

    # 복합 Primary Key (3개!)
    # time + stock_code + investor_type
    time = Column(
        Date,
        primary_key=True,
        nullable=False,
        comment="날짜"
    )

    stock_code = Column(
        String(10),
        primary_key=True,
        nullable=False,
        comment="종목코드"
    )

    investor_type = Column(
        String(20),
        primary_key=True,  # ← 3번째 Primary Key
        nullable=False,
        comment="투자자 유형 (FOREIGN/INSTITUTION/RETAIL/PENSION)"
    )

    # 순매수 데이터
    net_buy_volume = Column(
        BigInteger,
        nullable=True,
        comment="순매수 수량 (주) = 매수 - 매도"
    )

    net_buy_value = Column(
        BigInteger,
        nullable=True,
        comment="순매수 금액 (원) = 매수금액 - 매도금액"
    )

    # 세부 데이터
    buy_volume = Column(
        BigInteger,
        nullable=True,
        comment="매수 수량 (주)"
    )

    sell_volume = Column(
        BigInteger,
        nullable=True,
        comment="매도 수량 (주)"
    )

    buy_value = Column(
        BigInteger,
        nullable=True,
        comment="매수 금액 (원)"
    )

    sell_value = Column(
        BigInteger,
        nullable=True,
        comment="매도 금액 (원)"
    )

    created_at = Column(
        TIMESTAMP,
        default=datetime.now,
        comment="생성일시"
    )

    def __repr__(self):
        net_billion = self.net_buy_value // 100000000 if self.net_buy_value else 0
        sign = "+" if net_billion >= 0 else ""
        return f"<InvestorTrading({self.stock_code}, {self.time}, {self.investor_type}, {sign}{net_billion:,}억원)>"


# ==========================================
# 9. 모니터링 테이블: DataCollectionLogs
# ==========================================

class DataCollectionLogs(Base):
    """
    데이터 수집 이력 테이블

    언제, 어떤 데이터를, 어디서, 얼마나 수집했는지 기록
    """

    __tablename__ = "data_collection_logs"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="ID"
    )

    data_type = Column(
        String(50),
        nullable=False,
        comment="데이터 종류 (OHLCV, INVESTOR, MARKET_CAP 등)"
    )

    collection_date = Column(
        Date,
        nullable=False,
        comment="수집 대상 날짜"
    )

    source = Column(
        String(50),
        nullable=True,
        comment="데이터 소스 (INFOMAX, HTS, CRAWLING 등)"
    )

    status = Column(
        String(20),
        nullable=True,
        comment="상태 (SUCCESS, FAILED, PARTIAL)"
    )

    records_count = Column(
        Integer,
        nullable=True,
        comment="수집된 레코드 수"
    )

    error_message = Column(
        Text,
        nullable=True,
        comment="에러 메시지 (실패시)"
    )

    started_at = Column(
        TIMESTAMP,
        nullable=True,
        comment="수집 시작 시각"
    )

    completed_at = Column(
        TIMESTAMP,
        nullable=True,
        comment="수집 완료 시각"
    )

    created_at = Column(
        TIMESTAMP,
        default=datetime.now,
        comment="생성일시"
    )

    def __repr__(self):
        return f"<DataCollectionLogs({self.data_type}, {self.collection_date}, {self.status}, {self.records_count}건)>"


# ==========================================
# 10. 모니터링 테이블: DataQualityChecks
# ==========================================

class DataQualityChecks(Base):
    """
    데이터 품질 체크 테이블

    데이터 정합성, 이상치 등을 체크한 결과 저장
    """

    __tablename__ = "data_quality_checks"

    id = Column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="ID"
    )

    table_name = Column(
        String(50),
        nullable=False,
        comment="체크 대상 테이블명"
    )

    check_date = Column(
        Date,
        nullable=False,
        comment="체크 날짜"
    )

    check_type = Column(
        String(50),
        nullable=True,
        comment="체크 유형 (NULL_CHECK, DUPLICATE_CHECK, RANGE_CHECK 등)"
    )

    issue_count = Column(
        Integer,
        nullable=True,
        comment="발견된 이슈 개수"
    )

    details = Column(
        Text,  # JSONB는 PostgreSQL 전용이므로 Text 사용
        nullable=True,
        comment="상세 정보 (JSON 형식)"
    )

    created_at = Column(
        TIMESTAMP,
        default=datetime.now,
        comment="생성일시"
    )

    def __repr__(self):
        return f"<DataQualityChecks({self.table_name}, {self.check_date}, {self.check_type}, 이슈={self.issue_count}건)>"


# 모델이 제대로 만들어졌는지 확인하는 테스트 코드
if __name__ == "__main__":
    print("=" * 60)
    print("📚 모델 정의 테스트")
    print("=" * 60)

    print("\n1️⃣  Stock 모델")
    print("-" * 60)
    samsung = Stock(
        stock_code="005930",
        stock_name="삼성전자",
        market="KOSPI",
        sector_id=1,
        listing_date=datetime(1975, 6, 11).date()
    )
    print(f"생성: {samsung}")

    print("\n2️⃣  Sector 모델")
    print("-" * 60)

    # 최상위 섹터 (parent 없음)
    it_sector = Sector(
        sector_id=1,
        sector_name="IT산업",
        sector_code="IT",
        parent_sector_id=None  # 최상위
    )
    print(f"최상위 섹터: {it_sector}")

    # 하위 섹터 (parent 있음)
    semiconductor = Sector(
        sector_id=2,
        sector_name="반도체",
        sector_code="IT001",
        parent_sector_id=1  # IT산업의 하위
    )
    print(f"하위 섹터: {semiconductor}")

    print("\n3️⃣  자기 참조 Foreign Key 설명")
    print("-" * 60)
    print(f"parent_sector_id = {semiconductor.parent_sector_id}")
    print(f"→ sectors 테이블의 sector_id={semiconductor.parent_sector_id}를 가리킴")
    print(f"→ 즉, '{it_sector.sector_name}' 섹터가 부모")

    print("\n4️⃣  IndexComponent 모델")
    print("-" * 60)
    kospi200 = IndexComponent(
        index_name="KOSPI200",
        stock_code="005930",
        effective_date=datetime(2020, 1, 1).date(),
        end_date=None  # 현재 편입 중
    )
    print(f"생성: {kospi200}")

    print("\n5️⃣  FloatingShares 모델")
    print("-" * 60)
    floating = FloatingShares(
        stock_code="005930",
        base_date=datetime(2026, 2, 18).date(),
        total_shares=5969783000,
        floating_shares=4000000000,
        floating_ratio=67.02
    )
    print(f"생성: {floating}")

    print("\n6️⃣  ETFPortfolios 모델 (같은 테이블 2번 참조!)")
    print("-" * 60)
    etf_component = ETFPortfolios(
        etf_code="102110",  # TIGER 반도체 ETF
        component_code="005930",  # 삼성전자
        base_date=datetime(2026, 2, 18).date(),
        weight=25.5,
        shares=1000000
    )
    print(f"생성: {etf_component}")
    print(f"→ ETF: {etf_component.etf_code} (TIGER 반도체)")
    print(f"→ 구성종목: {etf_component.component_code} (삼성전자)")
    print(f"→ 비중: {etf_component.weight}%")

    print("\n" + "=" * 60)
    print("⏰ Hypertable 모델 (시계열 데이터)")
    print("=" * 60)

    print("\n7️⃣  MarketCapDaily 모델")
    print("-" * 60)
    market_cap = MarketCapDaily(
        time=datetime(2026, 2, 18).date(),
        stock_code="005930",
        market_cap=450000000000000,  # 450조원
        shares_outstanding=5969783000
    )
    print(f"생성: {market_cap}")

    print("\n8️⃣  OHLCVDaily 모델")
    print("-" * 60)
    ohlcv = OHLCVDaily(
        time=datetime(2026, 2, 18).date(),
        stock_code="005930",
        open_price=75000,
        high_price=76000,
        low_price=74500,
        close_price=75500,
        volume=10000000,
        trading_value=755000000000
    )
    print(f"생성: {ohlcv}")
    print(f"→ 시가: {ohlcv.open_price:,}원")
    print(f"→ 고가: {ohlcv.high_price:,}원")
    print(f"→ 저가: {ohlcv.low_price:,}원")
    print(f"→ 종가: {ohlcv.close_price:,}원")
    print(f"→ 거래량: {ohlcv.volume:,}주")

    print("\n9️⃣  InvestorTrading 모델 (복합 PK 3개!)")
    print("-" * 60)
    foreign_trading = InvestorTrading(
        time=datetime(2026, 2, 18).date(),
        stock_code="005930",
        investor_type="FOREIGN",
        net_buy_volume=100000,
        net_buy_value=7500000000,  # 75억원
        buy_volume=500000,
        sell_volume=400000,
        buy_value=37500000000,
        sell_value=30000000000
    )
    print(f"생성: {foreign_trading}")
    print(f"→ Primary Key: (time, stock_code, investor_type)")
    print(f"→ 순매수: {foreign_trading.net_buy_volume:,}주")
    print(f"→ 순매수금액: {foreign_trading.net_buy_value:,}원")

    print("\n🔟 모니터링 모델")
    print("-" * 60)
    log = DataCollectionLogs(
        data_type="OHLCV",
        collection_date=datetime(2026, 2, 18).date(),
        source="INFOMAX",
        status="SUCCESS",
        records_count=3000
    )
    print(f"수집 로그: {log}")

    quality = DataQualityChecks(
        table_name="ohlcv_daily",
        check_date=datetime(2026, 2, 18).date(),
        check_type="NULL_CHECK",
        issue_count=0
    )
    print(f"품질 체크: {quality}")

    print("\n" + "=" * 60)
    print("✅ 모든 모델 정의 완료! (총 10개)")
    print("=" * 60)
    print("\n📊 완성된 모델 목록:")
    print("  1. Stock - 종목 마스터")
    print("  2. Sector - 섹터 분류 (자기 참조)")
    print("  3. IndexComponent - 지수 구성종목")
    print("  4. FloatingShares - 유동주식")
    print("  5. ETFPortfolios - ETF 구성 (같은 테이블 2번 참조)")
    print("  6. MarketCapDaily - 시가총액 (Hypertable)")
    print("  7. OHLCVDaily - 일봉 (Hypertable)")
    print("  8. InvestorTrading - 투자자별 수급 (Hypertable)")
    print("  9. DataCollectionLogs - 데이터 수집 이력")
    print(" 10. DataQualityChecks - 데이터 품질 체크")
    print("=" * 60)
