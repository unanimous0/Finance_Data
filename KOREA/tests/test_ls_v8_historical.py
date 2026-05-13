"""
LS API v8: 주식선물 historical 분/30초봉 마지막 시도

후보:
  1. t8427 (과거데이터시간대별조회) — dt_gbn (D/M) + min_term (분 단위)
  2. t8404 (주식선물 시간대별 체결조회) — date/time 인자로 historical?
  3. t8406 강제 sdate/edate 주입 — doc에 없지만 LS 다른 TR과 일관 시도
  4. t8465에 expcode (KR4...) 직접 주입 — shcode 형식 의심
  5. t8453/t8452 (통합 주식차트)에 주식선물 코드 시도
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
        return {"status": r.status_code,
                "body": r.json() if r.status_code == 200 else r.text}
    except Exception as e:
        return {"status": -1, "error": str(e)}


def short(d, mx=200):
    s = json.dumps(d, ensure_ascii=False, default=str)
    return s if len(s) <= mx else s[:mx] + "..."


def report(label, r):
    body_d = r.get("body", {})
    print(f"  {label}")
    if isinstance(body_d, dict):
        rsp = body_d.get("rsp_msg", "")
        # 모든 OutBlock 표시
        for k in body_d:
            v = body_d[k]
            if k.startswith("rsp"):
                continue
            if isinstance(v, list):
                print(f"    {k}: {len(v)}건  rsp={rsp}")
                if v:
                    print(f"      first = {short(v[0], 250)}")
                    if len(v) > 1:
                        print(f"      last  = {short(v[-1], 250)}")
            elif isinstance(v, dict):
                if v:
                    print(f"    {k}: {short(v, 250)}")
    else:
        print(f"    body = {short(body_d, 250)}")


def main():
    cli = LsApiClient()
    URL_FOMD = f"{BASE_URL}/futureoption/market-data"
    URL_FOC  = f"{BASE_URL}/futureoption/chart"
    URL_SC   = f"{BASE_URL}/stock/chart"

    SH_KOSPI = "A0166000"      # KOSPI200 F 2606
    SH_TKG   = "A0A65000"      # TKG휴켐스 F 202605
    EXPCODE_KOSPI = "KR4A01660005"

    # ── 1) t8427 다양한 dt_gbn / min_term ──────────
    print("=" * 90)
    print("### 1) t8427 — KOSPI200 F (A0166000), 만기 2026-06")
    print("=" * 90)
    base = {
        "fo_gbn": "F", "yyyy": "2026", "mm": "06", "cp_gbn": "2",
        "actprice": 0.00, "focode": SH_KOSPI,
    }
    cases = [
        ("D 일봉", {"dt_gbn": "D", "min_term": "", "date": "", "time": ""}),
        ("D 일봉 + date=20260101", {"dt_gbn": "D", "min_term": "", "date": "20260101", "time": ""}),
        ("M 분봉 min=1", {"dt_gbn": "M", "min_term": "1", "date": "20260101", "time": ""}),
        ("M 분봉 min=30 (30분)", {"dt_gbn": "M", "min_term": "30", "date": "20260101", "time": ""}),
        ("M 분봉 min=0.5", {"dt_gbn": "M", "min_term": "0.5", "date": "20260101", "time": ""}),
        ("M 분봉 min=00.5 (30초)", {"dt_gbn": "M", "min_term": "00.5", "date": "20260101", "time": ""}),
        ("M 분봉 min=0", {"dt_gbn": "M", "min_term": "0", "date": "20260101", "time": ""}),
        ("T 틱", {"dt_gbn": "T", "min_term": "", "date": "20260101", "time": ""}),
        ("Y 년", {"dt_gbn": "Y", "min_term": "", "date": "", "time": ""}),
        ("W 주", {"dt_gbn": "W", "min_term": "", "date": "", "time": ""}),
    ]
    for label, extra in cases:
        body = {"t8427InBlock": {**base, **extra}}
        r = call(cli, "t8427", body, URL_FOMD)
        report(f"{label}", r)

    # ── 2) t8427 TKG휴켐스 (주식선물) ──────────────
    print()
    print("=" * 90)
    print("### 2) t8427 — TKG휴켐스 F (A0A65000), 만기 2026-05")
    print("=" * 90)
    base_t = {
        "fo_gbn": "F", "yyyy": "2026", "mm": "05", "cp_gbn": "2",
        "actprice": 0.00, "focode": SH_TKG,
    }
    cases_t = [
        ("D 일봉 (전체)", {"dt_gbn": "D", "min_term": "", "date": "", "time": ""}),
        ("M 분봉 min=1", {"dt_gbn": "M", "min_term": "1", "date": "20260102", "time": ""}),
        ("M 분봉 min=30", {"dt_gbn": "M", "min_term": "30", "date": "20260102", "time": ""}),
    ]
    for label, extra in cases_t:
        body = {"t8427InBlock": {**base_t, **extra}}
        r = call(cli, "t8427", body, URL_FOMD)
        report(f"{label}", r)

    # ── 3) t8404 (주식선물 시간대별 체결조회) ─────
    print()
    print("=" * 90)
    print("### 3) t8404 (주식선물 시간대별 체결조회) — historical 가능?")
    print("=" * 90)
    # t8404 doc 안 봤음, 일단 다양 시도
    cases404 = [
        {"focode": SH_TKG, "cgubun": "S", "bun_term": "0", "cnt": 5},
        {"focode": SH_TKG, "cgubun": "S", "bun_term": "0", "cnt": 5, "date": "20260102"},
        {"focode": SH_TKG, "cnt": 5, "date": "20260102"},
        {"focode": SH_TKG, "cnt": 5},
    ]
    for body_in in cases404:
        body = {"t8404InBlock": body_in}
        r = call(cli, "t8404", body, URL_FOMD)
        report(f"input={short(body_in,150)}", r)

    # ── 4) t8406 강제 sdate/edate ─────────────────
    print()
    print("=" * 90)
    print("### 4) t8406 + sdate/edate 강제 주입")
    print("=" * 90)
    cases_force = [
        {"focode": SH_TKG, "cgubun": "M", "bgubun": 0, "cnt": 5,
         "sdate": "20260102", "edate": "20260102"},
        {"focode": SH_TKG, "cgubun": "M", "bgubun": 0, "cnt": 5,
         "date": "20260102"},
        {"focode": SH_TKG, "cgubun": "M", "bgubun": 0, "cnt": 5,
         "edate": "20260102"},
    ]
    for body_in in cases_force:
        body = {"t8406InBlock": body_in}
        r = call(cli, "t8406", body, URL_FOMD)
        report(f"input={short(body_in,150)}", r)

    # ── 5) t8465 with expcode field ──────────────
    print()
    print("=" * 90)
    print("### 5) t8465 expcode 형식 시도")
    print("=" * 90)
    for sh in [SH_TKG, "A0A65000", EXPCODE_KOSPI, "KR4A0A650006"]:
        body = {"t8465InBlock": {
            "shcode": sh, "ncnt": 1, "qrycnt": 5, "nday": "1",
            "sdate": "20260102", "edate": "20260102",
            "cts_date": "", "cts_time": "", "comp_yn": "N"
        }}
        r = call(cli, "t8465", body, URL_FOC)
        report(f"shcode={sh}", r)

    # ── 6) t8452/t8453 (통합 주식차트) — 주식선물에도 가능? ─
    print()
    print("=" * 90)
    print("### 6) t8452 (통합 주식 N분차트) — 주식선물 코드 직접 시도")
    print("=" * 90)
    body = {"t8452InBlock": {
        "shcode": SH_TKG, "ncnt": 1, "qrycnt": 5, "nday": "0",
        "sdate": "20260102", "stime": "",
        "edate": "20260102", "etime": "",
        "cts_date": "", "cts_time": "", "comp_yn": "N",
        "exchgubun": "K"
    }}
    r = call(cli, "t8452", body, URL_SC)
    report(f"t8452 with stock futures shcode={SH_TKG}", r)


if __name__ == "__main__":
    main()
