"""
순매수거래량(net_buy_volume) CSV → investor_trading 테이블 UPDATE
파일: 13~16번 CSV (pivot 형식: 행=날짜, 열=종목명)
방식: melt → 종목코드 매핑 → COPY 임시테이블 → UPDATE
"""

import io
import sys
import psycopg2
import pandas as pd

FILES = {
    "FOREIGN":     "raw_data/temp/13_순매수거래량 _외인.csv",
    "INSTITUTION": "raw_data/temp/14_순매수거래량_기관계.csv",
    "PENSION":     "raw_data/temp/15_순매수거래량_연기금.csv",
    "RETAIL":      "raw_data/temp/16_순매수거래량_개인.csv",
}


def get_conn():
    return psycopg2.connect(
        host="localhost", dbname="korea_stock_data",
        user="postgres", password="",
    )


def load_stock_map(conn):
    """stock_name → stock_code 매핑"""
    with conn.cursor() as cur:
        cur.execute("SELECT stock_name, stock_code FROM stocks")
        return {name: code for name, code in cur.fetchall()}


def process_file(inv_type: str, fpath: str, stock_map: dict) -> pd.DataFrame:
    """CSV 로드 → melt → 종목코드 매핑 → 정제"""
    df = pd.read_csv(fpath, index_col=0)
    date_col = df.index.name or df.reset_index().columns[0]

    df = df.reset_index()
    df = df.rename(columns={df.columns[0]: "date"})

    # melt: wide → long
    melted = df.melt(id_vars="date", var_name="stock_name", value_name="net_buy_volume")
    melted["date"] = pd.to_datetime(melted["date"]).dt.date
    melted["investor_type"] = inv_type

    # 종목코드 매핑
    melted["stock_code"] = melted["stock_name"].map(stock_map)
    matched   = melted[melted["stock_code"].notna()].copy()
    unmatched = melted[melted["stock_code"].isna()]["stock_name"].nunique()

    # NULL 값 제거 (거래없는 날)
    matched = matched.dropna(subset=["net_buy_volume"])
    matched["net_buy_volume"] = matched["net_buy_volume"].astype("Int64")

    print(f"  [{inv_type}] 원본: {len(melted):,}행 | 매칭: {len(matched):,}행 | 미매칭 종목: {unmatched}개")
    return matched[["date", "stock_code", "investor_type", "net_buy_volume"]]


def copy_and_update(conn, df: pd.DataFrame, inv_type: str):
    """임시 테이블 COPY → UPDATE investor_trading"""
    with conn.cursor() as cur:
        # 임시 테이블 생성
        cur.execute("""
            CREATE TEMP TABLE tmp_volume (
                date        DATE,
                stock_code  VARCHAR(10),
                investor_type VARCHAR(20),
                net_buy_volume BIGINT
            ) ON COMMIT DROP
        """)

        # COPY
        buf = io.StringIO()
        df.to_csv(buf, index=False, header=False)
        buf.seek(0)
        cur.copy_expert(
            "COPY tmp_volume FROM STDIN WITH CSV",
            buf
        )
        print(f"  [{inv_type}] COPY 완료: {len(df):,}행", flush=True)

        # UPDATE
        cur.execute("""
            UPDATE investor_trading it
            SET net_buy_volume = t.net_buy_volume
            FROM tmp_volume t
            WHERE it.time        = t.date
              AND it.stock_code  = t.stock_code
              AND it.investor_type = t.investor_type
        """)
        updated = cur.rowcount
        print(f"  [{inv_type}] UPDATE 완료: {updated:,}행", flush=True)

    conn.commit()


def main():
    conn = get_conn()
    stock_map = load_stock_map(conn)
    print(f"종목 마스터: {len(stock_map):,}개\n")

    total_updated = 0

    for inv_type, fpath in FILES.items():
        print(f"처리 중: {inv_type} ({fpath})")
        df = process_file(inv_type, fpath, stock_map)
        copy_and_update(conn, df, inv_type)
        total_updated += len(df)
        print()

    # 검증
    with conn.cursor() as cur:
        cur.execute("""
            SELECT investor_type,
                   COUNT(*)                    AS total,
                   COUNT(net_buy_volume)        AS volume_filled,
                   COUNT(*) - COUNT(net_buy_volume) AS volume_null
            FROM investor_trading
            GROUP BY investor_type
            ORDER BY investor_type
        """)
        rows = cur.fetchall()

    print("=" * 60)
    print("최종 검증:")
    print(f"{'투자자':<12} {'전체':>10} {'volume채움':>10} {'NULL':>10}")
    for r in rows:
        print(f"{r[0]:<12} {r[1]:>10,} {r[2]:>10,} {r[3]:>10,}")

    conn.close()
    print("\n완료!")


if __name__ == "__main__":
    main()
