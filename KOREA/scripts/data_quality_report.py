"""
데이터 품질 체크 이력 리포트

data_quality_checks 테이블에 저장된 결과를 출력합니다.

사용법:
    python scripts/data_quality_report.py             # 최근 30일 요약
    python scripts/data_quality_report.py 20260219    # 특정 날짜 상세
    python scripts/data_quality_report.py 7           # 최근 7일 요약
"""

import sys
import json
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict

import psycopg2

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings

KST = ZoneInfo("Asia/Seoul")


def get_conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


# ── 데이터 조회 ───────────────────────────────────────────────────────────────
def fetch_summary(conn, since: date) -> list[dict]:
    """날짜별 × 테이블 × 체크유형별 이슈 요약"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT check_date, table_name, check_type, issue_count, details
            FROM data_quality_checks
            WHERE check_date >= %s
            ORDER BY check_date DESC, table_name, check_type
        """, (since,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_date_detail(conn, check_date: date) -> list[dict]:
    """특정 날짜의 모든 체크 결과 (details 포함)"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name, check_type, issue_count, details, created_at
            FROM data_quality_checks
            WHERE check_date = %s
            ORDER BY table_name, check_type
        """, (check_date,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── 요약 출력 ─────────────────────────────────────────────────────────────────
def print_summary(rows: list[dict], days: int):
    W = 72

    def sep(c="─"):
        print(c * W)

    print("═" * W)
    print(f"  데이터 품질 체크 이력 (최근 {days}일)")
    print("═" * W)

    if not rows:
        print("  체크 이력 없음 — daily_update.py 실행 후 자동으로 쌓입니다.")
        print("═" * W)
        return

    # 날짜별로 묶기
    by_date: dict[date, list[dict]] = defaultdict(list)
    for r in rows:
        by_date[r["check_date"]].append(r)

    # 체크 유형 종류 (컬럼 헤더용)
    check_types = ["NULL_CHECK", "RANGE_CHECK", "CONSISTENCY_CHECK"]
    type_abbr   = {"NULL_CHECK": "NULL", "RANGE_CHECK": "RANGE", "CONSISTENCY_CHECK": "CONSIST"}

    # 헤더
    print(f"\n  {'날짜':<12} ", end="")
    for ct in check_types:
        print(f"  {type_abbr[ct]:>8}", end="")
    print(f"  {'총이슈':>6}  상태")
    sep()

    issue_dates = []  # 이슈 있는 날짜 모아서 상세 출력용

    for d in sorted(by_date.keys(), reverse=True):
        checks = by_date[d]

        # 체크유형별 이슈 합산
        issues_by_type: dict[str, int] = defaultdict(int)
        for c in checks:
            issues_by_type[c["check_type"]] += (c["issue_count"] or 0)

        total = sum(issues_by_type.values())

        print(f"  {str(d):<12} ", end="")
        for ct in check_types:
            cnt = issues_by_type.get(ct)
            if cnt is None:
                print(f"  {'─':>8}", end="")
            elif cnt == 0:
                print(f"  {'0':>8}", end="")
            else:
                print(f"  {'⚠️ '+str(cnt):>8}", end="")

        status = "✅" if total == 0 else f"⚠️  {total}건"
        print(f"  {str(total):>6}  {status}")

        if total > 0:
            issue_dates.append(d)

    sep()
    total_issue_days = len(issue_dates)
    total_clean_days = len(by_date) - total_issue_days
    print(f"  총 {len(by_date)}일 체크  |  이상 없음: {total_clean_days}일  |  이슈 발생: {total_issue_days}일")

    # 이슈 있는 날짜 상세
    if issue_dates:
        print(f"\n{'═'*W}")
        print(f"  이슈 상세")
        print(f"{'═'*W}")
        conn = get_conn()
        for d in sorted(issue_dates, reverse=True):
            detail_rows = fetch_date_detail(conn, d)
            _print_date_detail(d, detail_rows)
        conn.close()

    print("═" * W)


# ── 날짜 상세 출력 ────────────────────────────────────────────────────────────
def _print_date_detail(check_date: date, rows: list[dict]):
    W = 72
    print(f"\n  [{check_date}]")
    print("  " + "─" * (W - 2))

    if not rows:
        print("  체크 이력 없음")
        return

    for r in rows:
        issue_count = r["issue_count"] or 0
        icon = "✅" if issue_count == 0 else "⚠️ "
        print(f"  {icon} {r['table_name']:<30} [{r['check_type']}]  이슈 {issue_count}건")

        if issue_count > 0 and r["details"]:
            details = r["details"]
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except Exception:
                    pass

            if isinstance(details, dict):
                for key, val in details.items():
                    if isinstance(val, list):
                        if val:
                            codes = ", ".join(str(v) for v in val[:10])
                            suffix = f" 외 {len(val)-10}개" if len(val) > 10 else ""
                            print(f"       {key}: {codes}{suffix}")
                    elif val not in (0, None, []):
                        print(f"       {key}: {val}")


def print_date_detail(conn, check_date: date):
    W = 72
    print("═" * W)
    print(f"  데이터 품질 체크 상세: {check_date}")
    print("═" * W)

    rows = fetch_date_detail(conn, check_date)

    if not rows:
        print(f"  {check_date} 체크 이력 없음")
        print("  daily_update.py 를 실행하거나:")
        print(f"  python validators/quality_checks.py {check_date.strftime('%Y%m%d')}")
    else:
        _print_date_detail(check_date, rows)
        print()
        print(f"  체크 실행 시각: {rows[0]['created_at'].strftime('%Y-%m-%d %H:%M:%S')}")

    print("═" * W)


# ── 진입점 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None

    conn = get_conn()

    if arg and len(arg) == 8 and arg.isdigit():
        # 특정 날짜 상세
        try:
            target = datetime.strptime(arg, "%Y%m%d").date()
        except ValueError:
            print("날짜 형식 오류. 사용법: python data_quality_report.py YYYYMMDD")
            sys.exit(1)
        print_date_detail(conn, target)

    else:
        # 요약 (기본 30일, 숫자 인수로 변경 가능)
        days = 30
        if arg:
            try:
                days = int(arg)
            except ValueError:
                print("사용법: python data_quality_report.py [일수|YYYYMMDD]")
                sys.exit(1)

        since = datetime.now(KST).date() - timedelta(days=days)
        rows  = fetch_summary(conn, since)
        print_summary(rows, days)

    conn.close()
