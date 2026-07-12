"""
FnGuide XML 수집기
URL: https://comp.fnguide.com/SVO2/xml/Snapshot_all/{stock_code}.xml
제공: 분기 재무지표 (EPS/BPS/매출/영업이익 등, 연결+별도)
한계: 최근 5~8분기만 제공, 과거 백필 불가
"""
import re
import logging
from datetime import date, datetime
from typing import Optional
import requests

log = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://comp.fnguide.com/",
})

_EOKWON = 100_000_000  # 억원 → 원


def _parse_num(s: str) -> Optional[float]:
    s = s.strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _period_to_date(period: str) -> Optional[date]:
    """'2026/03' → date(2026, 3, 31)  |  '2026/03(P)' → date(2026, 3, 31)"""
    m = re.match(r"(\d{4})/(\d{2})", period)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    # 분기말 마지막 날 계산
    import calendar
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day)


def _data_type(period: str) -> str:
    if "(P)" in period:
        return "preliminary"
    if "(E)" in period:
        return "estimate"
    return "actual"


def _parse_section(section_text: str) -> list[dict]:
    """financial_highlight_quarter 또는 annual 섹션 → 레코드 목록"""
    pre = section_text[: section_text.find("<record>")]
    fields = re.findall(r"<field><!\[CDATA\[(.*?)\]\]></field>", pre)

    idx = {name: i for i, name in enumerate(fields)}
    records = re.findall(r"<record>(.*?)</record>", section_text, re.DOTALL)

    def get(vals, key):
        i = idx.get(key)
        return _parse_num(vals[i]) if i is not None and i < len(vals) else None

    rows = []
    for rec in records:
        date_m = re.search(r"<date>(.*?)</date>", rec)
        fs_m = re.search(r"<fs_nm><!\[CDATA\[(.*?)\]\]></fs_nm>", rec)
        vals = re.findall(r"<value><!\[CDATA\[(.*?)\]\]></value>", rec)

        if not date_m:
            continue

        period = date_m.group(1)
        fs_raw = fs_m.group(1) if fs_m else ""
        fs_type = "CFS" if "연결" in fs_raw else "OFS"

        rev = get(vals, "매출액(억원)")
        op = get(vals, "영업이익(억원)")
        ni = get(vals, "당기순이익(억원)")
        cni_key = next((k for k in idx if "지배주주순이익" in k and "비지배" not in k), None)
        cni = (_parse_num(vals[idx[cni_key]]) if cni_key and idx[cni_key] < len(vals) else None)
        ta = get(vals, "자산총계(억원)")
        te = get(vals, "자본총계(억원)")
        ce_key = next((k for k in idx if "지배주주지분" in k), None)
        ce = (_parse_num(vals[idx[ce_key]]) if ce_key and idx[ce_key] < len(vals) else None)

        shares_raw = get(vals, "발행주식수(천주)")

        rows.append({
            "period": period,
            "period_end": _period_to_date(period),
            "fs_type": fs_type,
            "data_type": _data_type(period),
            "revenue":            int(rev * _EOKWON) if rev is not None else None,
            "operating_profit":   int(op  * _EOKWON) if op  is not None else None,
            "net_income":         int(ni  * _EOKWON) if ni  is not None else None,
            "controlling_ni":     int(cni * _EOKWON) if cni is not None else None,
            "total_assets":       int(ta  * _EOKWON) if ta  is not None else None,
            "total_equity":       int(te  * _EOKWON) if te  is not None else None,
            "controlling_equity": int(ce  * _EOKWON) if ce  is not None else None,
            "eps":                int(get(vals, "EPS(원)"))   if get(vals, "EPS(원)")   is not None else None,
            "bps":                int(get(vals, "BPS(원)"))   if get(vals, "BPS(원)")   is not None else None,
            "dps":                int(get(vals, "DPS(원)"))   if get(vals, "DPS(원)")   is not None else None,
            "per":                get(vals, "PER(배)"),
            "pbr":                get(vals, "PBR(배)"),
            "roe":                get(vals, "ROE(%)"),
            "roa":                get(vals, "ROA(%)"),
            "operating_margin":   get(vals, "영업이익률(%)"),
            "shares_outstanding": int(shares_raw * 1000) if shares_raw is not None else None,
        })
    return rows


def fetch_quarterly(stock_code: str) -> list[dict]:
    """
    FnGuide XML에서 분기 재무지표 수집.
    반환: 연결+별도 분기 레코드 목록 (actual/preliminary/estimate 포함)
    """
    url = f"https://comp.fnguide.com/SVO2/xml/Snapshot_all/{stock_code}.xml"
    try:
        r = _SESSION.get(url, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("FnGuide 요청 실패 %s: %s", stock_code, e)
        return []

    text = r.content.decode("euc-kr", errors="replace")

    rows = []
    for section_tag in ("financial_highlight_ifrs_D", "financial_highlight_ifrs_B"):
        m = re.search(rf"<{section_tag}>(.*?)</{section_tag}>", text, re.DOTALL)
        if not m:
            continue
        sec = m.group(1)
        q_m = re.search(r"<financial_highlight_quarter>(.*?)</financial_highlight_quarter>", sec, re.DOTALL)
        if q_m:
            for row in _parse_section(q_m.group(1)):
                row["stock_code"] = stock_code
                rows.append(row)

    log.debug("FnGuide %s: %d 레코드", stock_code, len(rows))
    return rows
