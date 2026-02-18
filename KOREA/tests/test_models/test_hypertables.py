"""
Hypertable 모델 테스트

시계열 데이터 모델 (OHLCVDaily, InvestorTrading, MarketCapDaily) 테스트
복합 Primary Key 동작 확인
"""

import pytest
from datetime import date, timedelta
from sqlalchemy.exc import IntegrityError

from database.models import Stock, OHLCVDaily, InvestorTrading, MarketCapDaily


class TestOHLCVDailyCRUD:
    """OHLCVDaily 모델 CRUD 테스트"""

    def test_create_ohlcv(self, db_session, sample_stock):
        """CREATE: OHLCV 데이터 생성"""
        # Given: OHLCV 데이터 준비
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

        # When: DB에 저장
        db_session.add(ohlcv)
        db_session.commit()
        db_session.refresh(ohlcv)

        # Then: 저장된 데이터 확인
        assert ohlcv.stock_code == "005930"
        assert ohlcv.open_price == 75000
        assert ohlcv.close_price == 75500

    def test_read_ohlcv(self, db_session, sample_ohlcv):
        """READ: OHLCV 데이터 조회"""
        # When: 복합키로 조회 (time + stock_code)
        ohlcv = db_session.query(OHLCVDaily).filter_by(
            time=date(2026, 2, 18),
            stock_code="005930"
        ).first()

        # Then: 조회 성공
        assert ohlcv is not None
        assert ohlcv.close_price == 75500

    def test_update_ohlcv(self, db_session, sample_ohlcv):
        """UPDATE: OHLCV 데이터 수정"""
        # When: 종가 수정
        sample_ohlcv.close_price = 76000
        db_session.commit()
        db_session.refresh(sample_ohlcv)

        # Then: 변경사항 반영 확인
        ohlcv = db_session.query(OHLCVDaily).filter_by(
            time=date(2026, 2, 18),
            stock_code="005930"
        ).first()
        assert ohlcv.close_price == 76000

    def test_delete_ohlcv(self, db_session, sample_ohlcv):
        """DELETE: OHLCV 데이터 삭제"""
        # When: 데이터 삭제
        db_session.delete(sample_ohlcv)
        db_session.commit()

        # Then: 삭제 확인
        ohlcv = db_session.query(OHLCVDaily).filter_by(
            time=date(2026, 2, 18),
            stock_code="005930"
        ).first()
        assert ohlcv is None

    def test_composite_primary_key_uniqueness(self, db_session, sample_stock):
        """복합 Primary Key 고유성 테스트"""
        # Given: 첫 번째 데이터 생성
        ohlcv1 = OHLCVDaily(
            time=date(2026, 2, 18),
            stock_code=sample_stock.stock_code,
            open_price=75000,
            close_price=75500,
            volume=10000000
        )
        db_session.add(ohlcv1)
        db_session.commit()

        # When: 같은 (time, stock_code) 조합으로 중복 생성 시도
        ohlcv2 = OHLCVDaily(
            time=date(2026, 2, 18),  # 같은 날짜
            stock_code=sample_stock.stock_code,  # 같은 종목
            open_price=76000,
            close_price=76500,
            volume=5000000
        )

        # Then: IntegrityError 발생 (복합키 중복)
        with pytest.raises(IntegrityError):
            db_session.add(ohlcv2)
            db_session.commit()


class TestOHLCVDailyQueries:
    """OHLCVDaily 모델 복잡한 쿼리 테스트"""

    def test_query_by_stock_code(self, db_session, sample_stock):
        """특정 종목의 OHLCV 데이터 조회"""
        # Given: 여러 날짜의 데이터 추가
        dates = [date(2026, 2, 17), date(2026, 2, 18), date(2026, 2, 19)]
        for d in dates:
            ohlcv = OHLCVDaily(
                time=d,
                stock_code=sample_stock.stock_code,
                open_price=75000,
                close_price=75500,
                volume=10000000
            )
            db_session.add(ohlcv)
        db_session.commit()

        # When: 종목코드로 모든 데이터 조회
        ohlcv_list = db_session.query(OHLCVDaily).filter_by(
            stock_code=sample_stock.stock_code
        ).order_by(OHLCVDaily.time).all()

        # Then: 3개 데이터 모두 조회됨
        assert len(ohlcv_list) == 3
        assert ohlcv_list[0].time == date(2026, 2, 17)
        assert ohlcv_list[2].time == date(2026, 2, 19)

    def test_query_by_date_range(self, db_session, sample_stock):
        """기간별 OHLCV 데이터 조회"""
        # Given: 1주일치 데이터 추가
        start_date = date(2026, 2, 10)
        for i in range(7):
            d = start_date + timedelta(days=i)
            ohlcv = OHLCVDaily(
                time=d,
                stock_code=sample_stock.stock_code,
                open_price=75000 + i * 100,
                close_price=75500 + i * 100,
                volume=10000000
            )
            db_session.add(ohlcv)
        db_session.commit()

        # When: 특정 기간 데이터 조회 (2/12 ~ 2/14)
        ohlcv_list = db_session.query(OHLCVDaily).filter(
            OHLCVDaily.stock_code == sample_stock.stock_code,
            OHLCVDaily.time >= date(2026, 2, 12),
            OHLCVDaily.time <= date(2026, 2, 14)
        ).order_by(OHLCVDaily.time).all()

        # Then: 3일치 데이터 반환
        assert len(ohlcv_list) == 3
        assert ohlcv_list[0].time == date(2026, 2, 12)
        assert ohlcv_list[2].time == date(2026, 2, 14)

    def test_calculate_price_change(self, db_session, sample_stock):
        """가격 변동 계산 테스트"""
        # Given: 2일치 데이터 추가
        yesterday = OHLCVDaily(
            time=date(2026, 2, 17),
            stock_code=sample_stock.stock_code,
            close_price=75000,
            volume=10000000
        )
        today = OHLCVDaily(
            time=date(2026, 2, 18),
            stock_code=sample_stock.stock_code,
            close_price=76500,  # +1500원
            volume=12000000
        )
        db_session.add_all([yesterday, today])
        db_session.commit()

        # When: 전일 대비 계산
        price_change = today.close_price - yesterday.close_price
        change_rate = (price_change / yesterday.close_price) * 100

        # Then: 올바른 계산
        assert price_change == 1500
        assert round(change_rate, 2) == 2.0  # +2%


class TestInvestorTradingCRUD:
    """InvestorTrading 모델 CRUD 테스트"""

    def test_create_investor_trading(self, db_session, sample_stock):
        """CREATE: 투자자별 수급 데이터 생성"""
        # Given: 투자자별 수급 데이터 준비
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

        # When: DB에 저장
        db_session.add(trading)
        db_session.commit()
        db_session.refresh(trading)

        # Then: 저장된 데이터 확인
        assert trading.stock_code == "005930"
        assert trading.investor_type == "FOREIGN"
        assert trading.net_buy_volume == 100000

    def test_triple_composite_primary_key(self, db_session, sample_stock):
        """3개 컬럼 복합 Primary Key 테스트"""
        # Given: 같은 날짜, 같은 종목, 다른 투자자 유형으로 여러 데이터 생성
        investor_types = ["FOREIGN", "INSTITUTION", "RETAIL", "PENSION"]

        for investor_type in investor_types:
            trading = InvestorTrading(
                time=date(2026, 2, 18),
                stock_code=sample_stock.stock_code,
                investor_type=investor_type,
                net_buy_volume=100000
            )
            db_session.add(trading)

        db_session.commit()

        # When: 조회
        trading_list = db_session.query(InvestorTrading).filter_by(
            time=date(2026, 2, 18),
            stock_code=sample_stock.stock_code
        ).all()

        # Then: 4개 투자자 유형 모두 저장됨
        assert len(trading_list) == 4
        saved_types = [t.investor_type for t in trading_list]
        assert set(saved_types) == set(investor_types)

    def test_duplicate_composite_key(self, db_session, sample_stock):
        """복합키 중복 테스트"""
        # Given: 첫 번째 데이터 생성
        trading1 = InvestorTrading(
            time=date(2026, 2, 18),
            stock_code=sample_stock.stock_code,
            investor_type="FOREIGN",
            net_buy_volume=100000
        )
        db_session.add(trading1)
        db_session.commit()

        # When: 같은 (time, stock_code, investor_type) 조합으로 중복 생성
        trading2 = InvestorTrading(
            time=date(2026, 2, 18),  # 같은 날짜
            stock_code=sample_stock.stock_code,  # 같은 종목
            investor_type="FOREIGN",  # 같은 투자자 유형
            net_buy_volume=200000
        )

        # Then: IntegrityError 발생 (복합키 중복)
        with pytest.raises(IntegrityError):
            db_session.add(trading2)
            db_session.commit()


class TestInvestorTradingQueries:
    """InvestorTrading 모델 복잡한 쿼리 테스트"""

    def test_query_by_investor_type(self, db_session, sample_stock):
        """투자자 유형별 조회"""
        # Given: 여러 투자자 유형 데이터 추가
        investor_data = [
            ("FOREIGN", 100000),
            ("INSTITUTION", -50000),
            ("RETAIL", -30000),
            ("PENSION", -20000)
        ]

        for investor_type, net_volume in investor_data:
            trading = InvestorTrading(
                time=date(2026, 2, 18),
                stock_code=sample_stock.stock_code,
                investor_type=investor_type,
                net_buy_volume=net_volume
            )
            db_session.add(trading)

        db_session.commit()

        # When: 외국인 데이터만 조회
        foreign_trading = db_session.query(InvestorTrading).filter_by(
            stock_code=sample_stock.stock_code,
            investor_type="FOREIGN"
        ).first()

        # Then: 외국인 데이터만 반환
        assert foreign_trading.investor_type == "FOREIGN"
        assert foreign_trading.net_buy_volume == 100000

    def test_sum_net_buy_by_investor(self, db_session, sample_stock):
        """투자자별 누적 순매수 합계"""
        # Given: 1주일치 외국인 수급 데이터
        start_date = date(2026, 2, 10)
        net_volumes = [100000, -50000, 200000, -30000, 150000]  # 총 370000

        for i, net_volume in enumerate(net_volumes):
            d = start_date + timedelta(days=i)
            trading = InvestorTrading(
                time=d,
                stock_code=sample_stock.stock_code,
                investor_type="FOREIGN",
                net_buy_volume=net_volume
            )
            db_session.add(trading)

        db_session.commit()

        # When: 외국인 누적 순매수 합계 계산
        from sqlalchemy import func
        total_net_buy = db_session.query(
            func.sum(InvestorTrading.net_buy_volume)
        ).filter(
            InvestorTrading.stock_code == sample_stock.stock_code,
            InvestorTrading.investor_type == "FOREIGN",
            InvestorTrading.time >= start_date
        ).scalar()

        # Then: 올바른 합계 반환
        assert total_net_buy == 370000


class TestMarketCapDaily:
    """MarketCapDaily 모델 테스트"""

    def test_create_market_cap(self, db_session, sample_stock):
        """CREATE: 시가총액 데이터 생성"""
        # Given: 시가총액 데이터 준비
        market_cap = MarketCapDaily(
            time=date(2026, 2, 18),
            stock_code=sample_stock.stock_code,
            market_cap=450000000000000,  # 450조원
            shares_outstanding=5969783000
        )

        # When: DB에 저장
        db_session.add(market_cap)
        db_session.commit()
        db_session.refresh(market_cap)

        # Then: 저장된 데이터 확인
        assert market_cap.market_cap == 450000000000000

    def test_market_cap_calculation(self, db_session, sample_stock):
        """시가총액 = 종가 × 상장주식수 검증"""
        # Given: OHLCV 데이터 (종가 75,500원)
        ohlcv = OHLCVDaily(
            time=date(2026, 2, 18),
            stock_code=sample_stock.stock_code,
            close_price=75500,
            volume=10000000
        )
        db_session.add(ohlcv)

        # 시가총액 데이터 (상장주식수 5,969,783,000주)
        shares = 5969783000
        expected_market_cap = 75500 * shares  # = 450,748,615,500,000원

        market_cap = MarketCapDaily(
            time=date(2026, 2, 18),
            stock_code=sample_stock.stock_code,
            market_cap=expected_market_cap,
            shares_outstanding=shares
        )
        db_session.add(market_cap)
        db_session.commit()

        # When: 조회 후 계산 검증
        saved_market_cap = db_session.query(MarketCapDaily).filter_by(
            time=date(2026, 2, 18),
            stock_code=sample_stock.stock_code
        ).first()

        # Then: 올바른 계산
        calculated = saved_market_cap.market_cap / saved_market_cap.shares_outstanding
        assert int(calculated) == ohlcv.close_price


class TestHypertableIntegration:
    """Hypertable 모델들의 통합 테스트"""

    def test_complete_daily_data(self, db_session, sample_stock):
        """하루치 모든 데이터 입력 (OHLCV + 투자자별 수급 + 시가총액)"""
        # Given: 하루치 완전한 데이터 준비
        target_date = date(2026, 2, 18)

        # OHLCV
        ohlcv = OHLCVDaily(
            time=target_date,
            stock_code=sample_stock.stock_code,
            open_price=75000,
            high_price=76000,
            low_price=74500,
            close_price=75500,
            volume=10000000,
            trading_value=755000000000
        )

        # 투자자별 수급 (4개 유형)
        investor_data = [
            ("FOREIGN", 100000),
            ("INSTITUTION", -50000),
            ("RETAIL", -30000),
            ("PENSION", -20000)
        ]
        investor_trades = []
        for investor_type, net_volume in investor_data:
            trading = InvestorTrading(
                time=target_date,
                stock_code=sample_stock.stock_code,
                investor_type=investor_type,
                net_buy_volume=net_volume
            )
            investor_trades.append(trading)

        # 시가총액
        market_cap = MarketCapDaily(
            time=target_date,
            stock_code=sample_stock.stock_code,
            market_cap=450000000000000,
            shares_outstanding=5969783000
        )

        # When: 모든 데이터 저장
        db_session.add(ohlcv)
        db_session.add_all(investor_trades)
        db_session.add(market_cap)
        db_session.commit()

        # Then: 모든 데이터 조회 가능
        saved_ohlcv = db_session.query(OHLCVDaily).filter_by(
            time=target_date, stock_code=sample_stock.stock_code
        ).first()

        saved_trades = db_session.query(InvestorTrading).filter_by(
            time=target_date, stock_code=sample_stock.stock_code
        ).all()

        saved_market_cap = db_session.query(MarketCapDaily).filter_by(
            time=target_date, stock_code=sample_stock.stock_code
        ).first()

        assert saved_ohlcv is not None
        assert len(saved_trades) == 4
        assert saved_market_cap is not None
