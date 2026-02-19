"""
temp 폴더 CSV 파일에서 전체 데이터 적재
KOSPI + KOSDAQ + ETF 전체 시장 데이터 (2022-01-03 ~ 2026-02-13)
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
    """DB에서 종목명 → 종목코드 매핑"""
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

        logger.info(f"✅ DB에서 {len(mapping):,}개 종목 매핑 로드")
        return mapping

    except Exception as e:
        logger.error(f"❌ 종목 매핑 로드 실패: {e}")
        return {}

def load_csv_pivot_to_list(file_path, stock_mapping):
    """
    Pivot 형식 CSV를 리스트로 변환 (최적화 버전)
    행=날짜, 열=종목명 → [(날짜, 종목코드, 값), ...]
    """
    df = pd.read_csv(file_path)

    # 첫 번째 컬럼은 날짜
    date_col = df.columns[0]

    # 날짜를 인덱스로 설정하고 melt로 unpivot
    df[date_col] = pd.to_datetime(df[date_col])
    df_melted = df.melt(id_vars=[date_col], var_name='stock_name', value_name='value')

    # NaN 제거
    df_melted = df_melted.dropna(subset=['value'])

    # 종목명 → 종목코드 매핑
    df_melted['stock_code'] = df_melted['stock_name'].map(stock_mapping)

    # 매핑 통계
    matched_stocks = df_melted['stock_code'].notna().sum()
    total_stocks = len(df.columns) - 1
    unmatched_stocks = total_stocks - len(df_melted[df_melted['stock_code'].notna()]['stock_name'].unique())

    # 매칭되지 않은 종목 제거
    df_melted = df_melted.dropna(subset=['stock_code'])

    # 날짜를 date 형식으로 변환
    df_melted['date'] = df_melted[date_col].dt.date

    # 리스트로 변환
    data_list = df_melted[['date', 'stock_code', 'value']].to_dict('records')

    matched_stock_count = len(df_melted['stock_code'].unique())

    return data_list, matched_stock_count, unmatched_stocks

def load_ohlcv_data(stock_mapping, temp_folder):
    """OHLCV 데이터 적재"""
    logger.info("\n" + "="*80)
    logger.info("📊 OHLCV 데이터 적재 시작")
    logger.info("="*80)

    # 6개 파일 매핑
    files = {
        '1_시가.csv': 'open_price',
        '2_고가.csv': 'high_price',
        '3_저가.csv': 'low_price',
        '4_현재가.csv': 'close_price',
        '5_거래량.csv': 'volume',
        '6_거래대금.csv': 'trading_value',
    }

    # 데이터 딕셔너리: {(date, stock_code): {open: X, high: Y, ...}}
    ohlcv_dict = {}

    for filename, field_name in files.items():
        file_path = temp_folder / filename
        logger.info(f"\n📂 {filename} 읽는 중...")

        data_list, matched, unmatched = load_csv_pivot_to_list(file_path, stock_mapping)

        logger.info(f"   매칭: {matched:,}개 종목, 미매칭: {unmatched:,}개")
        logger.info(f"   데이터: {len(data_list):,}건")

        # 딕셔너리에 추가
        for item in data_list:
            key = (item['date'], item['stock_code'])
            if key not in ohlcv_dict:
                ohlcv_dict[key] = {}
            ohlcv_dict[key][field_name] = item['value']

    logger.info(f"\n✅ 전체 OHLCV 레코드: {len(ohlcv_dict):,}개")

    # DB 삽입
    logger.info("\n💾 DB에 삽입 중...")

    try:
        conn = psycopg2.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            dbname=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD
        )
        cursor = conn.cursor()

        sql = """
            INSERT INTO ohlcv_daily
            (time, stock_code, open_price, high_price, low_price, close_price, volume, trading_value)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """

        values = [
            (
                key[0],  # date
                key[1],  # stock_code
                int(data.get('open_price', 0)) if pd.notna(data.get('open_price')) else None,
                int(data.get('high_price', 0)) if pd.notna(data.get('high_price')) else None,
                int(data.get('low_price', 0)) if pd.notna(data.get('low_price')) else None,
                int(data.get('close_price', 0)) if pd.notna(data.get('close_price')) else None,
                int(data.get('volume', 0)) if pd.notna(data.get('volume')) else None,
                int(data.get('trading_value', 0)) if pd.notna(data.get('trading_value')) else None,
            )
            for key, data in ohlcv_dict.items()
            if data.get('close_price') is not None  # 종가가 있는 것만
        ]

        cursor.executemany(sql, values)
        conn.commit()

        logger.success(f"✅ OHLCV: {len(values):,}건 삽입 완료")

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        logger.error(f"❌ DB 삽입 실패: {e}")
        return False

def load_market_cap_data(stock_mapping, temp_folder):
    """시가총액 데이터 적재"""
    logger.info("\n" + "="*80)
    logger.info("📊 시가총액 데이터 적재 시작")
    logger.info("="*80)

    file_path = temp_folder / '7_시가총액.csv'

    logger.info(f"📂 {file_path.name} 읽는 중...")
    data_list, matched, unmatched = load_csv_pivot_to_list(file_path, stock_mapping)

    logger.info(f"   매칭: {matched:,}개 종목, 미매칭: {unmatched:,}개")
    logger.info(f"   데이터: {len(data_list):,}건")

    # DB 삽입
    logger.info("\n💾 DB에 삽입 중...")

    try:
        conn = psycopg2.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            dbname=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD
        )
        cursor = conn.cursor()

        sql = """
            INSERT INTO market_cap_daily
            (time, stock_code, market_cap)
            VALUES (%s, %s, %s)
        """

        values = [
            (item['date'], item['stock_code'], int(item['value']))
            for item in data_list
            if pd.notna(item['value']) and item['value'] > 0
        ]

        cursor.executemany(sql, values)
        conn.commit()

        logger.success(f"✅ 시가총액: {len(values):,}건 삽입 완료")

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        logger.error(f"❌ DB 삽입 실패: {e}")
        return False

def load_investor_data(stock_mapping, temp_folder):
    """투자자별 순매수 데이터 적재"""
    logger.info("\n" + "="*80)
    logger.info("📊 투자자 데이터 적재 시작")
    logger.info("="*80)

    files = {
        '8_순매수거래대금_외인.csv': 'FOREIGN',
        '9_순매수거래대금_기관계.csv': 'INSTITUTION',
        '10_순매수거래대금_연기금.csv': 'PENSION',
        '11_순매수거래대금_개인.csv': 'RETAIL',
    }

    all_values = []

    for filename, investor_type in files.items():
        file_path = temp_folder / filename

        logger.info(f"\n📂 {filename} ({investor_type}) 읽는 중...")
        data_list, matched, unmatched = load_csv_pivot_to_list(file_path, stock_mapping)

        logger.info(f"   매칭: {matched:,}개 종목, 미매칭: {unmatched:,}개")
        logger.info(f"   데이터: {len(data_list):,}건")

        # 투자자 타입 추가
        for item in data_list:
            if pd.notna(item['value']):
                all_values.append((
                    item['date'],
                    item['stock_code'],
                    investor_type,
                    int(item['value'])
                ))

    logger.info(f"\n✅ 전체 투자자 레코드: {len(all_values):,}개")

    # DB 삽입
    logger.info("\n💾 DB에 삽입 중...")

    try:
        conn = psycopg2.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            dbname=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD
        )
        cursor = conn.cursor()

        sql = """
            INSERT INTO investor_trading
            (time, stock_code, investor_type, net_buy_value)
            VALUES (%s, %s, %s, %s)
        """

        cursor.executemany(sql, all_values)
        conn.commit()

        logger.success(f"✅ 투자자 데이터: {len(all_values):,}건 삽입 완료")

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        logger.error(f"❌ DB 삽입 실패: {e}")
        return False

def main():
    """메인 함수"""
    logger.info("="*80)
    logger.info("🚀 전체 데이터 적재 시작")
    logger.info("="*80)
    logger.info("기간: 2022-01-03 ~ 2026-02-13")
    logger.info("시장: KOSPI + KOSDAQ + ETF")

    temp_folder = project_root / "raw_data" / "temp"

    # 1. 종목 매핑 로드
    stock_mapping = get_stock_mapping()
    if not stock_mapping:
        logger.error("❌ 종목 매핑 로드 실패")
        return False

    # 2. OHLCV 데이터
    if not load_ohlcv_data(stock_mapping, temp_folder):
        return False

    # 3. 시가총액 데이터
    if not load_market_cap_data(stock_mapping, temp_folder):
        return False

    # 4. 투자자 데이터
    if not load_investor_data(stock_mapping, temp_folder):
        return False

    # 5. 최종 확인
    logger.info("\n" + "="*80)
    logger.info("📊 적재 완료 - 최종 확인")
    logger.info("="*80)

    try:
        conn = psycopg2.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            dbname=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD
        )
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM ohlcv_daily")
        ohlcv_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM market_cap_daily")
        market_cap_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM investor_trading")
        investor_count = cursor.fetchone()[0]

        logger.info(f"  ohlcv_daily: {ohlcv_count:,}건")
        logger.info(f"  market_cap_daily: {market_cap_count:,}건")
        logger.info(f"  investor_trading: {investor_count:,}건")

        cursor.close()
        conn.close()

    except Exception as e:
        logger.error(f"확인 중 오류: {e}")

    logger.success("\n✅ 모든 데이터 적재 완료!")
    return True

if __name__ == "__main__":
    import time
    start_time = time.time()

    success = main()

    elapsed_time = time.time() - start_time
    logger.info(f"\n⏱️  소요 시간: {elapsed_time/60:.1f}분")

    sys.exit(0 if success else 1)
