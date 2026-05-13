"""
LS API v5: 신 TR 검증 (t8465/t8467/t8435) + 주식선물 차트 핵심 검증

배경:
  LS 5/28 deprecate 공지:
    t8415 → t8465 (선물/옵션 N분차트)
    t8432 → t8467 (지수선물 마스터)
    t8414 → t8464 (틱)
    t8416 → t8466 (일주월)
  + t8435 = 파생종목마스터 (주식선물 포함 가능성)

검증:
  1) t8465 KOSPI200 선물(A0166000) 분차트 — t8415 대체 동작 확인
  2) t8465 주식선물(A0A65000 TKG휴켐스) 분차트 — ⭐ 핵심
  3) t8465 다양한 주식선물 코드 (t8401에서 발견된 것들)
  4) t8467 지수선물 마스터
  5) t8435 파생종목 마스터 (gubun="SF" 주식선물)
"""

from __future__ import annotations

import json
import sys
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
        return {"status": r.status_code, "url": url,
                "body": r.json() if r.status_code == 200 else r.text}
    except Exception as e:
        return {"status": -1, "url": url, "error": str(e)}


def short(d, mx=300):
    s = json.dumps(d, ensure_ascii=False, default=str)
    return s if len(s) <= mx else s[:mx] + "..."


def main():
    cli = LsApiClient()

    URL_FUTOPT = f"{BASE_URL}/futureoption/chart"
    URL_FOMD   = f"{BASE_URL}/futureoption/market-data"

    # ─ 0. t8401에서 주식선물 코드 sample 받기 (gubun=1 - 주식선물 추정) ─
    print("=" * 92)
    print("### 0) t8401 주식선물 마스터 — sample 추출")
    print("=" * 92)
    body = {"t8401InBlock": {"gubun": "1"}}
    r = call(cli, "t8401", body, URL_FOMD)
    master = (r.get("body") or {}).get("t8401OutBlock") or []
    # F (선물) 만 필터, SP (스프레드) 제외
    futures = [m for m in master if m.get("hname", "").find("F") >= 0 and m.get("hname", "").find("SP") < 0]
    sp = [m for m in master if "SP" in m.get("hname", "")]
    print(f"  total={len(master)} futures={len(futures)} spread={len(sp)}")
    # 처음 5건 + KOSPI200 (A0166000) 명시 포함 시도
    samples = []
    for prefix_first in ["A", "1", "0", "2", "3"]:
        for m in master:
            if m.get("shcode", "").startswith(prefix_first) and m not in samples:
                samples.append(m)
                break
    explicit = [m for m in master if m.get("shcode") in ("A0166000",)]
    samples = explicit + samples
    print(f"  sample {len(samples)}건:")
    for m in samples[:8]:
        print(f"     {short(m, 200)}")
    samples = samples[:8]

    # ─ 1. t8465 KOSPI200 선물 (A0166000) 분차트 검증 ─
    print()
    print("=" * 92)
    print("### 1) t8465 — KOSPI200 선물 A0166000 분차트")
    print("=" * 92)
    body = {"t8465InBlock": {
        "shcode": "A0166000", "ncnt": 1, "qrycnt": 5, "nday": "1",
        "sdate": "20260512", "edate": "20260513",
        "cts_date": "", "cts_time": "", "comp_yn": "N"
    }}
    r = call(cli, "t8465", body, URL_FUTOPT)
    body_d = r.get("body", {})
    if isinstance(body_d, dict):
        meta = body_d.get("t8465OutBlock", {})
        rows = body_d.get("t8465OutBlock1", [])
        print(f"  status={r['status']}  rsp_msg={body_d.get('rsp_msg','')}")
        print(f"  meta = {short(meta, 300)}")
        print(f"  rows count = {len(rows) if isinstance(rows, list) else 0}")
        if rows:
            print(f"  first = {short(rows[0])}")
            print(f"  last  = {short(rows[-1])}")
    else:
        print(f"  body = {short(body_d, 400)}")

    # ─ 2. t8465 주식선물 차트 ─⭐핵심⭐
    print()
    print("=" * 92)
    print("### 2) t8465 — 주식선물 차트 (t8401 발견 코드)")
    print("=" * 92)
    for m in samples:
        sh = m.get("shcode", "")
        hname = m.get("hname", "")
        body = {"t8465InBlock": {
            "shcode": sh, "ncnt": 1, "qrycnt": 3, "nday": "1",
            "sdate": "20260512", "edate": "20260513",
            "cts_date": "", "cts_time": "", "comp_yn": "N"
        }}
        r = call(cli, "t8465", body, URL_FUTOPT)
        body_d = r.get("body", {})
        if isinstance(body_d, dict):
            rsp = body_d.get("rsp_msg", "")
            rows = body_d.get("t8465OutBlock1", [])
            print(f"  shcode={sh:10s} ({hname:35s}) status={r['status']} rows={len(rows) if isinstance(rows,list) else 0} rsp={rsp}")
            if rows:
                print(f"     first = {short(rows[0], 200)}")
        else:
            print(f"  shcode={sh:10s} ({hname:35s}) status={r['status']} body={short(body_d, 200)}")

    # ─ 3. t8467 지수선물 마스터 ─
    print()
    print("=" * 92)
    print("### 3) t8467 지수선물 마스터 (t8432 신 TR)")
    print("=" * 92)
    for gubun in ["F", "1", ""]:
        body = {"t8467InBlock": {"gubun": gubun}}
        r = call(cli, "t8467", body, URL_FOMD)
        body_d = r.get("body", {})
        if isinstance(body_d, dict):
            rows = body_d.get("t8467OutBlock", [])
            print(f"  gubun={gubun!r:5s} status={r['status']} rows={len(rows) if isinstance(rows,list) else 0} rsp={body_d.get('rsp_msg','')}")
            if isinstance(rows, list) and rows:
                print(f"     first = {short(rows[0], 200)}")
                print(f"     last  = {short(rows[-1], 200)}")

    # ─ 4. t8435 파생종목 마스터 ─
    print()
    print("=" * 92)
    print("### 4) t8435 파생종목 마스터 (gubun 다양)")
    print("=" * 92)
    for gubun in ["SF", "F", "FO", "1", ""]:
        body = {"t8435InBlock": {"gubun": gubun}}
        r = call(cli, "t8435", body, URL_FOMD)
        body_d = r.get("body", {})
        if isinstance(body_d, dict):
            rows = body_d.get("t8435OutBlock", [])
            print(f"  gubun={gubun!r:5s} status={r['status']} rows={len(rows) if isinstance(rows,list) else 0} rsp={body_d.get('rsp_msg','')}")
            if isinstance(rows, list) and rows:
                print(f"     first = {short(rows[0], 200)}")
                print(f"     last  = {short(rows[-1], 200)}")


if __name__ == "__main__":
    main()
