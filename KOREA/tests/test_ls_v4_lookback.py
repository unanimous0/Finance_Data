"""
LS API v4: lookback 정밀 + 주식선물 차트 실호출

A1) t8418 30초봉 첫날 정밀 (2026-01-02 OK 확정 → 그 이전 며칠씩 시도)
A2) t8418 1분봉 첫날 정밀 (2024-01-02 ❌ → 2025 어디?)
A2') t8415 30초/1분봉 첫날 정밀

B') t8401에서 받은 LS shcode로 t8415 호출 — 주식선물 분차트 실증
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


def test_one(client, tr_cd, shcode, ymd, ncnt, url):
    inblock = f"{tr_cd}InBlock"
    body = {inblock: {
        "shcode": shcode, "ncnt": ncnt, "qrycnt": 5, "nday": "0",
        "sdate": "", "stime": "", "edate": ymd, "etime": "",
        "cts_date": "", "cts_time": "", "comp_yn": "N"
    }}
    r = call(client, tr_cd, body, url)
    body_d = r.get("body") or r.get("error", "")
    if isinstance(body_d, dict):
        rsp = body_d.get("rsp_msg", "")
        rows = body_d.get(f"{tr_cd}OutBlock1") or []
        # 30초봉이라도 OutBlock1 빈 경우 = lookback 한도
        return {"status": r["status"], "rsp_msg": rsp, "rows": len(rows) if isinstance(rows, list) else 0,
                "first": rows[0] if rows else None}
    return {"status": r["status"], "raw": str(body_d)[:200]}


def main():
    cli = LsApiClient()

    URL_INDEX  = f"{BASE_URL}/indtp/chart"
    URL_FUTOPT = f"{BASE_URL}/futureoption/chart"

    # ── A1: 30초봉 첫날 정밀 (2026-01-02 OK → 그 이전 며칠?) ────────
    print("=" * 92)
    print("### A1) t8418 30초봉 정밀 lookback (KOSPI200 = 101)")
    print("=" * 92)
    candidates = ["20251215", "20251202", "20251104", "20251007",
                  "20260101", "20260102", "20260103",
                  "20260601", "20260701"]  # 미래도 포함
    for ymd in candidates:
        r = test_one(cli, "t8418", "101", ymd, 0, URL_INDEX)
        print(f"  edate={ymd}  status={r['status']}  rows={r.get('rows','?')}  rsp={r.get('rsp_msg','?')}")

    print()
    print("=" * 92)
    print("### A1') t8415 30초봉 정밀 lookback (KOSPI200 연결선물 90199999)")
    print("=" * 92)
    for ymd in candidates:
        r = test_one(cli, "t8415", "90199999", ymd, 0, URL_FUTOPT)
        print(f"  edate={ymd}  status={r['status']}  rows={r.get('rows','?')}  rsp={r.get('rsp_msg','?')}")

    print()
    print("=" * 92)
    print("### A2) t8418 1분봉 정밀 lookback")
    print("=" * 92)
    for ymd in ["20240102", "20240701", "20250102", "20250401",
                "20250701", "20251001", "20251215", "20260102"]:
        r = test_one(cli, "t8418", "101", ymd, 1, URL_INDEX)
        print(f"  edate={ymd}  status={r['status']}  rows={r.get('rows','?')}  rsp={r.get('rsp_msg','?')}")

    print()
    print("=" * 92)
    print("### A2') t8415 1분봉 정밀 lookback (선물)")
    print("=" * 92)
    for ymd in ["20240102", "20240701", "20250102", "20250701", "20251001", "20260102"]:
        r = test_one(cli, "t8415", "90199999", ymd, 1, URL_FUTOPT)
        print(f"  edate={ymd}  status={r['status']}  rows={r.get('rows','?')}  rsp={r.get('rsp_msg','?')}")

    # ── B': 주식선물 차트 실호출 (t8401 발견 코드로) ──────────────
    print()
    print("=" * 92)
    print("### B') t8401에서 받은 LS shcode로 t8415 호출 — 주식선물 차트 검증")
    print("=" * 92)

    # 먼저 t8401로 일부 master 받기 (전체 3080건, 일단 sample)
    body = {"t8401InBlock": {"gubun": "1"}}
    master_r = call(cli, "t8401", body, f"{BASE_URL}/futureoption/market-data")
    master = (master_r.get("body") or {}).get("t8401OutBlock") or []
    print(f"  t8401 master rows: {len(master)}")
    # 종류별 sample 추출
    samples = []
    seen_kinds = set()
    for row in master:
        sh = row.get("shcode", "")
        if not sh:
            continue
        # prefix 1글자(타입) + 2글자(종목/지수 코드 prefix) 패턴 분류
        prefix = sh[:1]
        if prefix not in seen_kinds:
            samples.append(row)
            seen_kinds.add(prefix)
        if len(samples) >= 5:
            break
    # 추가로 KOSPI200 선물 (A0166000), TKG휴켐스 (A0A65000) 명시 포함
    explicit = []
    for row in master:
        if row.get("shcode") in ("A0166000", "A0A65000"):
            explicit.append(row)
    samples = explicit + samples[:5]

    print(f"  검증할 sample {len(samples)}건:")
    for s in samples:
        print(f"     {short(s, 200)}")

    print()
    for s in samples:
        sh = s.get("shcode", "")
        hname = s.get("hname", "")
        r = test_one(cli, "t8415", sh, "20260513", 1, URL_FUTOPT)
        print(f"  shcode={sh:10s} ({hname:30s}) status={r['status']} rows={r.get('rows','?')} "
              f"rsp={r.get('rsp_msg','?')}")
        if r.get("first"):
            print(f"      first bar: {short(r['first'], 200)}")


if __name__ == "__main__":
    main()
