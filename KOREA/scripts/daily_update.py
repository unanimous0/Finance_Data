"""
일별 데이터 업데이트 스크립트
대상: ohlcv_daily, market_cap_daily, investor_trading
실행: 매일 16:30 (schedulers/daily_scheduler.py 또는 단독 실행)

사용법:
    python scripts/daily_update.py           # 자동 날짜 감지
    python scripts/daily_update.py 20260220  # 특정 날짜 지정
"""

import sys
import io
import time
import threading
import traceback
from pathlib import Path
from datetime import date, datetime, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings
from collectors.infomax import InfomaxClient

KST = ZoneInfo("Asia/Seoul")
REPORTS_DIR = project_root / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

# 병렬 처리 설정
# 공유 rate limiter(60회/분)를 N개 스레드가 나눠 쓰므로 rate limit 초과 없음.
# 네트워크 latency(~0.1초)와 throttle 대기를 오버랩해 처리율 향상.
MAX_WORKERS = 4

# 특이사항 임계값
THRESHOLD_PRICE_CHANGE  = 0.295   # 가격 변동 29.5% 이상 (상한/하한가 근접)
THRESHOLD_VOLUME_ZERO   = True    # 거래량 0 = 거래정지
THRESHOLD_LARGE_NET_BUY = 5e10    # 순매수 500억 이상 (거액 유입/이탈)


# ── DB 연결 ───────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


# ── 날짜 결정 ─────────────────────────────────────────────────────────────
def get_update_range(conn) -> tuple[date, date]:
    """
    업데이트 범위 결정
    start: DB의 마지막 날짜 + 1일
    end:   어제 (당일 데이터는 장 마감 확인 후 다음날 수집)
    """
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(time) FROM ohlcv_daily")
        last_date = cur.fetchone()[0]

    if last_date is None:
        raise ValueError("ohlcv_daily에 데이터가 없습니다.")

    start = last_date + timedelta(days=1)
    end   = datetime.now(KST).date() - timedelta(days=1)  # 어제까지 (당일 장 마감 전 실행 방지)
    return start, end


# ── 종목 목록 ─────────────────────────────────────────────────────────────
def get_stocks(conn, include_etf: bool = True) -> list[tuple[str, str]]:
    """(stock_code, stock_name) 리스트 반환"""
    with conn.cursor() as cur:
        if include_etf:
            cur.execute("""
                SELECT stock_code, stock_name
                FROM stocks
                WHERE is_active = TRUE
                ORDER BY stock_code
            """)
        else:
            cur.execute("""
                SELECT stock_code, stock_name
                FROM stocks
                WHERE is_active = TRUE
                  AND market IN ('KOSPI', 'KOSDAQ')
                ORDER BY stock_code
            """)
        return cur.fetchall()


# ── 병렬 수집 worker (module-level, pickle 가능) ──────────────────────────
def _fetch_hist(client, code, name, start, end):
    rows = client.get_hist(code, start, end)
    return code, name, rows


def _fetch_investor(client, code, name, start, end):
    rows = client.get_investor(code, start, end)
    return code, name, rows


# ── DB UPSERT ─────────────────────────────────────────────────────────────
OHLCV_SQL = """
INSERT INTO ohlcv_daily
    (time, stock_code, open_price, high_price, low_price, close_price, volume, trading_value)
VALUES %s
ON CONFLICT (time, stock_code) DO UPDATE SET
    open_price    = EXCLUDED.open_price,
    high_price    = EXCLUDED.high_price,
    low_price     = EXCLUDED.low_price,
    close_price   = EXCLUDED.close_price,
    volume        = EXCLUDED.volume,
    trading_value = EXCLUDED.trading_value
WHERE (ohlcv_daily.open_price, ohlcv_daily.high_price, ohlcv_daily.low_price,
       ohlcv_daily.close_price, ohlcv_daily.volume, ohlcv_daily.trading_value)
   IS DISTINCT FROM
      (EXCLUDED.open_price, EXCLUDED.high_price, EXCLUDED.low_price,
       EXCLUDED.close_price, EXCLUDED.volume, EXCLUDED.trading_value)
"""

MKTCAP_SQL = """
INSERT INTO market_cap_daily (time, stock_code, market_cap)
VALUES %s
ON CONFLICT (time, stock_code) DO UPDATE SET
    market_cap = EXCLUDED.market_cap
WHERE market_cap_daily.market_cap IS DISTINCT FROM EXCLUDED.market_cap
"""

INVESTOR_SQL = """
INSERT INTO investor_trading
    (time, stock_code, investor_type, net_buy_value, net_buy_volume)
VALUES %s
ON CONFLICT (time, stock_code, investor_type) DO UPDATE SET
    net_buy_value  = EXCLUDED.net_buy_value,
    net_buy_volume = EXCLUDED.net_buy_volume
WHERE (investor_trading.net_buy_value, investor_trading.net_buy_volume)
   IS DISTINCT FROM
      (EXCLUDED.net_buy_value, EXCLUDED.net_buy_volume)
"""


def upsert_batch(conn, sql: str, rows: list[tuple]) -> tuple[int, int]:
    """
    Returns: (changed_rows, total_rows)
    changed_rows: 실제 INSERT되거나 값이 달라서 UPDATE된 건수
    total_rows:   시도한 전체 건수 (changed + skipped)
    """
    if not rows:
        return 0, 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=500)
        changed = cur.rowcount  # WHERE 조건 불만족(값 동일)은 카운트 안 됨
    conn.commit()
    return changed, len(rows)


# ── 특이사항 분석 ─────────────────────────────────────────────────────────
def analyze_anomalies(ohlcv_rows: list[dict],
                      investor_rows: list[dict],
                      prev_close: dict) -> list[dict]:
    """
    특이사항 목록 반환
    각 항목: {"type", "stock_code", "stock_name", "date", "detail", "value"}
    """
    anomalies = []

    # ── OHLCV 특이사항 ─────────────────────────────────────────
    for r in ohlcv_rows:
        code  = r["stock_code"]
        name  = r.get("stock_name", code)
        dt    = r["date"]
        close = r["close_price"] or 0
        high  = r["high_price"]  or 0
        low   = r["low_price"]   or 0
        vol   = r["volume"]      or 0

        # 거래량 0 → 거래정지/관리종목
        if vol == 0 and close > 0:
            anomalies.append({
                "type": "거래정지",
                "stock_code": code, "stock_name": name, "date": dt,
                "detail": f"거래량=0, 종가={close:,}원",
                "value": 0,
            })

        # OHLCV 논리 오류
        if high and low and high < low:
            anomalies.append({
                "type": "OHLCV오류",
                "stock_code": code, "stock_name": name, "date": dt,
                "detail": f"고가({high:,}) < 저가({low:,})",
                "value": high - low,
            })

        # 전일 대비 급등락 (상/하한가 근접)
        prev = prev_close.get(code)
        if prev and prev > 0 and close > 0:
            chg_rate = abs(close - prev) / prev
            if chg_rate >= THRESHOLD_PRICE_CHANGE:
                direction = "급등" if close > prev else "급락"
                anomalies.append({
                    "type": f"가격{direction}",
                    "stock_code": code, "stock_name": name, "date": dt,
                    "detail": f"전일종가={prev:,}원 → 당일종가={close:,}원 ({chg_rate*100:+.1f}%)",
                    "value": chg_rate,
                })

    # ── 투자자 수급 특이사항 ────────────────────────────────────
    # 종목별 날짜별 순매수 집계
    net_by_stock = defaultdict(lambda: defaultdict(int))
    names = {}
    for r in investor_rows:
        code = r["stock_code"]
        dt   = r["date"]
        net_by_stock[code][dt] += (r["net_buy_value"] or 0)
        names[code] = r.get("stock_name", code)

    for code, dates in net_by_stock.items():
        for dt, net_total in dates.items():
            if abs(net_total) >= THRESHOLD_LARGE_NET_BUY:
                direction = "대규모순매수" if net_total > 0 else "대규모순매도"
                anomalies.append({
                    "type": direction,
                    "stock_code": code, "stock_name": names.get(code, code),
                    "date": dt,
                    "detail": f"전체투자자 순매수합계={net_total/1e8:+.1f}억원",
                    "value": abs(net_total),
                })

    return anomalies


# ── 전일 종가 조회 ────────────────────────────────────────────────────────
def get_prev_close(conn, target_date: date) -> dict[str, float]:
    """target_date 직전 영업일의 종가 딕셔너리"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (stock_code)
                stock_code, close_price
            FROM ohlcv_daily
            WHERE time < %s
            ORDER BY stock_code, time DESC
        """, (target_date,))
        return {row[0]: row[1] for row in cur.fetchall()}


# ── 메인 업데이트 로직 ────────────────────────────────────────────────────
def run_update(target_date: date = None) -> dict:
    """
    일별 업데이트 실행
    Returns: 결과 딕셔너리 (보고서 생성용)
    """
    started_at = datetime.now(KST)
    conn = get_conn()
    client = InfomaxClient()

    # ── 업데이트 날짜 결정 ─────────────────────────────────────
    if target_date:
        start_date = end_date = target_date
    else:
        start_date, end_date = get_update_range(conn)
        if start_date > end_date:
            print(f"  업데이트할 데이터 없음 (DB 최신: {end_date}, 어제: {end_date})")
            conn.close()
            return {}

    all_stocks   = get_stocks(conn, include_etf=True)
    kospi_kosdaq = get_stocks(conn, include_etf=False)
    code_to_name = {c: n for c, n in all_stocks}

    total_stocks    = len(all_stocks)
    investor_stocks = len(kospi_kosdaq)

    print(f"\n{'='*70}")
    print(f"  일별 업데이트 시작: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  업데이트 기간: {start_date} ~ {end_date}")
    print(f"  전체 종목: {total_stocks}개 | 수급 대상: {investor_stocks}개")
    print(f"{'='*70}\n")

    # 전일 종가 (급등락 감지용)
    prev_close = get_prev_close(conn, start_date)

    # ── 결과 집계 변수 ─────────────────────────────────────────
    result = {
        "started_at":     started_at,
        "start_date":     start_date,
        "end_date":       end_date,
        "ohlcv":          {"success": 0, "fail": 0, "rows": 0, "changed": 0, "skipped": 0, "fail_codes": []},
        "market_cap":     {"rows": 0, "changed": 0, "skipped": 0},
        "investor":       {"success": 0, "fail": 0, "rows": 0, "changed": 0, "skipped": 0, "fail_codes": []},
        "ohlcv_data":     [],   # 분석용 raw rows
        "investor_data":  [],   # 분석용 raw rows
        "anomalies":      [],
        "errors":         [],
    }

    # ─────────────────────────────────────────────────────────
    # STEP 1: OHLCV + 시가총액 수집 (전 종목, 병렬)
    # ─────────────────────────────────────────────────────────
    print(f"[1/2] OHLCV + 시가총액 수집 ({total_stocks}개 종목, workers={MAX_WORKERS})...")

    ohlcv_batch  = []
    mktcap_batch = []
    all_ohlcv_rows = []
    done_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_hist, client, code, name, start_date, end_date): code
            for code, name in all_stocks
        }
        for future in as_completed(futures):
            code, name, rows = future.result()
            done_count += 1

            if not rows:
                result["ohlcv"]["fail"] += 1
                result["ohlcv"]["fail_codes"].append(code)
            else:
                result["ohlcv"]["success"] += 1
                for r in rows:
                    if r["date"] is None:
                        continue
                    r["stock_name"] = name
                    all_ohlcv_rows.append(r)

                    ohlcv_batch.append((
                        r["date"], r["stock_code"],
                        r["open_price"], r["high_price"],
                        r["low_price"],  r["close_price"],
                        r["volume"],     r["trading_value"],
                    ))

                    if r["close_price"] and r["listed_shares"]:
                        mkt_cap = r["close_price"] * r["listed_shares"]
                        mktcap_batch.append((r["date"], r["stock_code"], mkt_cap))

            # 배치 저장 (500건마다, 메인 스레드에서만 실행)
            if len(ohlcv_batch) >= 500:
                ch, tot = upsert_batch(conn, OHLCV_SQL, ohlcv_batch)
                result["ohlcv"]["changed"] += ch
                result["ohlcv"]["skipped"] += tot - ch
                result["ohlcv"]["rows"]    += tot
                ohlcv_batch.clear()
            if len(mktcap_batch) >= 500:
                ch, tot = upsert_batch(conn, MKTCAP_SQL, mktcap_batch)
                result["market_cap"]["changed"] += ch
                result["market_cap"]["skipped"] += tot - ch
                result["market_cap"]["rows"]    += tot
                mktcap_batch.clear()

            if done_count % 500 == 0 or done_count == total_stocks:
                print(f"  [{done_count:4}/{total_stocks}] 진행 중... (성공:{result['ohlcv']['success']} 실패:{result['ohlcv']['fail']})")

    # 잔여 저장
    if ohlcv_batch:
        ch, tot = upsert_batch(conn, OHLCV_SQL, ohlcv_batch)
        result["ohlcv"]["changed"] += ch
        result["ohlcv"]["skipped"] += tot - ch
        result["ohlcv"]["rows"]    += tot
    if mktcap_batch:
        ch, tot = upsert_batch(conn, MKTCAP_SQL, mktcap_batch)
        result["market_cap"]["changed"] += ch
        result["market_cap"]["skipped"] += tot - ch
        result["market_cap"]["rows"]    += tot

    result["ohlcv_data"] = all_ohlcv_rows
    print(f"  ✅ OHLCV {result['ohlcv']['rows']:,}건 저장 (변경:{result['ohlcv']['changed']:,} / 스킵:{result['ohlcv']['skipped']:,})")
    print(f"  ✅ 시가총액 {result['market_cap']['rows']:,}건 저장 (변경:{result['market_cap']['changed']:,} / 스킵:{result['market_cap']['skipped']:,})")

    # ─────────────────────────────────────────────────────────
    # STEP 2: 투자자별 수급 수집 (KOSPI + KOSDAQ, 병렬)
    # ─────────────────────────────────────────────────────────
    print(f"\n[2/2] 투자자별 수급 수집 ({investor_stocks}개 종목, workers={MAX_WORKERS})...")

    investor_batch    = []
    all_investor_rows = []
    done_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_investor, client, code, name, start_date, end_date): code
            for code, name in kospi_kosdaq
        }
        for future in as_completed(futures):
            code, name, rows = future.result()
            done_count += 1

            if not rows:
                result["investor"]["fail"] += 1
                result["investor"]["fail_codes"].append(code)
            else:
                result["investor"]["success"] += 1
                for r in rows:
                    if r["date"] is None:
                        continue
                    r["stock_name"] = name
                    all_investor_rows.append(r)
                    investor_batch.append((
                        r["date"], r["stock_code"], r["investor_type"],
                        r["net_buy_value"], r["net_buy_volume"],
                    ))

            if len(investor_batch) >= 500:
                ch, tot = upsert_batch(conn, INVESTOR_SQL, investor_batch)
                result["investor"]["changed"] += ch
                result["investor"]["skipped"] += tot - ch
                result["investor"]["rows"]    += tot
                investor_batch.clear()

            if done_count % 500 == 0 or done_count == investor_stocks:
                print(f"  [{done_count:4}/{investor_stocks}] 진행 중... (성공:{result['investor']['success']} 실패:{result['investor']['fail']})")

    if investor_batch:
        ch, tot = upsert_batch(conn, INVESTOR_SQL, investor_batch)
        result["investor"]["changed"] += ch
        result["investor"]["skipped"] += tot - ch
        result["investor"]["rows"]    += tot

    result["investor_data"] = all_investor_rows
    print(f"  ✅ 수급 {result['investor']['rows']:,}건 저장 (변경:{result['investor']['changed']:,} / 스킵:{result['investor']['skipped']:,})")

    # ─────────────────────────────────────────────────────────
    # STEP 3: 특이사항 분석
    # ─────────────────────────────────────────────────────────
    print("\n[분석] 특이사항 감지 중...")
    result["anomalies"] = analyze_anomalies(
        result["ohlcv_data"],
        result["investor_data"],
        prev_close,
    )
    print(f"  ✅ 특이사항 {len(result['anomalies'])}건 감지")

    result["finished_at"] = datetime.now(KST)
    conn.close()
    return result


# ── 보고서 생성 ───────────────────────────────────────────────────────────
def generate_report(result: dict) -> str:
    """상세 보고서 텍스트 생성"""
    started  = result["started_at"]
    finished = result["finished_at"]
    elapsed  = (finished - started).total_seconds()
    s_date   = result["start_date"]
    e_date   = result["end_date"]

    ohlcv    = result["ohlcv"]
    mktcap   = result["market_cap"]
    investor = result["investor"]
    anomalies = result["anomalies"]

    lines = []
    W = 72

    def sep(char="="):
        lines.append(char * W)

    def title(text):
        sep()
        lines.append(f"  {text}")
        sep()

    def sub(text):
        lines.append(f"\n── {text} {'─'*(W-5-len(text))}")

    # ── 헤더 ──────────────────────────────────────────────────
    title("📊 일별 데이터 업데이트 보고서")
    lines.append(f"  실행 일시 : {started.strftime('%Y-%m-%d %H:%M:%S KST')}")
    lines.append(f"  완료 일시 : {finished.strftime('%Y-%m-%d %H:%M:%S KST')}")
    lines.append(f"  소요 시간 : {int(elapsed//3600)}시간 {int(elapsed%3600//60)}분 {int(elapsed%60)}초")
    lines.append(f"  업데이트 기간 : {s_date} ~ {e_date}")
    lines.append("")

    # ── 수집 요약 ──────────────────────────────────────────────
    sub("1. 수집 요약")
    lines.append(f"  {'항목':<18} {'성공':>7} {'실패':>7} {'전체레코드':>11} {'신규/변경':>10} {'스킵(동일)':>11}")
    lines.append(f"  {'-'*68}")
    lines.append(
        f"  {'OHLCV (일봉)':<18} {ohlcv['success']:>7,} {ohlcv['fail']:>7,} "
        f"{ohlcv['rows']:>11,} {ohlcv['changed']:>10,} {ohlcv['skipped']:>11,}"
    )
    lines.append(
        f"  {'시가총액':<18} {'(OHLCV와 동일)':>14} "
        f"{mktcap['rows']:>11,} {mktcap['changed']:>10,} {mktcap['skipped']:>11,}"
    )
    lines.append(
        f"  {'투자자별 수급':<18} {investor['success']:>7,} {investor['fail']:>7,} "
        f"{investor['rows']:>11,} {investor['changed']:>10,} {investor['skipped']:>11,}"
    )
    total_rows    = ohlcv['rows']    + mktcap['rows']    + investor['rows']
    total_changed = ohlcv['changed'] + mktcap['changed'] + investor['changed']
    total_skipped = ohlcv['skipped'] + mktcap['skipped'] + investor['skipped']
    lines.append(f"  {'-'*68}")
    lines.append(
        f"  {'합계':<18} {'':>14} "
        f"{total_rows:>11,} {total_changed:>10,} {total_skipped:>11,}"
    )
    lines.append("")

    # ── 테이블별 상세 ──────────────────────────────────────────
    sub("2. 테이블별 상세 결과")

    lines.append(f"\n  [ohlcv_daily]")
    lines.append(f"    전체 건수  : {ohlcv['rows']:,}건")
    lines.append(f"    신규/변경  : {ohlcv['changed']:,}건")
    lines.append(f"    스킵(동일) : {ohlcv['skipped']:,}건")
    lines.append(f"    성공 종목  : {ohlcv['success']:,}개")
    lines.append(f"    실패 종목  : {ohlcv['fail']:,}개")
    if ohlcv['fail_codes']:
        codes_str = ', '.join(ohlcv['fail_codes'][:20])
        suffix = f" 외 {ohlcv['fail']-20}개" if ohlcv['fail'] > 20 else ""
        lines.append(f"    실패 코드  : {codes_str}{suffix}")

    lines.append(f"\n  [market_cap_daily]")
    lines.append(f"    전체 건수  : {mktcap['rows']:,}건")
    lines.append(f"    신규/변경  : {mktcap['changed']:,}건")
    lines.append(f"    스킵(동일) : {mktcap['skipped']:,}건")
    lines.append(f"    산출 방식  : close_price × listed_shares (hist API)")

    lines.append(f"\n  [investor_trading]")
    lines.append(f"    전체 건수  : {investor['rows']:,}건")
    lines.append(f"    신규/변경  : {investor['changed']:,}건")
    lines.append(f"    스킵(동일) : {investor['skipped']:,}건")
    lines.append(f"    성공 종목  : {investor['success']:,}개")
    lines.append(f"    실패 종목  : {investor['fail']:,}개")
    if investor['fail_codes']:
        codes_str = ', '.join(investor['fail_codes'][:20])
        suffix = f" 외 {investor['fail']-20}개" if investor['fail'] > 20 else ""
        lines.append(f"    실패 코드  : {codes_str}{suffix}")
    lines.append("")

    # ── 특이사항 ────────────────────────────────────────────────
    sub("3. 특이사항")

    if not anomalies:
        lines.append("\n  ✅ 특이사항 없음")
    else:
        # 유형별 분류
        by_type = defaultdict(list)
        for a in anomalies:
            by_type[a["type"]].append(a)

        type_order = ["거래정지", "OHLCV오류", "가격급등", "가격급락",
                      "대규모순매수", "대규모순매도"]
        sorted_types = sorted(by_type.keys(),
                              key=lambda t: type_order.index(t) if t in type_order else 99)

        lines.append(f"\n  총 {len(anomalies)}건의 특이사항이 감지되었습니다.\n")

        for atype in sorted_types:
            items = by_type[atype]
            emoji = {
                "거래정지":     "🔴",
                "OHLCV오류":    "❌",
                "가격급등":     "📈",
                "가격급락":     "📉",
                "대규모순매수": "💰",
                "대규모순매도": "💸",
            }.get(atype, "⚠️")

            lines.append(f"  {emoji} [{atype}] {len(items)}건")
            lines.append(f"  {'날짜':<12} {'종목코드':<10} {'종목명':<18} {'상세'}")
            lines.append(f"  {'-'*68}")
            # 가장 큰 값 순으로 정렬
            for a in sorted(items, key=lambda x: abs(x.get("value", 0) or 0), reverse=True):
                dt_str   = str(a["date"]) if a["date"] else "-"
                lines.append(
                    f"  {dt_str:<12} {a['stock_code']:<10} "
                    f"{a['stock_name'][:16]:<18} {a['detail']}"
                )
            lines.append("")

    # ── 실패 종목 상세 ─────────────────────────────────────────
    if ohlcv['fail'] > 0 or investor['fail'] > 0:
        sub("4. API 수집 실패 종목")
        lines.append("\n  ※ 실패 원인: API 응답 없음 / 해당 날짜 데이터 없음 (휴장일, 신규상장 전)")

        if ohlcv['fail_codes']:
            lines.append(f"\n  OHLCV 실패 ({ohlcv['fail']}개):")
            for c in ohlcv['fail_codes']:
                lines.append(f"    - {c}")

        if investor['fail_codes']:
            lines.append(f"\n  수급 실패 ({investor['fail']}개):")
            for c in investor['fail_codes']:
                lines.append(f"    - {c}")
        lines.append("")

    # ── 푸터 ──────────────────────────────────────────────────
    sep()
    lines.append(f"  보고서 생성: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    sep()

    return "\n".join(lines)


def save_report(report_text: str, target_date: date) -> Path:
    fname = REPORTS_DIR / f"daily_update_{target_date.strftime('%Y%m%d')}.txt"
    fname.write_text(report_text, encoding="utf-8")
    return fname


# ── 진입점 ────────────────────────────────────────────────────────────────
def main(target_date: date = None):
    try:
        result = run_update(target_date)
        report = generate_report(result)

        # 콘솔 출력
        print("\n" + report)

        # 파일 저장
        end_date = result["end_date"]
        fpath = save_report(report, end_date)
        print(f"\n📁 보고서 저장: {fpath}")

    except Exception as e:
        err_msg = traceback.format_exc()
        print(f"\n❌ 업데이트 중 오류 발생:\n{err_msg}", file=sys.stderr)
        # 오류 보고서 저장
        today = datetime.now(KST).date()
        err_report = f"업데이트 실패\n실행시각: {datetime.now(KST)}\n\n{err_msg}"
        fpath = REPORTS_DIR / f"daily_update_{today.strftime('%Y%m%d')}_ERROR.txt"
        fpath.write_text(err_report, encoding="utf-8")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            td = datetime.strptime(sys.argv[1], "%Y%m%d").date()
        except ValueError:
            print("날짜 형식 오류. 사용법: python daily_update.py YYYYMMDD")
            sys.exit(1)
    else:
        td = None

    main(td)
