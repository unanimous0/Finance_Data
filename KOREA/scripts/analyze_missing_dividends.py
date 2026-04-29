"""
파싱 누락된 DART 배당결정 공시 분석

cache/dart/document/*.xml 에 저장된 모든 본문 중 DB(dividends 테이블)에
들어가지 못한 rcp_no를 식별하고, 본문에서 어떤 패턴이 누락되어 있는지 보고.

목적:
- 어떤 양식 차이로 파싱이 실패하는지 확인
- DartClient.parse_dividend_decision 보강 포인트 도출

실행:
    python scripts/analyze_missing_dividends.py [--samples 10] [--save-report PATH]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg2

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings
from collectors.dart import DartClient

DOC_CACHE = project_root / "cache" / "dart" / "document"


def _conn():
    return psycopg2.connect(
        host=settings.DB_HOST,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )


def fetch_db_rcp_nos() -> set[str]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT dart_rcp_no FROM dividends WHERE dart_rcp_no IS NOT NULL")
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def list_cached_rcp_nos() -> list[str]:
    if not DOC_CACHE.exists():
        return []
    return [p.stem for p in DOC_CACHE.glob("*.xml")]


def normalize_text(xml: str) -> str:
    text = re.sub(r"<[^>]+>", " ", xml)
    text = re.sub(r"\s+", " ", text)
    return text


def diagnose_one(text: str) -> dict:
    """한 본문에서 어떤 핵심 키워드가 보이는지 확인."""
    return {
        "has_amount_label": bool(re.search(r"1주당\s*(?:현금|현물)?\s*배당금", text)),
        "has_yield_label":  bool(re.search(r"시가배당[률율]", text)),
        "has_record_label": bool(re.search(r"배당기준일", text)),
        "has_board_label":  bool(re.search(r"이사회\s*결의일", text)),
        "has_class_label":  bool(re.search(r"배당구분", text)),
        # 보통주 명시 여부 (없으면 ETF/우선주만일 가능성)
        "has_common_share": bool(re.search(r"보통주", text)),
        # 종류주 only?
        "has_only_pref":    bool(re.search(r"종류주식|우선주", text)) and not re.search(r"보통주", text),
        # 길이 (짧은 본문 = 거의 빈 공시?)
        "text_len": len(text),
    }


def main():
    parser = argparse.ArgumentParser(description="파싱 누락 DART 공시 분석")
    parser.add_argument("--samples", type=int, default=10,
                        help="대표 샘플 출력 건수 (기본: 10)")
    parser.add_argument("--save-report", type=str, default=None,
                        help="누락 rcp_no + 본문 1000자 샘플을 텍스트로 저장")
    args = parser.parse_args()

    print(f"캐시 디렉토리: {DOC_CACHE}")
    cached = set(list_cached_rcp_nos())
    print(f"캐시된 본문: {len(cached):,}건")

    in_db = fetch_db_rcp_nos()
    print(f"DB 적재된 rcp_no: {len(in_db):,}건")

    missing = sorted(cached - in_db)
    print(f"누락 (캐시 있음, DB 없음): {len(missing):,}건")
    if not missing:
        print("→ 누락 없음. 종료.")
        return

    # 1. 진단 통계
    print("\n=== 누락 본문 진단 통계 ===")
    diag_counter = Counter()
    diagnoses = {}
    for rcp_no in missing:
        xml = (DOC_CACHE / f"{rcp_no}.xml").read_text(encoding="utf-8", errors="replace")
        text = normalize_text(xml)
        d = diagnose_one(text)
        diagnoses[rcp_no] = (d, text)
        for key, val in d.items():
            if isinstance(val, bool) and val:
                diag_counter[key] += 1

    for label, count in diag_counter.most_common():
        print(f"  {label:25s}: {count:>4}건 ({count*100//len(missing)}%)")

    # 2. 분류
    print("\n=== 누락 사유 분류 ===")
    cats = {
        "보통주 패턴 없음 (ETF/우선주만)": [],
        "텍스트 너무 짧음 (<300자)":    [],
        "기준일 라벨 없음":            [],
        "보통주는 있으나 amount 매칭 실패": [],
        "기타":                       [],
    }
    for rcp_no, (d, text) in diagnoses.items():
        if d["text_len"] < 300:
            cats["텍스트 너무 짧음 (<300자)"].append(rcp_no)
        elif not d["has_record_label"]:
            cats["기준일 라벨 없음"].append(rcp_no)
        elif not d["has_common_share"]:
            cats["보통주 패턴 없음 (ETF/우선주만)"].append(rcp_no)
        elif d["has_amount_label"]:
            # 라벨은 있으나 정규식이 못 잡음
            cats["보통주는 있으나 amount 매칭 실패"].append(rcp_no)
        else:
            cats["기타"].append(rcp_no)

    for cat, rcp_list in cats.items():
        print(f"  {cat}: {len(rcp_list)}건")

    # 3. 카테고리별 샘플
    print(f"\n=== 카테고리별 샘플 (최대 {args.samples}건씩) ===")
    for cat, rcp_list in cats.items():
        if not rcp_list:
            continue
        print(f"\n--- [{cat}] ---")
        for rcp_no in rcp_list[:args.samples]:
            text = diagnoses[rcp_no][1]
            print(f"\nrcp_no={rcp_no}  (길이 {len(text)})")
            print(f"  URL: {DartClient.make_raw_text_url(rcp_no)}")
            # 배당 키워드 주변 발췌
            snippets = []
            for keyword in ["배당구분", "1주당", "배당기준일", "보통주식", "종류주식"]:
                m = re.search(keyword, text)
                if m:
                    s = max(0, m.start() - 30)
                    e = min(len(text), m.end() + 150)
                    snippets.append(f"    [{keyword}] ...{text[s:e]}...")
            for sn in snippets[:3]:
                print(sn)

    # 4. 저장 옵션
    if args.save_report:
        out_path = Path(args.save_report)
        with out_path.open("w", encoding="utf-8") as f:
            f.write(f"누락 분석 보고 — 총 {len(missing)}건\n\n")
            for cat, rcp_list in cats.items():
                f.write(f"\n[{cat}] {len(rcp_list)}건\n")
                for rcp_no in rcp_list:
                    f.write(f"  {rcp_no}  {DartClient.make_raw_text_url(rcp_no)}\n")
                    text = diagnoses[rcp_no][1][:1000]
                    f.write(f"    {text}\n\n")
        print(f"\n→ 보고서 저장: {out_path}")


if __name__ == "__main__":
    main()
