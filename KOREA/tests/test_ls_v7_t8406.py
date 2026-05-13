"""
LS API v7: t8406 정밀 검증 — 봉 간격 + lookback + 페이징

검증:
  1. cgubun="M" cnt=900 → 한 번에 받는 봉 수, 시간 범위
  2. bgubun 0/1/2 차이
  3. 페이징 가능한지 (cts_time 같은 거 응답에 있는지, tr_cont 헤더)
  4. cnt 큰 값으로 과거 lookback (1000+ 가능?)
  5. 다른 종목 sanity (1GNW4000, 0차 prefix 등)
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


def call(client, tr_cd, body, url, tr_cont="N", tr_cont_key=""):
    token = client._get_token()
    headers = {
        "content-type":  "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "tr_cd":         tr_cd,
        "tr_cont":       tr_cont,
        "tr_cont_key":   tr_cont_key,
        "mac_address":   "",
        "Connection":    "close",
    }
    client._throttle()
    client._refresh_session()
    try:
        r = client.session.post(url, json=body, headers=headers, timeout=(10, 30), verify=False)
        # tr_cont response header check
        resp_tr_cont = r.headers.get("tr_cont", "")
        resp_tr_cont_key = r.headers.get("tr_cont_key", "")
        return {
            "status": r.status_code,
            "resp_tr_cont": resp_tr_cont,
            "resp_tr_cont_key": resp_tr_cont_key,
            "body": r.json() if r.status_code == 200 else r.text,
        }
    except Exception as e:
        return {"status": -1, "error": str(e)}


def short(d, mx=200):
    s = json.dumps(d, ensure_ascii=False, default=str)
    return s if len(s) <= mx else s[:mx] + "..."


def main():
    cli = LsApiClient()
    URL_FOMD = f"{BASE_URL}/futureoption/market-data"

    # 활성 종목 후보
    test_codes = [
        ("A0A65000", "TKG휴켐스 F 202605"),
        ("A0166000", "KOSPI200 F 2606"),
    ]

    # ── 1) cnt=900 — 한 번에 받는 양 ──────────────────
    print("=" * 90)
    print("### 1) t8406 cgubun='M' cnt=900 — 한 번에 받는 봉 양")
    print("=" * 90)
    for sh, hname in test_codes:
        body = {"t8406InBlock": {"focode": sh, "cgubun": "M", "bgubun": 0, "cnt": 900}}
        r = call(cli, "t8406", body, URL_FOMD)
        body_d = r.get("body", {})
        if isinstance(body_d, dict):
            rows = body_d.get("t8406OutBlock1", [])
            rsp = body_d.get("rsp_msg", "")
            print(f"  {sh} ({hname}) status={r['status']} rows={len(rows) if isinstance(rows,list) else 0} rsp={rsp}")
            print(f"    resp tr_cont={r.get('resp_tr_cont','')} tr_cont_key={r.get('resp_tr_cont_key','')}")
            if rows:
                print(f"    first(latest) chetime={rows[0].get('chetime')} price={rows[0].get('price')}")
                print(f"    last(oldest)  chetime={rows[-1].get('chetime')} price={rows[-1].get('price')}")
                # chetime 간격 확인 (앞 5개)
                times = [r.get("chetime") for r in rows[:5]]
                print(f"    first 5 chetimes: {times}")
                # 만약 다 같은 날짜라면 현재일만, 다르다면 lookback 됨
                # chetime이 HHMMSS 만이면 날짜 정보 없음 → ohlc값 비교
                ohlc_first = (rows[0].get("open"), rows[0].get("high"), rows[0].get("low"), rows[0].get("close"))
                print(f"    first OHLC: {ohlc_first}")

    # ── 2) bgubun 0/1/2 ──────────────────────────────
    print()
    print("=" * 90)
    print("### 2) t8406 cgubun='M' bgubun 비교 (KOSPI200 F)")
    print("=" * 90)
    sh = "A0166000"
    for bg in [0, 1, 2, 30, 60]:
        body = {"t8406InBlock": {"focode": sh, "cgubun": "M", "bgubun": bg, "cnt": 5}}
        r = call(cli, "t8406", body, URL_FOMD)
        body_d = r.get("body", {})
        if isinstance(body_d, dict):
            rows = body_d.get("t8406OutBlock1", [])
            rsp = body_d.get("rsp_msg", "")
            print(f"  bgubun={bg:3d} status={r['status']} rows={len(rows) if isinstance(rows,list) else 0} rsp={rsp}")
            if rows:
                times = [r.get("chetime") for r in rows]
                print(f"    chetimes: {times}")

    # ── 3) cgubun 다양 (1, 2, 3, M, T) ────────────────
    print()
    print("=" * 90)
    print("### 3) cgubun 다양 (KOSPI200 F)")
    print("=" * 90)
    for cg in ["T", "M", "1", "2", "3", "5", "10", "30", "60"]:
        body = {"t8406InBlock": {"focode": sh, "cgubun": cg, "bgubun": 0, "cnt": 5}}
        r = call(cli, "t8406", body, URL_FOMD)
        body_d = r.get("body", {})
        if isinstance(body_d, dict):
            rows = body_d.get("t8406OutBlock1", [])
            rsp = body_d.get("rsp_msg", "")
            times = [r.get("chetime") for r in rows][:3] if rows else []
            print(f"  cgubun={cg!r:5s} rows={len(rows) if isinstance(rows,list) else 0} rsp={rsp} times={times}")

    # ── 4) tr_cont='Y' 페이징 시도 ─────────────────────
    print()
    print("=" * 90)
    print("### 4) tr_cont='Y' 연속조회 시도 (KOSPI200 F)")
    print("=" * 90)
    body = {"t8406InBlock": {"focode": sh, "cgubun": "M", "bgubun": 0, "cnt": 900}}
    r1 = call(cli, "t8406", body, URL_FOMD, tr_cont="N")
    body_d = r1.get("body", {})
    rows1 = body_d.get("t8406OutBlock1", []) if isinstance(body_d, dict) else []
    last_time1 = rows1[-1].get("chetime") if rows1 else ""
    print(f"  1차: rows={len(rows1)} last_chetime={last_time1}")
    print(f"  1차 응답 tr_cont 헤더={r1.get('resp_tr_cont','')} key={r1.get('resp_tr_cont_key','')}")
    # 2차: tr_cont='Y' + key
    r2 = call(cli, "t8406", body, URL_FOMD, tr_cont="Y", tr_cont_key=r1.get('resp_tr_cont_key', last_time1))
    body_d = r2.get("body", {})
    rows2 = body_d.get("t8406OutBlock1", []) if isinstance(body_d, dict) else []
    last_time2 = rows2[-1].get("chetime") if rows2 else ""
    first_time2 = rows2[0].get("chetime") if rows2 else ""
    print(f"  2차(tr_cont=Y): rows={len(rows2)} first_chetime={first_time2} last_chetime={last_time2}")
    if last_time2 and last_time1 and last_time2 != last_time1:
        print(f"  → 페이징 가능! 시간이 더 과거로 이동")
    else:
        print(f"  → 페이징 같은 데이터 반복 또는 안 됨")


if __name__ == "__main__":
    main()
