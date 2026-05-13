"""
LS API 지수/선물 분봉 TR 종합 검증 (v2)

검증 항목:
  1. t8418 30초봉/1분봉 lookback (오늘/1주/2주/1달/2달)
  2. t8418 섹터지수 코드 (예: 013 전기전자)
  3. t8415 30초봉/1분봉 lookback
  4. t8415 다양한 선물 코드 (코스피200/코스닥150/미니)
  5. t8430 응답 형식 (주식선물 마스터 후보)
  6. t8432 응답 형식 (지수선물 마스터)
  7. 주식선물 분차트 시도 — t8415에 KRX 주식선물 코드 직접 호출
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from collectors.ls_api import LsApiClient, BASE_URL
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def call_tr(client: LsApiClient, tr_cd: str, body: dict, url: str) -> dict:
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
        return {
            "status": r.status_code,
            "url": url,
            "body": r.json() if r.status_code == 200 else r.text,
        }
    except Exception as e:
        return {"status": -1, "url": url, "error": str(e)}


def short(d, max_chars=400):
    s = json.dumps(d, ensure_ascii=False, default=str)
    return s if len(s) <= max_chars else s[:max_chars] + "..."


def section(title):
    print()
    print("=" * 90)
    print(f"### {title}")
    print("=" * 90)


def report(label, result, want_first_row=True):
    print(f"  ─ {label}")
    print(f"     status={result['status']}  url={result.get('url')}")
    body = result.get("body") or result.get("error", "")
    if isinstance(body, dict):
        meta = body.get("t8418OutBlock") or body.get("t8415OutBlock") or body.get("t8432OutBlock") or body.get("t8430OutBlock") or {}
        rows = body.get("t8418OutBlock1") or body.get("t8415OutBlock1") or body.get("t8432OutBlock") or body.get("t8430OutBlock") or []
        rsp_msg = body.get("rsp_msg", "")
        if rsp_msg and rsp_msg != "정상":
            print(f"     rsp_msg={rsp_msg}")
        if isinstance(meta, dict) and meta:
            print(f"     meta = {short(meta, 300)}")
        if isinstance(rows, list):
            print(f"     rows count = {len(rows)}")
            if rows and want_first_row:
                print(f"     first      = {short(rows[0], 300)}")
                if len(rows) > 1:
                    print(f"     last       = {short(rows[-1], 300)}")
    else:
        print(f"     body = {short(body, 400)}")
    print()


def main():
    cli = LsApiClient()

    today = date.today()
    one_week  = today - timedelta(days=7)
    two_week  = today - timedelta(days=14)
    one_month = today - timedelta(days=30)
    two_month = today - timedelta(days=60)

    URL_INDEX  = f"{BASE_URL}/indtp/chart"
    URL_FUTOPT = f"{BASE_URL}/futureoption/chart"
    URL_ETC    = f"{BASE_URL}/etc/master"      # t8430 추정
    URL_FUT_MASTER = f"{BASE_URL}/futureoption/market-data"  # t8432 추정

    # ── 1. t8418 30초봉/1분봉 lookback (KOSPI200 = 101) ───────
    section("1) t8418 — 30초봉/1분봉 lookback (KOSPI200 = '101')")
    for d, lbl in [(today, "오늘"), (one_week, "1주전"), (two_week, "2주전"),
                   (one_month, "1달전"), (two_month, "2달전")]:
        for ncnt in [0, 1]:
            body = {"t8418InBlock": {
                "shcode": "101", "ncnt": ncnt, "qrycnt": 50, "nday": "0",
                "sdate": "", "stime": "", "edate": d.strftime("%Y%m%d"), "etime": "",
                "cts_date": "", "cts_time": "", "comp_yn": "N"
            }}
            label = f"{lbl} ({d}) ncnt={ncnt}"
            report(label, call_tr(cli, "t8418", body, URL_INDEX))

    # ── 2. t8418 섹터지수 (예: 013 전기전자) ─────────────────────
    section("2) t8418 — 섹터지수 코드 시도")
    for code in ["013", "002", "301", "201"]:
        body = {"t8418InBlock": {
            "shcode": code, "ncnt": 1, "qrycnt": 5, "nday": "0",
            "sdate": "", "stime": "", "edate": today.strftime("%Y%m%d"), "etime": "",
            "cts_date": "", "cts_time": "", "comp_yn": "N"
        }}
        report(f"섹터/지수 shcode={code}", call_tr(cli, "t8418", body, URL_INDEX))

    # ── 3. t8415 30초봉 lookback (KOSPI200 연결선물) ──────────
    section("3) t8415 — 30초봉 lookback (90199999 = KOSPI200 연결)")
    for d, lbl in [(today, "오늘"), (one_week, "1주전"), (two_week, "2주전"),
                   (one_month, "1달전"), (two_month, "2달전")]:
        body = {"t8415InBlock": {
            "shcode": "90199999", "ncnt": 0, "qrycnt": 50, "nday": "0",
            "sdate": "", "stime": "", "edate": d.strftime("%Y%m%d"), "etime": "",
            "cts_date": "", "cts_time": "", "comp_yn": "N"
        }}
        report(f"{lbl} ({d}) ncnt=0", call_tr(cli, "t8415", body, URL_FUTOPT))

    # ── 4. t8415 다양한 선물 코드 ───────────────────────────────
    section("4) t8415 — 다양한 선물 코드 테스트 (오늘 1분봉)")
    futures_codes = [
        ("90199999", "KOSPI200 연결선물 (자동 99999999 매핑)"),
        ("90205999", "코스닥150 연결선물 추정"),
        ("90205000", "코스닥150 연결 변형"),
        ("105W3000", "KOSPI200 미니 추정"),
        ("101W3000", "KOSPI200 근월 추정"),
        ("101W4000", "KOSPI200 원월 추정"),
        ("106W3000", "코스닥150 근월 추정"),
    ]
    for code, desc in futures_codes:
        body = {"t8415InBlock": {
            "shcode": code, "ncnt": 1, "qrycnt": 5, "nday": "0",
            "sdate": "", "stime": "", "edate": today.strftime("%Y%m%d"), "etime": "",
            "cts_date": "", "cts_time": "", "comp_yn": "N"
        }}
        report(f"shcode={code} ({desc})", call_tr(cli, "t8415", body, URL_FUTOPT))

    # ── 5. t8432 지수선물 마스터 (코드 발견용) ───────────────────
    section("5) t8432 — 지수선물 마스터 조회")
    body = {"t8432InBlock": {"gubun": "1"}}  # 1=지수선물?
    report("gubun=1", call_tr(cli, "t8432", body, URL_FUT_MASTER))

    # ── 6. t8430 — etc master TR (주식선물 후보) ─────────────────
    section("6) t8430 — etc master TR 응답 형식 확인")
    body = {"t8430InBlock": {"gubun": "1"}}
    report("gubun=1", call_tr(cli, "t8430", body, URL_ETC))

    # ── 7. 주식선물 시도 — KRX 표준 6자리 코드 추정 ──────────────
    section("7) 주식선물 분차트 시도 — t8415에 6자리 코드")
    # 삼성전자 주식선물 추정 (0L9000? 1AC305? 005930000?)
    stock_futures_guess = [
        ("005930F", "삼성전자 + F"),
        ("105930", "10 + 5930"),
        ("1AC305", "KRX 형식 추정"),
        ("KRDFL5W3", "Bloomberg 형식 추정"),
    ]
    for code, desc in stock_futures_guess:
        body = {"t8415InBlock": {
            "shcode": code, "ncnt": 1, "qrycnt": 5, "nday": "0",
            "sdate": "", "stime": "", "edate": today.strftime("%Y%m%d"), "etime": "",
            "cts_date": "", "cts_time": "", "comp_yn": "N"
        }}
        report(f"shcode={code} ({desc})", call_tr(cli, "t8415", body, URL_FUTOPT))


if __name__ == "__main__":
    main()
