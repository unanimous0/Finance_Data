"""
수집 현황 확인 스크립트

스케줄러가 정상적으로 동작하는지 한눈에 확인합니다.

사용법:
    python scripts/check_collection_status.py       # 최근 10 거래일
    python scripts/check_collection_status.py 20    # 최근 20 거래일
"""

import sys
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings

KST = ZoneInfo("Asia/Seoul")

# 예상 종목 수 (stocks 테이블 기준으로 동적 계산하므로 참고용)
EXPECTED_OHLCV_MARKET  = {"KOSPI", "KOSDAQ", "ETF"}
EXPECTED_INVEST_MARKET = {"KOSPI", "KOSDAQ"}


# ── DB 연결 ───────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


# ── 거래일 목록 생성 (평일 기준) ──────────────────────────────────────────────
def recent_weekdays(n: int) -> list[date]:
    """오늘부터 거슬러 올라가며 최근 n개 평일(월~금) 반환 (오늘 포함)"""
    days = []
    d = datetime.now(KST).date()
    while len(days) < n:
        if d.weekday() < 5:   # 0=월 … 4=금
            days.append(d)
        d -= timedelta(days=1)
    return days  # 최신순


# ── 날짜별 수집 현황 조회 ─────────────────────────────────────────────────────
def fetch_daily_counts(conn, start_date: date) -> dict:
    """
    start_date 이후 날짜별 수집 건수 반환
    {
      date: {
        "ohlcv":      종목 수,
        "market_cap": 종목 수,
        "investor":   종목 수 (DISTINCT stock_code),
        "quality":    이슈 합계 (None = 체크 안 함),
      }
    }
    """
    result: dict[date, dict] = {}

    with conn.cursor() as cur:
        # ohlcv_daily
        cur.execute("""
            SELECT time, COUNT(*) FROM ohlcv_daily
            WHERE time >= %s GROUP BY time
        """, (start_date,))
        for row in cur.fetchall():
            result.setdefault(row[0], {})["ohlcv"] = row[1]

        # market_cap_daily
        cur.execute("""
            SELECT time, COUNT(*) FROM market_cap_daily
            WHERE time >= %s GROUP BY time
        """, (start_date,))
        for row in cur.fetchall():
            result.setdefault(row[0], {})["market_cap"] = row[1]

        # investor_trading (DISTINCT stock_code)
        cur.execute("""
            SELECT time, COUNT(DISTINCT stock_code) FROM investor_trading
            WHERE time >= %s GROUP BY time
        """, (start_date,))
        for row in cur.fetchall():
            result.setdefault(row[0], {})["investor"] = row[1]

        # 품질 체크 이슈 합계
        cur.execute("""
            SELECT check_date, SUM(issue_count) FROM data_quality_checks
            WHERE check_date >= %s GROUP BY check_date
        """, (start_date,))
        for row in cur.fetchall():
            result.setdefault(row[0], {})["quality"] = int(row[1]) if row[1] is not None else None

    return result


# ── DB 전체 통계 ──────────────────────────────────────────────────────────────
def fetch_db_stats(conn) -> dict:
    with conn.cursor() as cur:
        stats = {}

        # 테이블별 레코드 수 + 기간
        for tbl in ("ohlcv_daily", "market_cap_daily"):
            cur.execute(f"SELECT COUNT(*), MIN(time), MAX(time) FROM {tbl}")
            cnt, mn, mx = cur.fetchone()
            stats[tbl] = {"count": cnt, "min": mn, "max": mx}

        cur.execute("""
            SELECT COUNT(*), MIN(time), MAX(time) FROM investor_trading
        """)
        cnt, mn, mx = cur.fetchone()
        stats["investor_trading"] = {"count": cnt, "min": mn, "max": mx}

        # 종목 수 (시장별)
        cur.execute("""
            SELECT market, COUNT(*) FROM stocks
            WHERE is_active = TRUE
            GROUP BY market ORDER BY market
        """)
        stats["stocks_by_market"] = {r[0]: r[1] for r in cur.fetchall()}
        stats["stocks_total"] = sum(stats["stocks_by_market"].values())

    return stats


# ── 예상 종목 수 조회 ─────────────────────────────────────────────────────────
def fetch_expected_counts(conn) -> dict:
    """활성 종목 수 기준 예상 수집 건수"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE is_active = TRUE)                                   AS total,
                COUNT(*) FILTER (WHERE is_active = TRUE AND market IN ('KOSPI','KOSDAQ'))   AS kospi_kosdaq
            FROM stocks
        """)
        total, kk = cur.fetchone()
    return {"ohlcv": total, "investor": kk}


# ── 출력 ──────────────────────────────────────────────────────────────────────
def print_status(n_days: int = 10):
    conn = get_conn()
    now = datetime.now(KST)
    today = now.date()

    # 조회할 날짜 목록
    weekdays = recent_weekdays(n_days)       # 최신순
    oldest   = weekdays[-1]

    counts   = fetch_daily_counts(conn, oldest)
    stats    = fetch_db_stats(conn)
    expected = fetch_expected_counts(conn)
    conn.close()

    W = 72

    def sep(c="─"):
        print(c * W)

    def header(title):
        sep("═")
        print(f"  {title}")
        sep("═")

    # ── 헤더 ─────────────────────────────────────────────────────────────────
    header("📊 한국 주식 데이터 수집 현황")
    print(f"  조회 시각  : {now.strftime('%Y-%m-%d %H:%M:%S KST')}")

    # ── 최신 현황 ─────────────────────────────────────────────────────────────
    db_last = stats["ohlcv_daily"]["max"]
    print()
    sep()
    print("  최신 현황")
    sep()

    if db_last is None:
        print("  ❌ DB에 데이터 없음")
    else:
        lag = (today - db_last).days
        lag_str = f"{lag}일 전" if lag > 0 else "오늘"
        print(f"  DB 마지막 거래일 : {db_last}  ({lag_str})")
        print(f"  현재 시각        : {today} ({['월','화','수','목','금','토','일'][today.weekday()]})")

        # 어제까지 평일 중 수집 안 된 날 탐지
        missed = [
            d for d in weekdays
            if d > db_last and d < today  # 어제까지 (당일 16:30 이전이면 오늘도 미수집이 정상)
        ]
        # 오늘이 평일이고 현재 16:30 이전이면 오늘은 미수집이 정상
        today_is_weekday = today.weekday() < 5
        after_cutoff     = now.hour > 16 or (now.hour == 16 and now.minute >= 30)

        if not missed:
            print(f"  상태             : ✅ 정상 (누락 없음)")
        else:
            missed_str = ", ".join(
                f"{d.strftime('%m/%d')}({['월','화','수','목','금'][d.weekday()]})"
                for d in reversed(missed)   # 오래된 순
            )
            print(f"  상태             : ⚠️  누락 가능성 → {missed_str}")
            print(f"  ※ 공휴일이면 정상. 그렇지 않으면 재수집 필요:")
            print(f"     python scripts/daily_update.py YYYYMMDD --missing-only")

        if today_is_weekday and not after_cutoff and today not in counts:
            print(f"  오늘 ({today}) : 🕐 16:30 수집 예정")

    # ── 날짜별 수집 현황 ──────────────────────────────────────────────────────
    print()
    sep()
    print(f"  날짜별 수집 현황 (최근 {n_days} 거래일)")
    sep()

    exp_ohlcv    = expected["ohlcv"]
    exp_investor = expected["investor"]

    # 헤더
    print(f"  {'날짜':<12} {'요일':^3}  {'OHLCV':>6}  {'시가총액':>8}  {'수급':>6}  {'품질이슈':>8}  상태")
    sep("─")

    for d in weekdays:
        c    = counts.get(d, {})
        dow  = ["월","화","수","목","금","토","일"][d.weekday()]

        ohlcv_cnt = c.get("ohlcv")
        mktcap_cnt = c.get("market_cap")
        inv_cnt   = c.get("investor")
        quality   = c.get("quality")

        if ohlcv_cnt is None:
            # 수집 없음
            if d >= today:
                status = "🕐 예정"
                row = f"  {str(d):<12} {dow:^3}  {'─':>6}  {'─':>8}  {'─':>6}  {'─':>8}  {status}"
            else:
                status = "⚠️  미수집"
                row = f"  {str(d):<12} {dow:^3}  {'미수집':>6}  {'':>8}  {'':>6}  {'':>8}  {status}"
        else:
            # OHLCV 비율
            ohlcv_pct = ohlcv_cnt / exp_ohlcv * 100 if exp_ohlcv else 0
            inv_pct   = inv_cnt / exp_investor * 100 if (inv_cnt and exp_investor) else 0

            ohlcv_str  = f"{ohlcv_cnt:,}"
            mktcap_str = f"{mktcap_cnt:,}" if mktcap_cnt else "─"
            inv_str    = f"{inv_cnt:,}" if inv_cnt else "─"
            quality_str = (
                "✅ 0건" if quality == 0
                else f"⚠️ {quality:,}건" if quality and quality > 0
                else "─ 미체크"
            )

            # 상태 판단
            if ohlcv_pct >= 99 and (inv_cnt is None or inv_pct >= 99):
                status = "✅ 정상"
            elif ohlcv_pct >= 95:
                status = f"🔶 {ohlcv_pct:.0f}%"
            else:
                status = f"⚠️  {ohlcv_pct:.0f}%"

            row = (
                f"  {str(d):<12} {dow:^3}  {ohlcv_str:>6}  "
                f"{mktcap_str:>8}  {inv_str:>6}  {quality_str:>8}  {status}"
            )

        print(row)

    sep("─")
    print(f"  예상 종목수: OHLCV {exp_ohlcv:,}개  /  수급 {exp_investor:,}개 (KOSPI+KOSDAQ)")

    # ── DB 전체 통계 ──────────────────────────────────────────────────────────
    print()
    sep()
    print("  DB 전체 통계")
    sep()

    for tbl, key in [
        ("ohlcv_daily",     "ohlcv_daily"),
        ("market_cap_daily","market_cap_daily"),
        ("investor_trading","investor_trading"),
    ]:
        s = stats[key]
        cnt = s["count"]
        mn  = s["min"]
        mx  = s["max"]
        period = f"{mn} ~ {mx}" if mn and mx else "─"
        print(f"  {tbl:<20} {cnt:>14,} 건   [{period}]")

    # 종목 수
    mkt = stats["stocks_by_market"]
    total = stats["stocks_total"]
    mkt_str = "  /  ".join(f"{m} {mkt[m]:,}" for m in sorted(mkt))
    print(f"  {'stocks':<20} {total:>14,} 종목  [{mkt_str}]")

    sep("═")
    print()


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    n = 10
    if len(sys.argv) > 1:
        try:
            n = int(sys.argv[1])
        except ValueError:
            print("사용법: python check_collection_status.py [최근_거래일_수]")
            sys.exit(1)

    print_status(n)
