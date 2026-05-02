"""
DART '현금ㆍ현물배당결정' 공시 백필

전략:
- 날짜 단위로 list.json 검색 (corp_code 없이) → 한 번에 여러 종목 처리
- 각 공시의 document.xml 다운로드 + 파싱
- (code, fiscal_year, period) 그룹화 → announced_at 순으로 version 부여
- 정정공시는 별도 row, 마지막 것만 is_latest=TRUE
- ex_date는 ohlcv_daily 거래일 기반으로 산출

재개 가능:
- DB에 이미 들어있는 dart_rcp_no는 스킵
- 중간 중단해도 다시 실행하면 누락분만 처리

사용:
    python scripts/backfill_dividends.py 20220101 20260427
    python scripts/backfill_dividends.py 20220101 20260427 --workers 4
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings
from collectors.dart import DartClient


# ── 휴장일 (krx_holidays 테이블 SSoT) ──────────────────────────────
# 모듈 로드 시 DB에서 한 번만 읽어 메모리 캐시. daily_update 1회 실행 동안의 휴일은 불변.
_KRX_HOLIDAYS_CACHE: Optional[set[date]] = None


def _load_krx_holidays() -> set[date]:
    global _KRX_HOLIDAYS_CACHE
    if _KRX_HOLIDAYS_CACHE is None:
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute("SELECT date FROM krx_holidays")
                _KRX_HOLIDAYS_CACHE = {r[0] for r in cur.fetchall()}
    return _KRX_HOLIDAYS_CACHE


def _is_market_closed(d: date) -> bool:
    if d.weekday() >= 5:        # 토(5)·일(6)
        return True
    return d in _load_krx_holidays()


# ── 디스크 캐시 ───────────────────────────────
# 한 번 받은 list.json/document.xml은 디스크에 저장 → 재실행 시 API 호출 없이 즉시 활용
CACHE_DIR  = project_root / "cache" / "dart"
LIST_CACHE = CACHE_DIR / "list"      # {YYYYMMDD}_{YYYYMMDD}.json
DOC_CACHE  = CACHE_DIR / "document"  # {rcp_no}.xml


def _list_cache_path(bgn_de: str, end_de: str) -> Path:
    return LIST_CACHE / f"{bgn_de}_{end_de}.json"


def _doc_cache_path(rcp_no: str) -> Path:
    return DOC_CACHE / f"{rcp_no}.xml"


def cache_get_list(bgn_de: str, end_de: str) -> Optional[list[dict]]:
    p = _list_cache_path(bgn_de, end_de)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def cache_set_list(bgn_de: str, end_de: str, items: list[dict]) -> None:
    LIST_CACHE.mkdir(parents=True, exist_ok=True)
    p = _list_cache_path(bgn_de, end_de)
    p.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def cache_get_doc(rcp_no: str) -> Optional[str]:
    p = _doc_cache_path(rcp_no)
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def cache_set_doc(rcp_no: str, xml: str) -> None:
    DOC_CACHE.mkdir(parents=True, exist_ok=True)
    p = _doc_cache_path(rcp_no)
    p.write_text(xml, encoding="utf-8")


# ── DB ─────────────────────────────────────────

def _conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


def fetch_existing_rcp_nos(conn) -> set[str]:
    """이미 적재된 DART 접수번호 집합."""
    with conn.cursor() as cur:
        cur.execute("SELECT dart_rcp_no FROM dividends WHERE dart_rcp_no IS NOT NULL")
        return {r[0] for r in cur.fetchall()}


def fetch_business_days(conn, start: date, end: date) -> list[date]:
    """ohlcv_daily 의 거래일 (정렬 ASC)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT time FROM ohlcv_daily "
            "WHERE time BETWEEN %s AND %s ORDER BY time",
            (start, end),
        )
        return [r[0] for r in cur.fetchall()]


def business_day_before(business_days: list[date], target: date) -> Optional[date]:
    """
    target 직전 영업일 (target 자체는 제외).

    1) target이 ohlcv_daily 범위 안: 정확한 거래일 사용
    2) target이 미래 (ohlcv max 이후): weekday() 기반 추정 (토/일 제외, 한국 공휴일은 미고려)
       → record_date 도래 후 ohlcv가 채워지면 별도 보정 로직(refresh_future_ex_dates)으로 정확화
    """
    import bisect
    if not business_days:
        return _weekday_based_prev(target)
    if target <= business_days[-1]:
        idx = bisect.bisect_left(business_days, target)
        if idx == 0:
            return None
        return business_days[idx - 1]
    # 미래: weekday 추정
    return _weekday_based_prev(target)


def _weekday_based_prev(target: date) -> Optional[date]:
    """
    target 직전 한국 시장 영업일 (월~금 + KR 공휴일 + 근로자의 날 제외).
    ohlcv_daily 캘린더 범위 밖일 때 fallback.
    """
    d = target - timedelta(days=1)
    while _is_market_closed(d):
        d -= timedelta(days=1)
    return d


def refresh_future_ex_dates(conn) -> int:
    """
    모든 dividends row의 ex_date를 재계산.
    - ohlcv_daily 범위 안: 정확한 거래일 사용
    - ohlcv 범위 밖 (미래): holidays.KR + 근로자의 날 적용한 weekday 추정
    매일 daily_update에서 호출 → ohlcv가 추가될 때마다 미래 ex_date 점차 정확해짐.
    반환: UPDATE된 row 수
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, record_date, ex_date FROM dividends
             WHERE record_date IS NOT NULL
        """)
        rows = cur.fetchall()
    if not rows:
        return 0

    rd_max = max(r[1] for r in rows)
    rd_min = min(r[1] for r in rows)
    biz_days = fetch_business_days(conn, rd_min - timedelta(days=10),
                                          rd_max + timedelta(days=10))

    updated = 0
    with conn.cursor() as cur:
        for did, rd, old_ex in rows:
            new_ex = business_day_before(biz_days, rd)
            if new_ex and new_ex != old_ex:
                cur.execute("UPDATE dividends SET ex_date = %s WHERE id = %s",
                            (new_ex, did))
                updated += 1
    conn.commit()
    return updated


# ── 날짜 청크 ────────────────────────────────

def month_chunks(start: date, end: date):
    """[start, end] 구간을 월 단위로 (cur_start, cur_end) 튜플 yield. (deprecated)"""
    cur = start.replace(day=1)
    while cur <= end:
        if cur.month == 12:
            month_end = cur.replace(day=31)
        else:
            next_month = cur.replace(month=cur.month + 1, day=1)
            month_end = next_month - timedelta(days=1)
        yield max(cur, start), min(month_end, end)
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1, day=1)
        else:
            cur = cur.replace(month=cur.month + 1, day=1)


def week_chunks(start: date, end: date):
    """주별 chunk (deprecated — 결산기 한 주 5,000건 한도 초과 가능)."""
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=6), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def day_chunks(start: date, end: date):
    """
    [start, end] 구간을 일별로 yield.
    DART list.json 한 호출 5,000건 한도 회피의 가장 안전한 방식 (하루 최대 ~3,200건 관찰).
    """
    cur = start
    while cur <= end:
        yield cur, cur
        cur = cur + timedelta(days=1)


# ── 메인 ────────────────────────────────────

INSERT_SQL = """
INSERT INTO dividends (
    code, fiscal_year, period, version, is_latest,
    board_resolution_date, announced_at, record_date, ex_date, pay_date,
    amount, yield_pct, dividend_type,
    confirmed, source, dart_rcp_no, raw_text_url, corp_name
) VALUES (
    %(code)s, %(fiscal_year)s, %(period)s, %(version)s, %(is_latest)s,
    %(board_resolution_date)s, %(announced_at)s, %(record_date)s, %(ex_date)s, %(pay_date)s,
    %(amount)s, %(yield_pct)s, %(dividend_type)s,
    %(confirmed)s, %(source)s, %(dart_rcp_no)s, %(raw_text_url)s, %(corp_name)s
)
ON CONFLICT (code, fiscal_year, period, version) DO NOTHING
"""

UNFLAG_LATEST_SQL = """
UPDATE dividends
   SET is_latest = FALSE
 WHERE code = %s AND fiscal_year = %s AND period = %s
   AND is_latest = TRUE
   AND version < %s
"""


def run_backfill(start_date: date, end_date: date, workers: int = 4,
                 dry_run: bool = False) -> dict:
    print("=" * 70)
    print(f"DART 배당결정 백필: {start_date} ~ {end_date}")
    print("=" * 70)

    client = DartClient()

    # 1) 회사코드 매핑
    print("\n[1/5] 회사 코드 매핑 다운로드 중...")
    corp_map = client.get_corp_code_map()
    corp_to_stock: dict[str, str] = {}
    for stock_code, corp_code in corp_map.items():
        corp_to_stock[corp_code] = stock_code
    print(f"  → 종목 매핑 {len(corp_map):,}개")

    # 2) 날짜 청크별 list.json 검색 (일별 chunk, 캐시 사용)
    # ※ DART list.json은 한 호출 5,000건 한도 → 주별도 결산기 peak week에서 누락 발생 → 일별이 안전
    print("\n[2/5] 일 단위 공시 검색 중 (캐시 사용)...")
    all_decisions: list[dict] = []
    chunks = list(day_chunks(start_date, end_date))
    cache_hits = 0
    for i, (cs, ce) in enumerate(chunks, 1):
        bgn = cs.strftime("%Y%m%d")
        end = ce.strftime("%Y%m%d")
        items = cache_get_list(bgn, end)
        if items is None:
            items = client.get_dividend_decisions(bgn_de=bgn, end_de=end)
            cache_set_list(bgn, end, items)
            cache_label = "API"
        else:
            cache_hits += 1
            cache_label = "cache"
        all_decisions.extend(items)
        print(f"  [{i:>3}/{len(chunks)}] {cs} ~ {ce}: {len(items)}건  ({cache_label}, 누적 {len(all_decisions):,})")
    print(f"  → 전체 {len(all_decisions):,}건  (cache hit {cache_hits}/{len(chunks)})")

    # 3) 이미 처리한 rcp_no 제외
    print("\n[3/5] 기존 데이터 확인 중...")
    conn = _conn()
    try:
        existing = fetch_existing_rcp_nos(conn)
        print(f"  → 기존 적재 {len(existing):,}건 (스킵)")

        # 비상장사(stock_code 매핑 없음)도 제외
        new_decisions = [
            d for d in all_decisions
            if d["rcept_no"] not in existing
            and d["corp_code"] in corp_to_stock
        ]
        print(f"  → 신규 처리 대상 {len(new_decisions):,}건")

        if dry_run:
            print("\n[DRY-RUN] 실제 다운로드/INSERT 생략. 종료.")
            return {
                "found": len(all_decisions),
                "new": len(new_decisions),
                "parsed": 0, "inserted": 0,
            }

        # 4) document.xml 다운로드 + 파싱 (병렬)
        print(f"\n[4/5] 본문 다운로드/파싱 중 (workers={workers})...")
        parsed_records = _download_and_parse(client, new_decisions, corp_to_stock, workers)
        print(f"  → 파싱 성공 {len(parsed_records):,}건 / 시도 {len(new_decisions):,}건")

        # 5) ex_date 산출 + version/is_latest 부여 + INSERT
        print("\n[5/5] ex_date 산출 + version 부여 + INSERT 중...")
        # ex_date 계산용 영업일 캘린더 (record_date 있는 경우만)
        rd_list = [r["record_date"] for r in parsed_records if r.get("record_date")]
        if rd_list:
            min_rd = min(rd_list)
            max_rd = max(rd_list)
            biz_days = fetch_business_days(conn, min_rd - timedelta(days=10), max_rd + timedelta(days=10))
        else:
            biz_days = []

        rows = _assign_version_and_ex(parsed_records, biz_days)
        inserted = _insert_rows(conn, rows)
        print(f"  → INSERT {inserted:,}건")

        return {
            "found": len(all_decisions),
            "new": len(new_decisions),
            "parsed": len(parsed_records),
            "inserted": inserted,
        }
    finally:
        conn.close()


def _download_and_parse(client: DartClient, decisions: list[dict],
                        corp_to_stock: dict[str, str], workers: int) -> list[dict]:
    parsed: list[dict] = []
    fail = 0

    def _one(d: dict) -> Optional[dict]:
        nonlocal fail
        rcp_no = d["rcept_no"]
        try:
            xml = cache_get_doc(rcp_no)
            if xml is None:
                xml = client.get_document_xml(rcp_no)
                if xml:
                    cache_set_doc(rcp_no, xml)
            if not xml:
                return None
            # 본문에서 자회사 배당 2차 필터
            if DartClient.is_subsidiary_disclosure(xml):
                return None
            p = DartClient.parse_dividend_decision(xml)
            # amount는 필수 (없으면 보통주가 없는 우선주만 공시)
            if not p.get("amount"):
                return None

            stock_code = corp_to_stock.get(d["corp_code"])
            if not stock_code:
                return None

            # 공시 접수일시: rcept_dt(YYYYMMDD) 자정 가정
            rcept_dt_str = d.get("rcept_dt", "")
            try:
                announced_at = datetime.strptime(rcept_dt_str, "%Y%m%d")
            except ValueError:
                announced_at = None

            # record_date 추출 (있으면). 없으면(정관변경 미정 공시) NULL 허용.
            rd = None
            if p.get("record_date"):
                try:
                    rd = datetime.strptime(p["record_date"], "%Y-%m-%d").date()
                except ValueError:
                    pass

            # fiscal_year 추정 우선순위: record_date → board_resolution_date → announced_at
            fiscal_year = None
            if rd:
                fiscal_year = rd.year
            elif p.get("board_resolution_date"):
                try:
                    fiscal_year = datetime.strptime(
                        p["board_resolution_date"], "%Y-%m-%d").date().year
                except ValueError:
                    pass
            if fiscal_year is None and announced_at:
                fiscal_year = announced_at.year
            if fiscal_year is None:
                return None  # 어떤 연도도 결정 불가

            return {
                "code": stock_code,
                "fiscal_year": fiscal_year,
                "period": p.get("period") or "ANNUAL",
                "announced_at": announced_at,
                "board_resolution_date": p.get("board_resolution_date"),
                "record_date": rd,
                "pay_date": p.get("pay_date"),
                "amount": p.get("amount"),
                "yield_pct": p.get("yield_pct"),
                "dart_rcp_no": rcp_no,
                "raw_text_url": DartClient.make_raw_text_url(rcp_no),
                "corp_name": (d.get("corp_name") or "").strip() or None,
            }
        except Exception:
            fail += 1
            return None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_one, d) for d in decisions]
        done = 0
        for fut in as_completed(futures):
            done += 1
            r = fut.result()
            if r:
                parsed.append(r)
            if done % 200 == 0:
                print(f"  [{done:>5}/{len(decisions)}] 진행 (성공 {len(parsed):,} / 실패 {fail})")
    return parsed


def _assign_version_and_ex(records: list[dict], biz_days: list[date]) -> list[dict]:
    """(code, fiscal_year, period) 그룹별로 announced_at ASC 정렬 → version + is_latest."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        key = (r["code"], r["fiscal_year"], r["period"])
        groups[key].append(r)

    out: list[dict] = []
    for key, group in groups.items():
        # announced_at None은 맨 앞으로 (드물 것)
        group.sort(key=lambda x: (x["announced_at"] or datetime.min))
        for i, r in enumerate(group, 1):
            r["version"] = i
            r["is_latest"] = (i == len(group))
            r["confirmed"] = True
            r["dividend_type"] = "CASH"
            r["source"] = "DART"
            # ex_date = record_date 직전 영업일 (record_date 없으면 NULL)
            r["ex_date"] = (
                business_day_before(biz_days, r["record_date"])
                if biz_days and r.get("record_date") else None
            )
            # pay_date 문자열 → date
            if isinstance(r.get("pay_date"), str):
                try:
                    r["pay_date"] = datetime.strptime(r["pay_date"], "%Y-%m-%d").date()
                except ValueError:
                    r["pay_date"] = None
            # board_resolution_date 문자열 → date
            if isinstance(r.get("board_resolution_date"), str):
                try:
                    r["board_resolution_date"] = datetime.strptime(
                        r["board_resolution_date"], "%Y-%m-%d").date()
                except ValueError:
                    r["board_resolution_date"] = None
            out.append(r)
    return out


def _insert_rows(conn, rows: list[dict]) -> int:
    if not rows:
        return 0

    # 같은 그룹의 기존 is_latest=TRUE 가 있으면 FALSE로 (재실행 시 안전)
    with conn.cursor() as cur:
        for r in rows:
            if r["is_latest"]:
                cur.execute(UNFLAG_LATEST_SQL,
                            (r["code"], r["fiscal_year"], r["period"], r["version"]))

        psycopg2.extras.execute_batch(cur, INSERT_SQL, rows, page_size=200)
    conn.commit()
    return len(rows)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DART 배당결정 공시 백필")
    parser.add_argument("start", help="시작일 YYYYMMDD")
    parser.add_argument("end", help="종료일 YYYYMMDD")
    parser.add_argument("--workers", type=int, default=4, help="병렬 다운로드 워커 수")
    parser.add_argument("--dry-run", action="store_true", help="실제 INSERT 생략")
    args = parser.parse_args()

    t0 = time.time()
    result = run_backfill(_parse_date(args.start), _parse_date(args.end),
                          workers=args.workers, dry_run=args.dry_run)
    elapsed = time.time() - t0
    print()
    print("=" * 70)
    print(f"완료: 검색 {result['found']:,}건 / 신규 {result['new']:,}건 / "
          f"파싱 {result['parsed']:,}건 / INSERT {result['inserted']:,}건")
    print(f"소요: {elapsed/60:.1f}분")
    print("=" * 70)
