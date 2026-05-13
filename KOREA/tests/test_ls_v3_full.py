"""
LS API v3: 주식선물 마스터 발견 + 30초봉 lookback 한계 (2022-01-02)

Section A — 30초봉 lookback (2022-01-02 가능 여부)
  1. t8418 KOSPI200 30초봉 — 2022-01-02 / 2024-01-02 / 2025-01-02
  2. t8415 KOSPI200 연결선물 30초봉 — 2022-01-02 / 2024-01-02 / 2025-01-02

Section B — 주식선물 마스터 발견
  3. t8401 / t8403 / t8404 / t8431 / t8433 / t8434 / t8435 / t8438 / t8442 시도
  4. endpoint path 변경: /futureoption/market-data, /futureoption/master 등
  5. 주식선물 가능성 있는 코드: KOSPI200 옵션과 같은 prefix 또는 1XXXXX 형식
"""

from __future__ import annotations

import json
import sys
from datetime import date
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


def short(d, mx=350):
    s = json.dumps(d, ensure_ascii=False, default=str)
    return s if len(s) <= mx else s[:mx] + "..."


def section(title):
    print()
    print("=" * 92)
    print(f"### {title}")
    print("=" * 92)


def report(label, result, blocks=None):
    print(f"  ─ {label}")
    print(f"     status={result['status']}  url={result.get('url')}")
    body = result.get("body") or result.get("error", "")
    if isinstance(body, dict):
        rsp_msg = body.get("rsp_msg", "")
        if rsp_msg:
            print(f"     rsp_msg={rsp_msg}")
        block_names = blocks or []
        if not block_names:
            block_names = [k for k in body.keys() if k.endswith("OutBlock") or k.endswith("OutBlock1")]
        for bn in block_names:
            v = body.get(bn)
            if isinstance(v, list):
                print(f"     {bn} count={len(v)}")
                if v:
                    print(f"     {bn}[0] = {short(v[0])}")
                    if len(v) > 1:
                        print(f"     {bn}[-1]= {short(v[-1])}")
            elif isinstance(v, dict):
                print(f"     {bn} = {short(v)}")
    else:
        print(f"     body = {short(body, 400)}")
    print()


def main():
    cli = LsApiClient()

    URL_INDEX  = f"{BASE_URL}/indtp/chart"
    URL_FUTOPT = f"{BASE_URL}/futureoption/chart"
    URL_FOMD   = f"{BASE_URL}/futureoption/market-data"  # t8432 동작
    URL_FOMM   = f"{BASE_URL}/futureoption/master"        # 후보
    URL_STOCK  = f"{BASE_URL}/stock/market-data"

    # ──────────────────────────────────────────────
    # SECTION A: 2022-01-02 lookback
    # ──────────────────────────────────────────────
    section("A) t8418 30초봉 lookback (KOSPI200 = '101')")
    for ymd, lbl in [("20220102","2022-01-02"), ("20240102","2024-01-02"),
                     ("20250102","2025-01-02"), ("20260102","2026-01-02")]:
        body = {"t8418InBlock": {
            "shcode": "101", "ncnt": 0, "qrycnt": 5, "nday": "0",
            "sdate": "", "stime": "", "edate": ymd, "etime": "",
            "cts_date": "", "cts_time": "", "comp_yn": "N"
        }}
        report(f"edate={lbl} ncnt=0 (30초봉)", call(cli, "t8418", body, URL_INDEX))

    section("A') t8418 1분봉 lookback (KOSPI200) — 비교용")
    for ymd, lbl in [("20220102","2022-01-02"), ("20240102","2024-01-02")]:
        body = {"t8418InBlock": {
            "shcode": "101", "ncnt": 1, "qrycnt": 5, "nday": "0",
            "sdate": "", "stime": "", "edate": ymd, "etime": "",
            "cts_date": "", "cts_time": "", "comp_yn": "N"
        }}
        report(f"edate={lbl} ncnt=1 (1분봉)", call(cli, "t8418", body, URL_INDEX))

    section("A) t8415 30초봉 lookback (KOSPI200 연결선물)")
    for ymd, lbl in [("20220102","2022-01-02"), ("20240102","2024-01-02"),
                     ("20250102","2025-01-02"), ("20260102","2026-01-02")]:
        body = {"t8415InBlock": {
            "shcode": "90199999", "ncnt": 0, "qrycnt": 5, "nday": "0",
            "sdate": "", "stime": "", "edate": ymd, "etime": "",
            "cts_date": "", "cts_time": "", "comp_yn": "N"
        }}
        report(f"edate={lbl} ncnt=0", call(cli, "t8415", body, URL_FUTOPT))

    # ──────────────────────────────────────────────
    # SECTION B: 주식선물 마스터 TR 발견
    # ──────────────────────────────────────────────
    section("B-1) 마스터 TR 후보 — futureoption/market-data 경로")
    for tr in ["t8401", "t8403", "t8404", "t8431", "t8433", "t8434", "t8435", "t8438", "t8442", "t8451"]:
        body = {f"{tr}InBlock": {"gubun": "1"}}
        report(f"{tr} gubun=1", call(cli, tr, body, URL_FOMD))

    section("B-2) 마스터 TR 후보 — futureoption/master 경로")
    for tr in ["t8401", "t8431", "t8434", "t8435"]:
        body = {f"{tr}InBlock": {"gubun": "1"}}
        report(f"{tr} gubun=1", call(cli, tr, body, URL_FOMM))

    section("B-3) t8432 다양한 gubun (전체 선물 종류 발견)")
    for gubun in ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]:
        body = {"t8432InBlock": {"gubun": gubun}}
        result = call(cli, "t8432", body, URL_FOMD)
        rows = result.get("body", {}).get("t8432OutBlock", []) if isinstance(result.get("body"), dict) else []
        print(f"  ─ gubun={gubun}  status={result['status']}  rows={len(rows) if isinstance(rows,list) else 'n/a'}")
        if isinstance(rows, list) and rows:
            print(f"     sample: {short(rows[0])}")
            print(f"     last  : {short(rows[-1])}")
        cli._throttle()
    print()

    section("B-4) t8415 — 주식선물 추정 코드 broader 시도")
    # 주식선물은 KRX 표준상 6자리(1로 시작) 또는 LS 자체 8자리 코드일 수 있음
    # 삼성전자(005930) 주식선물 추정
    for code, desc in [
        ("1AC305", "KRX 6자 추정"),
        ("105930", "1+5자리"),
        ("1059300", "1+종목+0"),
        ("F005930", "F+종목"),
        ("00593F", "단축+F"),
        ("KS0593", "KS prefix"),
        ("1AC301", "KRX 6자 변형"),
        ("0593X3", "X3 만기"),
        ("0593W3", "W3 만기"),
        ("059300", "주식 + 00"),
        ("159930", "1+종목 변형"),
        ("0593", "단축만"),
    ]:
        body = {"t8415InBlock": {
            "shcode": code, "ncnt": 1, "qrycnt": 3, "nday": "0",
            "sdate": "", "stime": "", "edate": "20260513", "etime": "",
            "cts_date": "", "cts_time": "", "comp_yn": "N"
        }}
        result = call(cli, "t8415", body, URL_FUTOPT)
        body_d = result.get("body", {})
        if isinstance(body_d, dict):
            rsp = body_d.get("rsp_msg", "")
            rows = body_d.get("t8415OutBlock1", [])
            print(f"  ─ shcode={code:8s} ({desc:15s}) status={result['status']} rsp_msg={rsp} rows={len(rows) if isinstance(rows,list) else 0}")
        else:
            print(f"  ─ shcode={code:8s} ({desc:15s}) status={result['status']} body={short(body_d, 200)}")


if __name__ == "__main__":
    main()
