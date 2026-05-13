"""
LS API 지수/선물 분봉 TR 검증 스크립트

테스트 대상 TR:
  t8418  : 업종 N분차트 (지수)              — 추정
  t8415  : 선물옵션 N분차트                  — 샘플 86 검증됨
  t8419  : 업종 일주월 (분봉 아님, 비교용)

검증 항목:
  1) 30초봉 가능 여부 (ncnt=0)
  2) 1분봉 가능 여부 (ncnt=1)
  3) lookback (오늘 / 어제 / 1주 전 / 1달 전)
  4) 종목 코드 형식 (지수 코드, 선물 코드)
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from collectors.ls_api import LsApiClient, BASE_URL, LsApiError
import requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def call_tr(client: LsApiClient, tr_cd: str, body: dict) -> dict:
    """단일 TR 호출 — 결과 그대로 반환 (디버그용)"""
    url_map = {
        "t8418": f"{BASE_URL}/indtp/chart",        # 추정
        "t8415": f"{BASE_URL}/futureoption/chart", # 추정
        "t8419": f"{BASE_URL}/indtp/chart",
    }
    url = url_map.get(tr_cd, f"{BASE_URL}/stock/chart")
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


def short(d: dict, max_chars: int = 600) -> str:
    s = json.dumps(d, ensure_ascii=False, default=str)
    return s if len(s) <= max_chars else s[:max_chars] + "..."


def main():
    cli = LsApiClient()

    # ── 테스트 케이스 ─────────────────────────────────
    today = date.today()
    yesterday = today - timedelta(days=1)
    one_week_ago = today - timedelta(days=7)
    one_month_ago = today - timedelta(days=30)

    targets = [
        # 1. 업종 N분차트 (t8418) — KOSPI200 지수 = 101
        ("t8418", "지수 30초봉 KOSPI200 (오늘)", {
            "t8418InBlock": {
                "shcode": "101", "ncnt": 0, "qrycnt": 100, "nday": "0",
                "sdate": "", "stime": "",
                "edate": today.strftime("%Y%m%d"), "etime": "",
                "cts_date": "", "cts_time": "", "comp_yn": "N"
            }
        }),
        ("t8418", "지수 1분봉 KOSPI200 (오늘)", {
            "t8418InBlock": {
                "shcode": "101", "ncnt": 1, "qrycnt": 100, "nday": "0",
                "sdate": "", "stime": "",
                "edate": today.strftime("%Y%m%d"), "etime": "",
                "cts_date": "", "cts_time": "", "comp_yn": "N"
            }
        }),
        ("t8418", "지수 1분봉 KOSPI200 (1주전)", {
            "t8418InBlock": {
                "shcode": "101", "ncnt": 1, "qrycnt": 100, "nday": "0",
                "sdate": "", "stime": "",
                "edate": one_week_ago.strftime("%Y%m%d"), "etime": "",
                "cts_date": "", "cts_time": "", "comp_yn": "N"
            }
        }),
        ("t8418", "지수 1분봉 KOSPI (001) (오늘)", {
            "t8418InBlock": {
                "shcode": "001", "ncnt": 1, "qrycnt": 100, "nday": "0",
                "sdate": "", "stime": "",
                "edate": today.strftime("%Y%m%d"), "etime": "",
                "cts_date": "", "cts_time": "", "comp_yn": "N"
            }
        }),

        # 2. 선물옵션 N분차트 (t8415) — KOSPI200 연결선물 90199999
        ("t8415", "지수선물 30초봉 KOSPI200 연결 (오늘)", {
            "t8415InBlock": {
                "shcode": "90199999", "ncnt": 0, "qrycnt": 100, "nday": "0",
                "sdate": "", "stime": "",
                "edate": today.strftime("%Y%m%d"), "etime": "",
                "cts_date": "", "cts_time": "", "comp_yn": "N"
            }
        }),
        ("t8415", "지수선물 1분봉 KOSPI200 연결 (오늘)", {
            "t8415InBlock": {
                "shcode": "90199999", "ncnt": 1, "qrycnt": 100, "nday": "0",
                "sdate": "", "stime": "",
                "edate": today.strftime("%Y%m%d"), "etime": "",
                "cts_date": "", "cts_time": "", "comp_yn": "N"
            }
        }),
        ("t8415", "지수선물 1분봉 KOSPI200 연결 (1달전)", {
            "t8415InBlock": {
                "shcode": "90199999", "ncnt": 1, "qrycnt": 100, "nday": "0",
                "sdate": "", "stime": "",
                "edate": one_month_ago.strftime("%Y%m%d"), "etime": "",
                "cts_date": "", "cts_time": "", "comp_yn": "N"
            }
        }),
    ]

    for tr_cd, desc, body in targets:
        print("="*80)
        print(f"[TEST] {tr_cd} — {desc}")
        result = call_tr(cli, tr_cd, body)
        print(f"  status: {result['status']}")
        print(f"  url   : {result['url']}")
        print(f"  body  : {short(result.get('body', result.get('error', '')), 800)}")
        print()


if __name__ == "__main__":
    main()
