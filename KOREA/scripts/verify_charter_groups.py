"""
charter_group 분류 결과 검증 (cross-check)

두 가지 독립 신호 비교:
- 신호 1: 정관 본문 분석 결과 (classify_charter_groups.py 가 채운 dividends.charter_group)
- 신호 2: record_date 휴리스틱
    · 종목의 가장 최근 ANNUAL 결산배당 record_date 가 12-31 → 휴리스틱 B
    · 그 외 (1-1 ~ 12-30) → 휴리스틱 A
    · ANNUAL 데이터 없음 → 알 수 없음

비교 결과:
- 일치: 분류 결과 신뢰도 ↑
- 불일치: 보고 (수동 검토 필요)
- 분류 NULL + 휴리스틱 명확: 휴리스틱 결과를 추천 (자동 적용 옵션 가능)

실행:
    python scripts/verify_charter_groups.py                # 보고서만 출력
    python scripts/verify_charter_groups.py --apply        # NULL인 종목에 휴리스틱 자동 적용
    python scripts/verify_charter_groups.py --save report.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import psycopg2
import psycopg2.extras

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings


def _conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


HEURISTIC_SQL = """
WITH last_annual AS (
    SELECT code,
           MAX(fiscal_year) AS fy,
           (SELECT record_date
              FROM dividends d2
             WHERE d2.code = d.code
               AND d2.period = 'ANNUAL'
               AND d2.is_latest = TRUE
               AND d2.record_date IS NOT NULL
             ORDER BY fiscal_year DESC
             LIMIT 1) AS rd
      FROM dividends d
     WHERE period = 'ANNUAL'
       AND is_latest = TRUE
     GROUP BY code
)
SELECT d.code,
       MAX(d.charter_group) AS classified,
       (SELECT rd FROM last_annual la WHERE la.code = d.code) AS last_annual_rd
  FROM dividends d
 GROUP BY d.code
 ORDER BY d.code
"""


def heuristic_from_record_date(rd) -> str | None:
    """ANNUAL record_date의 (월,일)로 A/B 추정. None=판별 불가."""
    if rd is None:
        return None
    if rd.month == 12 and rd.day == 31:
        return "B"
    return "A"


def main():
    parser = argparse.ArgumentParser(description="charter_group 분류 결과 검증")
    parser.add_argument("--apply", action="store_true",
                        help="NULL 종목에 휴리스틱 결과를 자동 적용 (UPDATE 실행)")
    parser.add_argument("--save", type=str, default=None,
                        help="보고서를 텍스트 파일로 저장")
    args = parser.parse_args()

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(HEURISTIC_SQL)
            rows = cur.fetchall()
    finally:
        conn.close()

    # 분류
    matches = []     # (code, group)
    mismatches = []  # (code, classified, heuristic, last_rd)
    null_with_h = []  # (code, heuristic, last_rd) — NULL 분류인데 휴리스틱은 명확
    null_no_h = []    # (code, ) — 둘 다 알 수 없음

    counter = Counter()

    for code, classified, last_rd in rows:
        h = heuristic_from_record_date(last_rd)
        counter[("classified", classified)] += 1
        counter[("heuristic", h)] += 1

        if classified is None:
            if h is None:
                null_no_h.append(code)
            else:
                null_with_h.append((code, h, last_rd))
        else:
            if h is None or classified == h:
                matches.append((code, classified))
            else:
                mismatches.append((code, classified, h, last_rd))

    # 보고
    out = []
    out.append("=" * 70)
    out.append("charter_group 검증 보고서")
    out.append("=" * 70)
    out.append("")
    out.append(f"전체 종목: {len(rows):,}개")
    out.append("")
    out.append("[분류 결과 분포]")
    out.append(f"  classified=A : {counter[('classified', 'A')]}")
    out.append(f"  classified=B : {counter[('classified', 'B')]}")
    out.append(f"  classified=NULL: {counter[('classified', None)]}")
    out.append("")
    out.append("[휴리스틱 분포]")
    out.append(f"  heuristic=A : {counter[('heuristic', 'A')]}")
    out.append(f"  heuristic=B : {counter[('heuristic', 'B')]}")
    out.append(f"  heuristic=NULL (ANNUAL record_date 없음): {counter[('heuristic', None)]}")
    out.append("")
    out.append("=" * 70)
    out.append("[교차검증 결과]")
    out.append("=" * 70)
    out.append(f"  ✅ 일치 (또는 휴리스틱 NULL이라 비교 불가): {len(matches):,}개")
    out.append(f"  ⚠️  불일치 (수동 검토 필요): {len(mismatches):,}개")
    out.append(f"  🔵 분류 NULL + 휴리스틱 명확: {len(null_with_h):,}개  → 휴리스틱 결과로 채울 수 있음")
    out.append(f"  ⚫ 분류·휴리스틱 둘 다 NULL: {len(null_no_h):,}개")
    out.append("")

    if mismatches:
        out.append("=" * 70)
        out.append("⚠️  불일치 종목 (수동 검토)")
        out.append("=" * 70)
        out.append(f"{'code':<8} {'classified':<11} {'heuristic':<10} {'last_annual_rd':<15}")
        for code, c, h, rd in mismatches[:200]:
            out.append(f"{code:<8} {c:<11} {h:<10} {str(rd):<15}")
        if len(mismatches) > 200:
            out.append(f"... 그 외 {len(mismatches) - 200}개 더")
        out.append("")

    if null_with_h:
        out.append("=" * 70)
        out.append("🔵 분류 NULL인데 휴리스틱은 명확 (--apply 시 휴리스틱 채움)")
        out.append("=" * 70)
        out.append(f"  → A 추정: {sum(1 for _, h, _ in null_with_h if h == 'A')}개")
        out.append(f"  → B 추정: {sum(1 for _, h, _ in null_with_h if h == 'B')}개")
        out.append("")

    report = "\n".join(out)
    print(report)

    if args.save:
        Path(args.save).write_text(report, encoding="utf-8")
        print(f"\n📁 보고서 저장: {args.save}")

    if args.apply and null_with_h:
        print()
        print(f"=== --apply: NULL 종목 {len(null_with_h)}개에 휴리스틱 적용 ===")
        conn = _conn()
        try:
            with conn.cursor() as cur:
                for code, h, _ in null_with_h:
                    cur.execute(
                        "UPDATE dividends SET charter_group = %s WHERE code = %s AND charter_group IS NULL",
                        (h, code),
                    )
            conn.commit()
        finally:
            conn.close()
        print(f"  → UPDATE 완료")


if __name__ == "__main__":
    main()
