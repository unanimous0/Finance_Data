"""
종목별 정관변경 분류 (charter_group A/B)

전략:
- dividends 테이블에 있는 583종목 대상
- 각 종목의 4년치 주주총회 공시 (소집공고/결의/결과) 리스트 → 최신 순 정렬
- 본문 다운로드 + 배당 관련 정관 변경 안건 분석
- 첫 hit 발견 시 분류 결정
- 모든 공시 분석 후 정관변경 안건 없으면 → 디폴트 B

판별 패턴:
- A 그룹 (정관변경): "이사회결의로 ... 배당 ... 기준일", "배당기준일 명시내용 삭제" 등
- B 그룹 (미변경): 변경 안건 없음 또는 "결산기 말일" 유지 표현

캐시: cache/dart/document/{rcp_no}.xml (기존과 공유)

실행:
    python scripts/classify_charter_groups.py
    python scripts/classify_charter_groups.py --workers 4
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings
from collectors.dart import DartClient
from scripts.backfill_dividends import (
    cache_get_doc, cache_set_doc,
)

CLASSIFICATION_RESULT_PATH = project_root / "cache" / "dart" / "charter_classification.json"


# 주총 공시 매칭 (회사별 검색 결과에서 필터)
SHM_PATTERN = re.compile(r"주주총회(소집공고|소집결의|결과)")

# 배당기준일 + 이사회 결의 → A 그룹 패턴들
A_PATTERNS = [
    re.compile(r"배당기준일\s*명시내용\s*(을\s*)?삭제"),
    re.compile(r"이사회\s*결의(로|일)?\s*[^.]{0,80}?배당[^.]{0,80}?기준일"),
    re.compile(r"이사회\s*결의로\s*[^.]{0,80}?정하는\s*날"),
    re.compile(r"배당[^.]{0,30}?기준일[^.]{0,80}?이사회\s*결의"),
]

# 명백한 B 그룹 패턴 (전통 방식 유지/회복)
B_PATTERNS = [
    re.compile(r"배당기준일[^.]{0,30}?(매\s*)?결산기?\s*말일"),
    re.compile(r"배당기준일[^.]{0,30}?매년\s*12월\s*31일"),
]


# ── DB ─────────────────────────────────────────

def _conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


def fetch_target_codes(conn) -> list[str]:
    """dividends에 있는 모든 종목 코드."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT code FROM dividends ORDER BY code")
        return [r[0] for r in cur.fetchall()]


def update_charter_group(conn, code: str, group: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE dividends SET charter_group = %s WHERE code = %s",
            (group, code),
        )


# ── 분류 로직 ─────────────────────────────────

def classify_text(text: str) -> Optional[str]:
    """본문 평문에서 A/B 분류. 어느 쪽도 매칭 안 되면 None."""
    # A 우선
    for p in A_PATTERNS:
        if p.search(text):
            return "A"
    for p in B_PATTERNS:
        if p.search(text):
            return "B"
    return None


def classify_one_code(client: DartClient, corp_code: str,
                      bgn_de: str, end_de: str) -> tuple[Optional[str], int, Optional[str]]:
    """
    한 종목의 주총 공시들을 최근 → 과거 순으로 본문 분석.
    반환: (분류 결과 'A'/'B'/None, 분석한 본문 수, 결정 근거 rcp_no)
    """
    # 1) 모든 공시 검색 후 주총 관련만 필터
    all_items = client.search_disclosures(corp_code=corp_code,
                                          bgn_de=bgn_de, end_de=end_de)
    shm_items = [x for x in all_items
                 if SHM_PATTERN.search((x.get("report_nm") or "").strip())]
    # 최신 → 과거 순
    shm_items.sort(key=lambda x: x.get("rcept_dt") or "", reverse=True)

    bodies_checked = 0
    for item in shm_items:
        rcp_no = item["rcept_no"]
        xml = cache_get_doc(rcp_no)
        if xml is None:
            xml = client.get_document_xml(rcp_no)
            if xml:
                cache_set_doc(rcp_no, xml)
        if not xml:
            continue
        bodies_checked += 1

        # 평문화
        text = re.sub(r"<[^>]+>", " ", xml)
        text = re.sub(r"\s+", " ", text)

        # 본문에 "배당기준일" 단어 자체가 없으면 정관변경 안건이 배당과 무관 → 다음 공시
        if "배당기준일" not in text:
            continue

        result = classify_text(text)
        if result:
            return result, bodies_checked, rcp_no

    return None, bodies_checked, None


# ── 메인 ────────────────────────────────────

def run(start_date: date, end_date: date, workers: int = 4):
    print("=" * 70)
    print(f"종목별 정관변경 분류 (charter_group)  기간: {start_date} ~ {end_date}")
    print("=" * 70)

    client = DartClient()
    print("\n[1/4] corp_code 매핑 다운로드 중...")
    corp_map = client.get_corp_code_map()
    print(f"  → {len(corp_map):,}개 매핑")

    conn = _conn()
    try:
        codes = fetch_target_codes(conn)
        print(f"\n[2/4] 분류 대상 종목: {len(codes):,}개")

        # 종목 → corp_code 매핑 (dividends에 있는 것만)
        targets = []
        unmapped = 0
        for code in codes:
            cc = corp_map.get(code)
            if cc:
                targets.append((code, cc))
            else:
                unmapped += 1
        print(f"  → 매핑 가능 {len(targets):,}개 / 매핑 실패 {unmapped}개")

        # 3) 종목별 분류
        print(f"\n[3/4] 본문 다운로드 + 분류 (workers={workers})...")
        bgn_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")

        results: dict[str, tuple[Optional[str], int, Optional[str]]] = {}

        def _worker(item):
            code, corp_code = item
            try:
                return code, classify_one_code(client, corp_code, bgn_str, end_str)
            except Exception as e:
                return code, (None, 0, f"ERR:{type(e).__name__}")

        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_worker, t) for t in targets]
            for fut in as_completed(futures):
                code, info = fut.result()
                results[code] = info
                done += 1
                if done % 50 == 0:
                    a_cnt = sum(1 for v in results.values() if v[0] == "A")
                    b_cnt = sum(1 for v in results.values() if v[0] == "B")
                    none_cnt = sum(1 for v in results.values() if v[0] is None)
                    print(f"  [{done:>4}/{len(targets)}] A={a_cnt} B={b_cnt} 미정={none_cnt}")

        # 4) DB UPDATE — A/B만 채우고 미정은 NULL 유지 (디폴트 B 강제 안 함)
        print(f"\n[4/4] dividends 테이블 charter_group UPDATE...")
        a_codes = [c for c, (g, _, _) in results.items() if g == "A"]
        b_codes_explicit = [c for c, (g, _, _) in results.items() if g == "B"]
        none_codes = [c for c, (g, _, _) in results.items() if g is None]

        with conn.cursor() as cur:
            for code in a_codes:
                cur.execute("UPDATE dividends SET charter_group='A' WHERE code=%s", (code,))
            for code in b_codes_explicit:
                cur.execute("UPDATE dividends SET charter_group='B' WHERE code=%s", (code,))
            # 미정 종목: NULL 유지 (verify 스크립트에서 record_date 휴리스틱과 cross-check 후 사람이 결정)
        conn.commit()

        print(f"  → A 그룹 (정관변경): {len(a_codes):,}개")
        print(f"  → B 그룹 (명시적 미변경): {len(b_codes_explicit):,}개")
        print(f"  → 미정 (NULL 유지): {len(none_codes):,}개  ※ verify 스크립트로 휴리스틱과 비교")
        print(f"  → 매핑 실패 (UPDATE 안 함): {unmapped}개")

        # 5) 결과 JSON 저장 (verify에서 활용)
        CLASSIFICATION_RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        result_dump = {
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "start_date": start_date.isoformat(),
            "end_date":   end_date.isoformat(),
            "results": {
                code: {
                    "group":          g,
                    "bodies_checked": n,
                    "decision_rcp":   r,
                }
                for code, (g, n, r) in results.items()
            },
        }
        CLASSIFICATION_RESULT_PATH.write_text(
            json.dumps(result_dump, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"  → 분류 결과 저장: {CLASSIFICATION_RESULT_PATH}")

        return results
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="종목별 정관변경 분류 (charter_group)")
    parser.add_argument("--start", default="20220101", help="검색 시작일 YYYYMMDD")
    parser.add_argument("--end", default=None, help="검색 종료일 YYYYMMDD (기본: 오늘)")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y%m%d").date()
    end = datetime.strptime(args.end, "%Y%m%d").date() if args.end else date.today()

    t0 = time.time()
    run(start, end, workers=args.workers)
    print(f"\n소요: {(time.time() - t0) / 60:.1f}분")
