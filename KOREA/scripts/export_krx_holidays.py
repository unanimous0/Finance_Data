"""
KRX 휴장일 LENS export

출처/방법:
- 과거 (ohlcv_daily 범위 내): ohlcv_daily에 없는 평일 = 휴장일 (정확)
  - 임시공휴일도 자동 포착 (실제 거래소가 휴장한 날이 진실)
- 미래 (ohlcv_daily 이후): holidays.KR + 근로자의 날(5/1) + 연말 폐장(12/31)
  - 임시공휴일은 발표 후 정부가 holidays.KR에 반영되면 자동 갱신
  - KRX 12월 영업일정 발표 후 수동 보완 가능

reason 추정 우선순위:
1. holidays.KR 매칭 → 한국어 공휴일명
2. 5/1 → "근로자의 날"
3. 12/31 → "연말 폐장"
4. 그 외 (ohlcv 갭) → "임시휴장"

출력:
    [
      {"date": "2026-05-01", "reason": "근로자의 날"},
      {"date": "2026-05-05", "reason": "어린이날"},
      ...
    ]

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
    # 1) 공공 공휴일 (영→한 변환)
    if d in kr_hols:
        return _to_korean(kr_hols[d])
    # 2) 근로자의 날
    if d.month == 5 and d.day == 1:
        return "근로자의 날"
    # 3) 12/31 연말 폐장 (KRX 관행: 12/31이 평일이면 폐장)
    if d.month == 12 and d.day == 31:
        return "연말 폐장"
    # 4) 그 외: 임시공휴일 또는 휴장
    return "임시휴장"


def build_holidays(conn, year_start: int, year_end: int) -> list[dict]:
    """[year_start, year_end] 범위의 KRX 휴장일 산출."""
    start = date(year_start, 1, 1)
    end = date(year_end, 12, 31)

    ohlcv_max = fetch_ohlcv_max(conn)
    trading_days = fetch_trading_days(conn, start, ohlcv_max)
    kr_hols = _hol.KR(years=range(year_start, year_end + 1))

    result: list[dict] = []
    cur = start
    while cur <= end:
        # 토/일은 휴장일이지만 명시적 출력 안 함 (캘린더에서 자명)
        if cur.weekday() < 5:  # 평일만
            is_holiday = False

            if cur <= ohlcv_max:
                # 과거: ohlcv 진실
                is_holiday = cur not in trading_days
            else:
                # 미래: holidays.KR + 5/1 + 12/31 추정 (KRX 비휴장 항목 제외)
                if cur in kr_hols and kr_hols[cur] not in KRX_NON_HOLIDAYS:
                    is_holiday = True
                elif cur.month == 5 and cur.day == 1:
                    is_holiday = True
                elif cur.month == 12 and cur.day == 31:
                    is_holiday = True

            if is_holiday:
                result.append({
                    "date":   cur.isoformat(),
                    "reason": get_reason(cur, kr_hols),
                })
        cur += timedelta(days=1)

    return result


def write_atomic(payload: list[dict], output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp_path.replace(output_path)


def main():
    parser = argparse.ArgumentParser(description="KRX 휴장일 → LENS JSON export")
    parser.add_argument("--years", nargs=2, type=int, metavar=("START", "END"),
                        default=[2022, 2027],
                        help="대상 연도 범위 (기본: 2022 ~ 2027)")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    output_path = Path(args.output)
    print(f"[krx-holidays] {args.years[0]}~{args.years[1]} → {output_path}")

    conn = _conn()
    try:
        ohlcv_max = fetch_ohlcv_max(conn)
        print(f"  ohlcv_daily 범위: ~ {ohlcv_max} (그 이후는 holidays.KR 추정)")
        holidays_list = build_holidays(conn, args.years[0], args.years[1])
    finally:
        conn.close()

    write_atomic(holidays_list, output_path)
    print(f"[krx-holidays] 완료: {len(holidays_list)}건")

    # 요약: 연도별 카운트 + 임시공휴일 case
    from collections import Counter
    by_year = Counter(h["date"][:4] for h in holidays_list)
    print()
    print("연도별:")
    for y, n in sorted(by_year.items()):
        print(f"  {y}: {n}건")
    irregular = [h for h in holidays_list if h["reason"] == "임시휴장"]
    if irregular:
        print()
        print(f"임시휴장 ({len(irregular)}건):")
        for h in irregular[:20]:
            print(f"  {h['date']}")


if __name__ == "__main__":
    main()
