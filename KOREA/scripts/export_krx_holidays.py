"""
KRX 휴장일 — DB 적재 + LENS JSON export

DB가 SSoT (Single Source of Truth):
- krx_holidays 테이블이 권위
- LENS JSON은 동일 결과의 파생물 (계약: 경로/포맷/reason 키 유지)

산출 출처/방법:
- 과거 (ohlcv_daily 범위 내): ohlcv_daily에 없는 평일 = 휴장일 → source='ohlcv_gap'
  - 임시공휴일도 자동 포착 (실제 거래소가 휴장한 날이 진실)
- 미래 (ohlcv_daily 이후): holidays.KR + 근로자의 날(5/1) + 연말 폐장(12/31)
  - source='holidays_kr' | 'rule_0501' | 'rule_1231'

reason (한국어) 우선순위:
1. holidays.KR 매칭 → 한국어 공휴일명
2. 5/1 → "근로자의 날"
3. 12/31 → "연말 폐장"
4. 그 외 (ohlcv 갭) → "임시휴장"

삭제 정책 (재계산 시 산출에서 사라진 날짜 처리):
- 산출 [year_start ~ year_end] 범위 안에서 테이블에 있는데 산출에 없는 날짜 → DELETE
- 단 source='manual' 행은 보호 (사람이 직접 넣은 임시공휴일 보존)

사용:
    python scripts/export_krx_holidays.py
    python scripts/export_krx_holidays.py --years 2022 2027
    python scripts/export_krx_holidays.py --output /custom/path.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import holidays as _hol
import psycopg2

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings


DEFAULT_OUTPUT = "/home/una0/projects/LENS/data/krx_holidays.json"

# holidays.KR이 잡지만 KRX는 정상 거래하는 날 (제외 필요)
# - 제헌절: 2008년부터 공휴일 아님. KRX 정상 거래.
KRX_NON_HOLIDAYS = {"Constitution Day"}

# holidays.KR이 한국어 미지원 → 직접 매핑
EN_KR_HOLIDAY = {
    "New Year's Day":                 "신정",
    "Korean New Year":                "설날",
    "The day preceding Korean New Year": "설날 연휴 (전일)",
    "The second day of Korean New Year": "설날 연휴 (다음날)",
    "Independence Movement Day":      "삼일절",
    "Buddha's Birthday":              "부처님오신날",
    "Children's Day":                 "어린이날",
    "Memorial Day":                   "현충일",
    "Constitution Day":               "제헌절",
    "Liberation Day":                 "광복절",
    "National Foundation Day":        "개천절",
    "Hangul Day":                     "한글날",
    "Chuseok":                        "추석",
    "The day preceding Chuseok":      "추석 연휴 (전일)",
    "The second day of Chuseok":      "추석 연휴 (다음날)",
    "Christmas Day":                  "성탄절",
    "Local Election Day":             "지방선거일",
}


def _to_korean(en_name: str) -> str:
    """영어 공휴일명 → 한국어. 'Alternative holiday for X' → 'X 대체공휴일'."""
    if en_name.startswith("Alternative holiday for "):
        base = en_name.replace("Alternative holiday for ", "")
        return f"{EN_KR_HOLIDAY.get(base, base)} 대체공휴일"
    return EN_KR_HOLIDAY.get(en_name, en_name)


def _conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


def fetch_trading_days(conn, start: date, end: date) -> set[date]:
    """ohlcv_daily에 실제 거래된 날짜 셋."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT time FROM ohlcv_daily WHERE time BETWEEN %s AND %s",
            (start, end),
        )
        return {r[0] for r in cur.fetchall()}


def fetch_ohlcv_max(conn) -> date:
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(time) FROM ohlcv_daily")
        return cur.fetchone()[0]


def get_reason(d: date, kr_hols: dict) -> str:
    """휴장일 사유 추정 (한국어)."""
    if d in kr_hols:
        return _to_korean(kr_hols[d])
    if d.month == 5 and d.day == 1:
        return "근로자의 날"
    if d.month == 12 and d.day == 31:
        return "연말 폐장"
    return "임시휴장"


def build_holidays(conn, year_start: int, year_end: int) -> list[dict]:
    """[year_start, year_end] 범위의 KRX 휴장일 산출. 각 항목에 source 포함."""
    start = date(year_start, 1, 1)
    end = date(year_end, 12, 31)

    ohlcv_max = fetch_ohlcv_max(conn)
    trading_days = fetch_trading_days(conn, start, ohlcv_max)
    kr_hols = _hol.KR(years=range(year_start, year_end + 1))

    result: list[dict] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # 평일만 (토/일은 캘린더에서 자명)
            is_holiday = False
            source = None

            if cur <= ohlcv_max:
                # 과거: ohlcv 진실 — 갭이면 휴장
                if cur not in trading_days:
                    is_holiday = True
                    source = "ohlcv_gap"
            else:
                # 미래: 라이브러리 + 규칙
                if cur in kr_hols and kr_hols[cur] not in KRX_NON_HOLIDAYS:
                    is_holiday = True
                    source = "holidays_kr"
                elif cur.month == 5 and cur.day == 1:
                    is_holiday = True
                    source = "rule_0501"
                elif cur.month == 12 and cur.day == 31:
                    is_holiday = True
                    source = "rule_1231"

            if is_holiday:
                result.append({
                    "date":   cur.isoformat(),
                    "reason": get_reason(cur, kr_hols),
                    "source": source,
                })
        cur += timedelta(days=1)

    return result


def upsert_holidays(conn, holidays_list: list[dict], year_start: int, year_end: int) -> dict:
    """
    트랜잭션 내에서:
      1) UPSERT (ON CONFLICT (date) DO UPDATE) — 산출된 모든 휴일
      2) DELETE — 범위 내 source != 'manual' 인데 산출에 없는 날짜 (사후 정정 대응)
    Returns: {'upserted': N, 'deleted': N}
    """
    range_start = date(year_start, 1, 1)
    range_end   = date(year_end, 12, 31)
    computed_dates = [date.fromisoformat(h["date"]) for h in holidays_list]

    upserted = 0
    deleted = 0
    with conn:
        with conn.cursor() as cur:
            for h in holidays_list:
                cur.execute("""
                    INSERT INTO krx_holidays (date, reason, source, updated_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (date) DO UPDATE
                       SET reason = EXCLUDED.reason,
                           source = EXCLUDED.source,
                           updated_at = now()
                """, (h["date"], h["reason"], h["source"]))
                upserted += 1

            # 사후 정정: 범위 내 manual 아닌 행 중 산출에서 사라진 날짜 삭제
            if computed_dates:
                cur.execute("""
                    DELETE FROM krx_holidays
                     WHERE date BETWEEN %s AND %s
                       AND source <> 'manual'
                       AND date <> ALL(%s)
                """, (range_start, range_end, computed_dates))
            else:
                cur.execute("""
                    DELETE FROM krx_holidays
                     WHERE date BETWEEN %s AND %s
                       AND source <> 'manual'
                """, (range_start, range_end))
            deleted = cur.rowcount

    return {"upserted": upserted, "deleted": deleted}


def fetch_for_export(conn, year_start: int, year_end: int) -> list[dict]:
    """LENS JSON용: DB에서 [year_start, year_end] 범위의 휴일 조회 (date, reason)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT date, reason
              FROM krx_holidays
             WHERE date BETWEEN %s AND %s
             ORDER BY date
        """, (date(year_start, 1, 1), date(year_end, 12, 31)))
        return [{"date": r[0].isoformat(), "reason": r[1]} for r in cur.fetchall()]


def write_atomic(payload: list[dict], output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp_path.replace(output_path)


def run_export(year_start: int = 2022, year_end: int = 2027,
               output_path: Path = Path(DEFAULT_OUTPUT)) -> dict:
    """
    파이프라인 진입점 (daily_update에서 호출):
      산출 → DB UPSERT/DELETE → DB에서 다시 읽어 JSON write
    """
    conn = _conn()
    try:
        ohlcv_max = fetch_ohlcv_max(conn)
        holidays_list = build_holidays(conn, year_start, year_end)
        stats = upsert_holidays(conn, holidays_list, year_start, year_end)
        # JSON은 DB에서 다시 읽어 적음 — manual 행 포함, source 컬럼 제외
        json_payload = fetch_for_export(conn, year_start, year_end)
    finally:
        conn.close()

    write_atomic(json_payload, output_path)
    return {
        "ohlcv_max": ohlcv_max,
        "computed":  len(holidays_list),
        "upserted":  stats["upserted"],
        "deleted":   stats["deleted"],
        "json_rows": len(json_payload),
        "json_path": str(output_path),
    }


def main():
    parser = argparse.ArgumentParser(description="KRX 휴장일 → DB 적재 + LENS JSON export")
    parser.add_argument("--years", nargs=2, type=int, metavar=("START", "END"),
                        default=[2022, 2027],
                        help="대상 연도 범위 (기본: 2022 ~ 2027)")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    output_path = Path(args.output)
    print(f"[krx-holidays] {args.years[0]}~{args.years[1]} → DB + {output_path}")

    result = run_export(args.years[0], args.years[1], output_path)
    print(f"  ohlcv_daily 범위: ~ {result['ohlcv_max']} (그 이후는 holidays.KR 추정)")
    print(f"  산출 {result['computed']}건 → DB UPSERT {result['upserted']} / DELETE {result['deleted']}")
    print(f"  JSON write: {result['json_rows']}건 → {result['json_path']}")

    # 요약: 연도별 카운트 + 임시공휴일/manual case
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXTRACT(YEAR FROM date)::int AS y, source, COUNT(*)
                  FROM krx_holidays
                 WHERE date BETWEEN %s AND %s
                 GROUP BY y, source
                 ORDER BY y, source
            """, (date(args.years[0], 1, 1), date(args.years[1], 12, 31)))
            rows = cur.fetchall()
    finally:
        conn.close()

    print()
    print("연도별 source 분포:")
    cur_year = None
    for y, src, n in rows:
        if y != cur_year:
            print(f"  {y}:")
            cur_year = y
        print(f"    {src:14s} {n:3d}건")


if __name__ == "__main__":
    main()
