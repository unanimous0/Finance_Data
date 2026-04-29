"""
배당 추정 엔진

목적:
- 공시 전 단계의 배당을 과거 패턴으로 추정해 dividends 테이블에 저장
- LENS 종목차익 화면이 베이시스 계산에 사용 (confirmed=False, source='ESTIMATE')

전략:
1. 과거 N년(기본 5년)의 확정 배당 이력을 (code, period)별로 그룹핑
2. 각 그룹의 amount 평균/중앙값/최근값을 후보로 가짐
3. record_date는 지난 회계연도 동기 record_date의 날짜 패턴(월·일)을 차년도에 투영
4. ex_date = record_date의 직전 영업일 (ohlcv_daily 거래일 기반)
5. charter_group A 그룹은 record_date 신뢰도가 낮음 → estimation_basis에 명시

기존 같은 (code, fiscal_year, period)에 확정값이 이미 있으면 추정값을 만들지 않음.
이미 추정값이 있고 새 추정 결과가 다르면 같은 row를 UPDATE (version 동일).
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean, median
from typing import Optional

import psycopg2
import psycopg2.extras

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings


HISTORY_YEARS_DEFAULT = 5


# ──────────────────────────────────────────────
# 자료구조
# ──────────────────────────────────────────────

@dataclass
class HistoricalDividend:
    fiscal_year: int
    period: str
    record_date: Optional[date]
    amount: Decimal
    charter_group: Optional[str]


@dataclass
class EstimatedDividend:
    code: str
    fiscal_year: int
    period: str
    amount: Decimal
    record_date: Optional[date]
    ex_date: Optional[date]
    pay_date: Optional[date]
    charter_group: Optional[str]
    estimation_basis: str

    def to_upsert_row(self) -> dict:
        return {
            "code": self.code,
            "fiscal_year": self.fiscal_year,
            "period": self.period,
            "version": 1,
            "is_latest": True,
            "amount": self.amount,
            "record_date": self.record_date,
            "ex_date": self.ex_date,
            "pay_date": self.pay_date,
            "dividend_type": "CASH",
            "confirmed": False,
            "estimation_basis": self.estimation_basis,
            "charter_group": self.charter_group,
            "source": "ESTIMATE",
        }


# ──────────────────────────────────────────────
# DB 액세스
# ──────────────────────────────────────────────

def _conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


def fetch_history(conn, code: str, end_year: int,
                  years: int = HISTORY_YEARS_DEFAULT) -> list[HistoricalDividend]:
    """과거 N년의 확정 배당 이력 (is_latest=TRUE, confirmed=TRUE)."""
    start_year = end_year - years
    with conn.cursor() as cur:
        cur.execute("""
            SELECT fiscal_year, period, record_date, amount, charter_group
              FROM dividends
             WHERE code = %s
               AND fiscal_year BETWEEN %s AND %s
               AND confirmed = TRUE
               AND is_latest = TRUE
             ORDER BY fiscal_year, period
        """, (code, start_year, end_year - 1))
        return [HistoricalDividend(*row) for row in cur.fetchall()]


def fetch_business_day_before(conn, target: date) -> Optional[date]:
    """ohlcv_daily 거래일 기반: target 직전 영업일."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT MAX(time) FROM ohlcv_daily WHERE time < %s
        """, (target,))
        row = cur.fetchone()
        return row[0] if row and row[0] else None


def fetch_latest_charter_group(conn, code: str) -> Optional[str]:
    """가장 최근 dividend의 charter_group (없으면 None)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT charter_group FROM dividends
             WHERE code = %s AND charter_group IS NOT NULL
             ORDER BY fiscal_year DESC, period DESC
             LIMIT 1
        """, (code,))
        row = cur.fetchone()
        return row[0] if row else None


def fetch_codes_with_history(conn, end_year: int,
                             years: int = HISTORY_YEARS_DEFAULT) -> list[str]:
    """과거 N년 내 확정 배당 이력이 있는 종목 코드."""
    start_year = end_year - years
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT code FROM dividends
             WHERE fiscal_year BETWEEN %s AND %s
               AND confirmed = TRUE
        """, (start_year, end_year - 1))
        return [r[0] for r in cur.fetchall()]


# ──────────────────────────────────────────────
# 추정 로직
# ──────────────────────────────────────────────

def _project_record_date(historical_dates: list[date], target_year: int) -> Optional[date]:
    """
    역대 record_date들의 (월·일) 패턴을 target_year에 투영.
    여러 해의 같은 period record_date를 받아 평균 일자를 계산.
    """
    if not historical_dates:
        return None
    # 단순화: 가장 최근 기록의 (월·일)을 그대로 사용
    last = max(historical_dates)
    try:
        return date(target_year, last.month, last.day)
    except ValueError:
        # 2/29 등 윤년 이슈 → 28일로 보정
        return date(target_year, last.month, min(last.day, 28))


def _estimate_amount(amounts: list[Decimal]) -> tuple[Decimal, str]:
    """
    amount 추정 + 근거 문구 반환.
    - 1개: 직전값 그대로
    - 2~3개: 평균
    - 4개 이상: 최근 3년 평균 (안정성)
    """
    if len(amounts) == 1:
        return amounts[0], "직전 1회 동일 적용"
    if len(amounts) <= 3:
        avg = Decimal(str(round(mean(amounts), 4)))
        return avg, f"직전 {len(amounts)}회 평균"
    recent = amounts[-3:]
    avg = Decimal(str(round(mean(recent), 4)))
    return avg, "직전 3회 평균"


def estimate_for_code(conn, code: str, target_year: int,
                      history_years: int = HISTORY_YEARS_DEFAULT
                      ) -> list[EstimatedDividend]:
    """
    한 종목의 target_year 배당을 추정.
    - target_year 내 어떤 period들이 있을지: 직전년도 동일 period 셋을 그대로 가정
    - 이미 확정값/추정값이 있는 (target_year, period)는 이 함수에서 만들지 않고 호출부에서 정리
    """
    history = fetch_history(conn, code, target_year, years=history_years)
    if not history:
        return []

    # period별로 묶어 amount 시계열 + record_date 시계열 생성
    by_period: dict[str, list[HistoricalDividend]] = defaultdict(list)
    for h in history:
        by_period[h.period].append(h)

    charter_group = fetch_latest_charter_group(conn, code)

    estimates: list[EstimatedDividend] = []
    for period, rows in by_period.items():
        rows.sort(key=lambda r: r.fiscal_year)
        amounts = [r.amount for r in rows if r.amount is not None]
        if not amounts:
            continue

        amount, basis_amount = _estimate_amount(amounts)

        # record_date 추정
        rec_dates = [r.record_date for r in rows if r.record_date]
        rec = _project_record_date(rec_dates, target_year)

        # ex_date 산출 (ohlcv_daily 거래일 기반)
        ex = fetch_business_day_before(conn, rec) if rec else None

        # estimation_basis 조립
        basis_parts = [basis_amount]
        if rec:
            basis_parts.append("기준일=직전년도 동일 일자")
        if charter_group == "A":
            basis_parts.append("정관변경(A)으로 기준일 확정성 낮음")
        basis = " · ".join(basis_parts)
        if len(basis) > 200:
            basis = basis[:197] + "..."

        estimates.append(EstimatedDividend(
            code=code,
            fiscal_year=target_year,
            period=period,
            amount=amount,
            record_date=rec,
            ex_date=ex,
            pay_date=None,  # 지급일은 보수적으로 NULL (회사마다 너무 다름)
            charter_group=charter_group,
            estimation_basis=basis,
        ))
    return estimates


# ──────────────────────────────────────────────
# Upsert
# ──────────────────────────────────────────────

UPSERT_SQL = """
INSERT INTO dividends (
    code, fiscal_year, period, version, is_latest,
    amount, record_date, ex_date, pay_date,
    dividend_type, confirmed, estimation_basis, charter_group, source
) VALUES (
    %(code)s, %(fiscal_year)s, %(period)s, %(version)s, %(is_latest)s,
    %(amount)s, %(record_date)s, %(ex_date)s, %(pay_date)s,
    %(dividend_type)s, %(confirmed)s, %(estimation_basis)s, %(charter_group)s, %(source)s
)
ON CONFLICT (code, fiscal_year, period, version) DO UPDATE SET
    amount           = EXCLUDED.amount,
    record_date      = EXCLUDED.record_date,
    ex_date          = EXCLUDED.ex_date,
    pay_date         = EXCLUDED.pay_date,
    estimation_basis = EXCLUDED.estimation_basis,
    charter_group    = EXCLUDED.charter_group
WHERE dividends.confirmed = FALSE   -- 확정값은 절대 덮어쓰지 않음
  AND dividends.source = 'ESTIMATE'
"""


def save_estimates(conn, estimates: list[EstimatedDividend]) -> int:
    """추정값을 dividends에 upsert. 확정값(confirmed=TRUE)은 보호."""
    if not estimates:
        return 0
    rows = [e.to_upsert_row() for e in estimates]
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, UPSERT_SQL, rows, page_size=200)
    conn.commit()
    return len(rows)


# ──────────────────────────────────────────────
# 엔트리 포인트
# ──────────────────────────────────────────────

def run_estimation(target_year: Optional[int] = None,
                   history_years: int = HISTORY_YEARS_DEFAULT,
                   verbose: bool = True) -> dict:
    """
    배당 이력이 있는 모든 종목에 대해 target_year 추정 실행.
    """
    if target_year is None:
        target_year = date.today().year

    conn = _conn()
    try:
        codes = fetch_codes_with_history(conn, target_year, years=history_years)
        if verbose:
            print(f"[추정] 대상 종목: {len(codes):,}개 (target_year={target_year})")

        all_estimates: list[EstimatedDividend] = []
        for i, code in enumerate(codes, 1):
            est = estimate_for_code(conn, code, target_year, history_years)
            all_estimates.extend(est)
            if verbose and i % 500 == 0:
                print(f"  [{i:>5}/{len(codes)}] 진행 중... 추정값 누적 {len(all_estimates):,}건")

        saved = save_estimates(conn, all_estimates)
        if verbose:
            print(f"[추정] 완료: {saved:,}건 upsert")
        return {"target_year": target_year, "codes": len(codes),
                "estimates": len(all_estimates), "saved": saved}
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="배당 추정 엔진 실행")
    parser.add_argument("--year", type=int, default=None,
                        help="추정 대상 회계연도 (기본: 올해)")
    parser.add_argument("--history-years", type=int, default=HISTORY_YEARS_DEFAULT,
                        help=f"참고할 과거 연수 (기본: {HISTORY_YEARS_DEFAULT})")
    args = parser.parse_args()
    run_estimation(target_year=args.year, history_years=args.history_years)
