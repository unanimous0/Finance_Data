"""
배당 데이터 LENS export

dividends 테이블 → LENS가 읽는 dividends.json 파일로 직렬화.

LENS 합의 형식 (한 row = 한 (code, fiscal_year, period)의 최신 버전):
{
  "exported_at": "2026-04-25T15:50:00+09:00",
  "count": 12345,
  "items": [
    {
      "id": ..., "code": ..., "fiscal_year": ..., "period": ...,
      "board_resolution_date": ..., "announced_at": ...,
      "record_date": ..., "ex_date": ..., "pay_date": ...,
      "amount": ..., "yield_pct": ..., "dividend_type": ...,
      "confirmed": ..., "estimation_basis": ...,
      "charter_group": ..., "source": ..., "version": ..., "is_latest": true,
      "raw_text_url": ...,
      "revisions": [
        {"version": 1, "amount": ..., "announced_at": ...},
        ...
      ]
    },
    ...
  ]
}

기본 동작:
- is_latest=TRUE 인 row만 export (각 그룹의 최신 버전)
- revisions: 같은 (code, fiscal_year, period) 의 과거 모든 버전을 임베드
- 추정값(confirmed=FALSE) 포함

cli:
    python scripts/export_dividends.py [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg2
import psycopg2.extras

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings


KST = timezone(timedelta(hours=9))


# ── DB ─────────────────────────────────────────

def _conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


# ── 직렬화 헬퍼 ──────────────────────────────

def _to_iso_date(v) -> str | None:
    return v.isoformat() if isinstance(v, date) else None


def _to_iso_dt(v) -> str | None:
    if not isinstance(v, datetime):
        return None
    if v.tzinfo is None:
        # DB는 naive timestamp → KST로 가정
        v = v.replace(tzinfo=KST)
    return v.isoformat()


def _to_float(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return v


# ── 메인 ────────────────────────────────────

LATEST_SQL = """
SELECT d.id, d.code,
       COALESCE(s.stock_name, d.corp_name) AS name,
       d.fiscal_year, d.period, d.version, d.is_latest,
       d.board_resolution_date, d.announced_at, d.record_date, d.ex_date, d.pay_date,
       d.amount, d.yield_pct, d.dividend_type,
       d.confirmed, d.estimation_basis, d.charter_group, d.source,
       d.dart_rcp_no, d.raw_text_url
  FROM dividends d
  LEFT JOIN stocks s ON s.stock_code = d.code
 WHERE d.is_latest = TRUE
 ORDER BY d.code, d.fiscal_year DESC, d.period
"""

REVISIONS_SQL = """
SELECT code, fiscal_year, period,
       version, amount, record_date, ex_date, announced_at,
       confirmed, source
  FROM dividends
 WHERE (code, fiscal_year, period) IN (
     SELECT code, fiscal_year, period FROM dividends
      GROUP BY code, fiscal_year, period
      HAVING COUNT(*) > 1
   )
 ORDER BY code, fiscal_year, period, version
"""


def build_payload(conn) -> dict:
    """전체 dividends → LENS payload."""
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(LATEST_SQL)
        latest_rows = cur.fetchall()

        cur.execute(REVISIONS_SQL)
        revision_rows = cur.fetchall()

    # 정정공시 이력을 (code, fiscal_year, period)별로 그룹핑
    revisions_map: dict[tuple, list[dict]] = defaultdict(list)
    for r in revision_rows:
        key = (r["code"], r["fiscal_year"], r["period"])
        revisions_map[key].append({
            "version":      r["version"],
            "amount":       _to_float(r["amount"]),
            "record_date":  _to_iso_date(r["record_date"]),
            "ex_date":      _to_iso_date(r["ex_date"]),
            "announced_at": _to_iso_dt(r["announced_at"]),
            "confirmed":    r["confirmed"],
            "source":       r["source"],
        })

    items: list[dict] = []
    for r in latest_rows:
        key = (r["code"], r["fiscal_year"], r["period"])
        # raw_text_url 없을 때 dart_rcp_no로 자동 생성
        url = r["raw_text_url"]
        if not url and r["dart_rcp_no"]:
            url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r['dart_rcp_no']}"

        items.append({
            "id":                    r["id"],
            "code":                  r["code"],
            "name":                  r["name"],   # stocks.stock_name (LEFT JOIN, NULL 가능)
            "fiscal_year":           r["fiscal_year"],
            "period":                r["period"],

            "board_resolution_date": _to_iso_date(r["board_resolution_date"]),
            "announced_at":          _to_iso_dt(r["announced_at"]),
            "record_date":           _to_iso_date(r["record_date"]),
            "ex_date":               _to_iso_date(r["ex_date"]),
            "pay_date":              _to_iso_date(r["pay_date"]),

            "amount":                _to_float(r["amount"]),
            "yield_pct":             _to_float(r["yield_pct"]),
            "dividend_type":         r["dividend_type"],

            "confirmed":             r["confirmed"],
            "estimation_basis":      r["estimation_basis"],
            "charter_group":         r["charter_group"],
            "source":                r["source"],
            "version":               r["version"],
            "is_latest":             r["is_latest"],
            "raw_text_url":          url,

            "revisions":             revisions_map.get(key, []),
        })

    return {
        "exported_at": datetime.now(KST).isoformat(timespec="seconds"),
        "count":       len(items),
        "items":       items,
    }


def write_atomic(payload: dict, output_path: Path):
    """tmp 파일에 쓰고 원자적으로 rename (LENS가 부분 파일 읽지 않도록)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp_path.replace(output_path)


def main():
    parser = argparse.ArgumentParser(description="dividends 테이블 → LENS JSON export")
    parser.add_argument("--output", type=str, default=settings.LENS_EXPORT_PATH,
                        help=f"출력 경로 (기본: {settings.LENS_EXPORT_PATH})")
    args = parser.parse_args()

    output_path = Path(args.output)
    print(f"[export] DB → {output_path}")

    conn = _conn()
    try:
        payload = build_payload(conn)
    finally:
        conn.close()

    write_atomic(payload, output_path)
    print(f"[export] 완료: {payload['count']:,}건 / {output_path}")


if __name__ == "__main__":
    main()
