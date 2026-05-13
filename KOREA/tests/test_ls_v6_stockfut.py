"""
LS API v6: 주식선물 진짜 활성 코드 발견

배경:
  - t8401 master는 shcode "A로 시작" (예: A0A65000) 반환 — t8465 차트 ❌
  - ls_api_full.md t8401 doc 예시는 "1로 시작" (예: 111T7000)  — 시리즈 다름
  - t8402(주식선물 현재가, TPS 10)로 active 종목 발견

전략:
  1. t8401 응답을 prefix별로 분류 (A/1/0/2/3/D/4 등)
  2. 각 prefix sample을 t8402에 넣어 가격 0이 아닌 종목 찾음
  3. active 종목으로 t8465 분차트 호출 → 검증
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from collectors.ls_api import LsApiClient, BASE_URL
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def call(client, tr_cd, body, url):
    token = client._get_token()
    headers = {
        "content-type":  "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "tr_cd":         tr_cd,
        "tr_cont":       "N",
        "tr_cont_key":   "",
        "mac_address":   "",
        "Connection":    "close",
    }
    client._throttle()
    client._refresh_session()
    try:
        r = client.session.post(url, json=body, headers=headers, timeout=(10, 30), verify=False)
        return {"status": r.status_code,
                "body": r.json() if r.status_code == 200 else r.text}
    except Exception as e:
        return {"status": -1, "error": str(e)}


def short(d, mx=200):
    s = json.dumps(d, ensure_ascii=False, default=str)
    return s if len(s) <= mx else s[:mx] + "..."


def main():
    cli = LsApiClient()
    URL_FOMD = f"{BASE_URL}/futureoption/market-data"
    URL_FOC  = f"{BASE_URL}/futureoption/chart"

    # ── 1) t8401 master 분석 ───────────────────────────
    print("=" * 90)
    print("### 1) t8401 master prefix 분포")
    print("=" * 90)
    body = {"t8401InBlock": {"dummy": ""}}
    r = call(cli, "t8401", body, URL_FOMD)
    master = (r.get("body") or {}).get("t8401OutBlock") or []
    print(f"  total rows: {len(master)}")

    by_prefix = defaultdict(list)
    by_year = defaultdict(int)  # 만기 연도별
    for m in master:
        sh = m.get("shcode", "")
        if sh:
            by_prefix[sh[0]].append(m)
        # 만기연도 hname에서 추출
        hname = m.get("hname", "")
        # "F 202605" 또는 "F 2606" 형식
        for token in hname.split():
            if token.isdigit():
                if len(token) == 6:  # YYYYMM
                    by_year[token[:4]] += 1
                elif len(token) == 4:  # YYMM
                    yy = "20" + token[:2]
                    by_year[yy] += 1

    print(f"\n  shcode prefix별 개수:")
    for p in sorted(by_prefix.keys()):
        rows = by_prefix[p]
        print(f"    prefix='{p}' : {len(rows)}건  sample: {short(rows[0], 150)}")

    print(f"\n  만기 연도별 개수:")
    for y in sorted(by_year.keys()):
        print(f"    {y}: {by_year[y]}건")

    # ── 2) 2026년 만기 종목만 추출 (현재 활성 후보) ─────
    print()
    print("=" * 90)
    print("### 2) 2026년 만기 active 후보 (각 prefix별 1개씩)")
    print("=" * 90)
    active_candidates = []
    seen_pref = set()
    for m in master:
        hname = m.get("hname", "")
        sh = m.get("shcode", "")
        if not sh or sh[0] in seen_pref:
            continue
        # hname에 "2026" 포함 또는 "26" 만기
        is_2026 = "2026" in hname or any(t.startswith("26") and t.isdigit() and len(t) == 4 for t in hname.split())
        if is_2026 and "SP" not in hname:
            active_candidates.append(m)
            seen_pref.add(sh[0])

    # 일부러 더 넓게 추가 (KOSPI200 F 등)
    for m in master:
        if m.get("shcode") in ("A0166000",) and m not in active_candidates:
            active_candidates.append(m)
        if len(active_candidates) >= 8:
            break

    for m in active_candidates:
        print(f"  {short(m, 200)}")

    # ── 3) t8402로 active 검증 ────────────────────────
    print()
    print("=" * 90)
    print("### 3) t8402 현재가 호출 — 가격 != 0 면 active")
    print("=" * 90)
    actives = []
    for m in active_candidates:
        sh = m.get("shcode", "")
        body = {"t8402InBlock": {"focode": sh}}
        r = call(cli, "t8402", body, URL_FOMD)
        body_d = r.get("body", {})
        if isinstance(body_d, dict):
            ob = body_d.get("t8402OutBlock", {})
            price = ob.get("price", 0) if isinstance(ob, dict) else 0
            recprice = ob.get("recprice", 0) if isinstance(ob, dict) else 0
            volume = ob.get("volume", 0) if isinstance(ob, dict) else 0
            rsp_msg = body_d.get("rsp_msg", "")
            print(f"  {sh:10s} ({m.get('hname',''):30s}) status={r['status']} "
                  f"price={price} recprice={recprice} volume={volume} rsp={rsp_msg}")
            if price and price != 0:
                actives.append((m, ob))
        else:
            print(f"  {sh:10s} ({m.get('hname',''):30s}) status={r['status']} body={short(body_d, 100)}")

    # ── 4) active 종목으로 t8465 분차트 검증 ──────────
    print()
    print("=" * 90)
    print("### 4) active 종목으로 t8465 분차트 검증 ⭐")
    print("=" * 90)
    if not actives:
        print("  active 0건 — t8402 결과로는 활성 종목 없음")
    else:
        for m, ob in actives:
            sh = m.get("shcode", "")
            body = {"t8465InBlock": {
                "shcode": sh, "ncnt": 1, "qrycnt": 5, "nday": "1",
                "sdate": "20260512", "edate": "20260513",
                "cts_date": "", "cts_time": "", "comp_yn": "N"
            }}
            r = call(cli, "t8465", body, URL_FOC)
            body_d = r.get("body", {})
            if isinstance(body_d, dict):
                rows = body_d.get("t8465OutBlock1", [])
                rsp = body_d.get("rsp_msg", "")
                print(f"  {sh:10s} ({m.get('hname',''):30s}) status={r['status']} "
                      f"rows={len(rows) if isinstance(rows,list) else 0} rsp={rsp}")
                if rows:
                    print(f"     first = {short(rows[0])}")

    # ── 5) 추가 시도: t8401 응답 코드를 t8406로 (주식선물틱분별체결조회) ─
    print()
    print("=" * 90)
    print("### 5) t8406 (주식선물틱분별체결) — t8401 코드 시도")
    print("=" * 90)
    # cgubun: T=틱, M=분 추정
    test_codes = active_candidates[:3]
    for m in test_codes:
        sh = m.get("shcode", "")
        for cg in ["T", "M", "1"]:
            body = {"t8406InBlock": {"focode": sh, "cgubun": cg, "bgubun": 0, "cnt": 5}}
            r = call(cli, "t8406", body, URL_FOMD)
            body_d = r.get("body", {})
            if isinstance(body_d, dict):
                rows = body_d.get("t8406OutBlock1", [])
                rsp = body_d.get("rsp_msg", "")
                print(f"  {sh:10s} cgubun={cg} status={r['status']} rows={len(rows) if isinstance(rows,list) else 0} rsp={rsp}")
                if rows:
                    print(f"     first = {short(rows[0])}")


if __name__ == "__main__":
    main()
