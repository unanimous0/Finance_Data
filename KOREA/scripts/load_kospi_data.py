"""
KOSPI 데이터 적재
엑셀 파일에서 OHLCV, 시가총액, 투자자별 순매수 데이터를 DB에 적재
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
import psycopg2
from datetime import datetime
from config.settings import settings
from utils.logger import logger

def get_stock_mapping():
    """DB에서 종목명 → 종목코드 매핑 가져오기"""
    try:
        conn = psycopg2.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            dbname=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD
        )
        cursor = conn.cursor()

        cursor.execute("SELECT stock_code, stock_name FROM stocks")
        stocks = cursor.fetchall()

        cursor.close()
        conn.close()

        # 종목명 → 종목코드 딕셔너리
        mapping = {name: code for code, name in stocks}

        logger.info(f"✅ DB에서 {len(mapping)}개 종목 매핑 로드")
        return mapping

    except Exception as e:
        logger.error(f"❌ 종목 매핑 로드 실패: {e}")
        return {}

def load_excel_data(file_path):
    """엑셀 파일 읽기 및 구조 파싱"""
    logger.info("📂 엑셀 파일 읽는 중...")

    # 첫 번째 행을 헤더로 읽기 (종목명이 여기 있음)
    df = pd.read_excel(file_path, header=0)

    logger.info(f"   - 총 {len(df)}개 행 (헤더 포함)")
    logger.info(f"   - 총 {len(df.columns)}개 컬럼")

    # 첫 번째 데이터 행이 "시간/항목"이면 제거 (헤더 행)
    if df.iloc[0, 0] == "시간/항목":
        logger.info("   - 헤더 행 제거")
        df = df.iloc[1:].reset_index(drop=True)

    logger.info(f"   - 실제 데이터: {len(df)}개 행")

    # 날짜 역순 정렬 (오래된 날짜가 위로)
    df = df.iloc[::-1].reset_index(drop=True)

    logger.info(f"   - 날짜 정렬: {df.iloc[0, 0]} ~ {df.iloc[-1, 0]}")

    return df

def parse_stock_columns(df, stock_mapping):
    """종목별 데이터 파싱"""
    logger.info("\n📊 데이터 파싱 중...")

    ohlcv_data = []
    market_cap_data = []
    investor_data = []

    # 날짜 컬럼 제외하고 11개씩 묶어서 처리
    col_idx = 1
    stock_count = 0
    matched_count = 0

    while col_idx < len(df.columns):
        # 종목명은 컬럼명 자체
        stock_name = df.columns[col_idx]

        # 다음 종목으로 넘어가는 조건 체크
        if col_idx + 10 >= len(df.columns):
            break

        stock_count += 1

        # 종목코드 매핑
        stock_code = stock_mapping.get(stock_name)

        if not stock_code:
            logger.warning(f"⚠️  종목명 '{stock_name}' 매핑 실패 - 스킵")
            col_idx += 11
            continue

        matched_count += 1

        # 각 날짜별로 데이터 추출
        for idx, row in df.iterrows():
            date_val = row.iloc[0]

            # 날짜 변환
            if isinstance(date_val, str):
                if date_val == "시간/항목":
                    continue
                date = pd.to_datetime(date_val).date()
            else:
                date = pd.to_datetime(date_val).date()

            # 11개 컬럼 데이터
            try:
                open_price = row.iloc[col_idx]
                high_price = row.iloc[col_idx + 1]
                low_price = row.iloc[col_idx + 2]
                close_price = row.iloc[col_idx + 3]
                volume = row.iloc[col_idx + 4]
                trading_value = row.iloc[col_idx + 5]
                market_cap = row.iloc[col_idx + 6]
                retail_net = row.iloc[col_idx + 7]      # 개인
                institution_net = row.iloc[col_idx + 8] # 기관계
                foreign_net = row.iloc[col_idx + 9]     # 외국인
                pension_net = row.iloc[col_idx + 10]    # 연기금

                # NaN 체크 (데이터 없는 날짜 스킵)
                if pd.isna(close_price) or close_price == 0:
                    continue

                # OHLCV 데이터
                ohlcv_data.append({
                    'time': date,
                    'stock_code': stock_code,
                    'open_price': float(open_price) if not pd.isna(open_price) else None,
                    'high_price': float(high_price) if not pd.isna(high_price) else None,
                    'low_price': float(low_price) if not pd.isna(low_price) else None,
                    'close_price': float(close_price),
                    'volume': int(volume) if not pd.isna(volume) else 0,
                    'trading_value': int(trading_value) if not pd.isna(trading_value) else 0,
                })

                # 시가총액 데이터
                if not pd.isna(market_cap) and market_cap > 0:
                    market_cap_data.append({
                        'time': date,
                        'stock_code': stock_code,
                        'market_cap': int(market_cap),
                    })

                # 투자자별 순매수 데이터
                investors = [
                    ('RETAIL', retail_net),
                    ('INSTITUTION', institution_net),
                    ('FOREIGN', foreign_net),
                    ('PENSION', pension_net),
                ]

                for investor_type, net_value in investors:
                    if not pd.isna(net_value):
                        investor_data.append({
                            'time': date,
                            'stock_code': stock_code,
                            'investor_type': investor_type,
                            'net_buy_value': int(net_value),
                        })

            except Exception as e:
                logger.warning(f"⚠️  {stock_name} ({date}) 데이터 파싱 오류: {e}")
                continue

        # 다음 종목으로 (11개 컬럼 건너뛰기)
        col_idx += 11

        # 진행상황 출력 (100개마다)
        if stock_count % 100 == 0:
            logger.info(f"   진행중... {stock_count}개 종목 처리 (매칭: {matched_count}개)")

    logger.info(f"\n✅ 파싱 완료:")
    logger.info(f"   - 전체 종목: {stock_count}개")
    logger.info(f"   - 매칭된 종목: {matched_count}개")
    logger.info(f"   - OHLCV 데이터: {len(ohlcv_data):,}건")
    logger.info(f"   - 시가총액 데이터: {len(market_cap_data):,}건")
    logger.info(f"   - 투자자 데이터: {len(investor_data):,}건")

    return ohlcv_data, market_cap_data, investor_data

def insert_to_db(ohlcv_data, market_cap_data, investor_data):
    """DB에 데이터 삽입"""
    logger.info("\n💾 DB에 데이터 삽입 중...")

    try:
        conn = psycopg2.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            dbname=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD
        )
        cursor = conn.cursor()

        # 1. OHLCV 데이터 삽입
        logger.info("\n1️⃣ OHLCV 데이터 삽입 중...")

        ohlcv_sql = """
            INSERT INTO ohlcv_daily
            (time, stock_code, open_price, high_price, low_price, close_price, volume, trading_value)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (time, stock_code) DO UPDATE SET
                open_price = EXCLUDED.open_price,
                high_price = EXCLUDED.high_price,
                low_price = EXCLUDED.low_price,
                close_price = EXCLUDED.close_price,
                volume = EXCLUDED.volume,
                trading_value = EXCLUDED.trading_value
        """

        ohlcv_values = [
            (d['time'], d['stock_code'], d['open_price'], d['high_price'],
             d['low_price'], d['close_price'], d['volume'], d['trading_value'])
            for d in ohlcv_data
        ]

        cursor.executemany(ohlcv_sql, ohlcv_values)
        logger.success(f"   ✅ OHLCV: {len(ohlcv_values):,}건 삽입")

        # 2. 시가총액 데이터 삽입
        logger.info("\n2️⃣ 시가총액 데이터 삽입 중...")

        market_cap_sql = """
            INSERT INTO market_cap_daily
            (time, stock_code, market_cap)
            VALUES (%s, %s, %s)
            ON CONFLICT (time, stock_code) DO UPDATE SET
                market_cap = EXCLUDED.market_cap
        """

        market_cap_values = [
            (d['time'], d['stock_code'], d['market_cap'])
            for d in market_cap_data
        ]

        cursor.executemany(market_cap_sql, market_cap_values)
        logger.success(f"   ✅ 시가총액: {len(market_cap_values):,}건 삽입")

        # 3. 투자자별 데이터 삽입
        logger.info("\n3️⃣ 투자자별 순매수 데이터 삽입 중...")

        investor_sql = """
            INSERT INTO investor_trading
            (time, stock_code, investor_type, net_buy_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (time, stock_code, investor_type) DO UPDATE SET
                net_buy_value = EXCLUDED.net_buy_value
        """

        investor_values = [
            (d['time'], d['stock_code'], d['investor_type'], d['net_buy_value'])
            for d in investor_data
        ]

        cursor.executemany(investor_sql, investor_values)
        logger.success(f"   ✅ 투자자 데이터: {len(investor_values):,}건 삽입")

        # 커밋
        conn.commit()

        cursor.close()
        conn.close()

        logger.success("\n🎉 모든 데이터 삽입 완료!")

        return True

    except Exception as e:
        logger.error(f"❌ DB 삽입 실패: {e}")
        if conn:
            conn.rollback()
        return False

def main():
    """메인 함수"""
    logger.info("="*80)
    logger.info("🚀 KOSPI 데이터 적재 시작")
    logger.info("="*80)

    file_path = project_root / "raw_data" / "2-KOSPI 데이터.xlsx"

    if not file_path.exists():
        logger.error(f"❌ 파일 없음: {file_path}")
        return False

    # 1. 종목 매핑 로드
    stock_mapping = get_stock_mapping()
    if not stock_mapping:
        logger.error("❌ 종목 매핑 로드 실패")
        return False

    # 2. 엑셀 데이터 로드
    df = load_excel_data(file_path)

    # 3. 데이터 파싱
    ohlcv_data, market_cap_data, investor_data = parse_stock_columns(
        df, stock_mapping
    )

    if not ohlcv_data:
        logger.error("❌ 파싱된 데이터 없음")
        return False

    # 4. DB 삽입
    success = insert_to_db(ohlcv_data, market_cap_data, investor_data)

    if success:
        logger.success("\n✅ 데이터 적재 완료!")
        logger.info("\n📊 적재 완료 후 확인:")
        logger.info("   SELECT COUNT(*) FROM ohlcv_daily;")
        logger.info("   SELECT COUNT(*) FROM market_cap_daily;")
        logger.info("   SELECT COUNT(*) FROM investor_trading;")
    else:
        logger.error("\n❌ 데이터 적재 실패")

    return success

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
