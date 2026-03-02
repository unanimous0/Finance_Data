# 인포맥스 API 정리

> **출처**: 인포맥스 API 백서.pdf (32페이지)
> **최종 업데이트**: 2026-02-20
> **HOST**: `https://infomaxy.einfomax.co.kr`

---

## 📌 서비스 개요

- **대상**: 인포맥스 단말기 사용자 한정 유료 서비스
- **신청**: 단말기 9000번 화면 → API 서비스 신청 / api_infomax@yna.co.kr / 02-398-5208
- **응답 형식**: JSON (`success: true/false`)
- **SSL**: `session.verify = False` 필수
- **공통 에러**:
  - `access_denied`: 토큰 오류
  - `error params`: 필수 파라미터 누락
  - `error timeout`: 조회 시간 초과

---

## 💰 요금제 & 제한사항

| 플랜 | 일 사용량 | 분당 요청 | 스트리밍 | 월 이용료 |
|------|----------|----------|---------|---------|
| Lite | 0.2 GB | 60회 | X | 10만원 |
| Standard | 0.5 GB | 120회 | X | 25만원 |
| Pro | 1.0 GB | 180회 | O(준비중) | 50만원 |
| Premium | 별도 산정 | | | |

### ⚠️ 과거 데이터 조회 범위 (매우 중요!)

| 유형 | 과거 조회 가능 범위 |
|------|-----------------|
| **일별 조회성 패키지** (`/hist`) | **최대 30일** |
| 틱 계열 패키지 (`/tick`) | 최대 7일 |
| 그 외 패키지 | 최대 4개월 |

> 💡 **핵심**: `hist` API는 30일 제한이 있으므로, **매일 실행**해야 누락 없이 쌓임

---

## 🔑 인증

```python
headers = {"Authorization": "bearer YOUR_API_TOKEN"}
```

토큰: 단말기 9000번 화면에서 발급 (계정당 1개)

---

## 📦 전체 API 목록 (주식/ETF/지수 중심)

| 분류 | API | 설명 | 우선순위 |
|------|-----|------|---------|
| **주식** | `/api/stock/hist` | 일봉 OHLCV 일별 | 🔴 최고 |
| **주식** | `/api/stock/investor` | 투자자별 수급 일별 | 🔴 최고 |
| **주식** | `/api/stock/code` | 종목 마스터 (상장 종목) | 🔴 최고 |
| **주식** | `/api/stock/expired` | 폐지 종목 | 🔴 최고 |
| **주식** | `/api/stock/info` | 기본정보 (단일일자 다종목) | 🟡 중간 |
| **주식** | `/api/stock/foreign` | 외국인 지분율 | 🟡 중간 |
| **주식** | `/api/stock/rank` | 순위 (시총/거래량/등락률) | 🟢 낮음 |
| **주식** | `/api/stock/lending` | 신용거래 | 🟢 낮음 |
| **주식** | `/api/stock/borrowing` | 대차거래 | 🟢 낮음 |
| **주식** | `/api/stock/tick` | 체결(정규장) 틱 | 🟢 낮음 |
| **ETF** | `/api/etf/hist` | ETF 일별 NAV | 🟡 중간 |
| **ETF** | `/api/etf/port` | ETF PDF (구성종목) | 🟡 중간 |
| **ETF** | `/api/etp/list` | ETF/ETN 상세검색 | 🟢 낮음 |
| **ETF** | `/api/etp` | ETP 추가정보 | 🟢 낮음 |
| **ETF** | `/api/etf/search` | 종목 보유 ETF 검색 | 🟢 낮음 |
| **지수** | `/api/index/hist` | 지수 일별 OHLCV | 🟡 중간 |
| **지수** | `/api/index/investor/hist` | 지수 투자자별 수급 | 🟢 낮음 |
| **지수** | `/api/index/code` | 지수 코드 검색 | 🟢 낮음 |

---

## 🏆 우선순위 1: 핵심 API (일별 업데이트)

### 1. 일봉 OHLCV - `/api/stock/hist`

**용도**: DB `ohlcv_daily` + `market_cap_daily` 업데이트

```
URL: /api/stock/hist
```

**파라미터:**
| 파라미터 | 타입 | 필수 | 설명 |
|---------|------|------|------|
| code | String | O | 6자리 종목코드 (복수: `,`로 구분 가능) |
| startDate | Number | | 시작일 (YYYYMMDD), 미입력시 endDate-30 |
| endDate | Number | | 종료일 (YYYYMMDD), 미입력시 today |

**응답 필드:**
| API 필드 | 타입 | DB 컬럼 | 비고 |
|---------|------|---------|------|
| date | Number | time | YYYYMMDD → DATE 변환 필요 |
| code | String | stock_code | |
| open_price | Number | open_price | |
| high_price | Number | high_price | |
| low_price | Number | low_price | |
| close_price | Number | close_price | |
| trading_volume | Number | volume | |
| trading_value | Number | trading_value | |
| listed_shares | Number | - | 상장주식수 (market_cap 계산 참고용) |
| base_price | Number | - | 기준가 (저장 안 함) |
| change | Number | - | 전일대비 (저장 안 함) |
| change_rate | Number | - | 등락률 (저장 안 함) |

> ⚠️ **시가총액은 `hist` API에 없음** → `/api/stock/rank` 또는 `/api/stock/info`에서 marketcap 조회하거나, `close_price × listed_shares`로 계산

**샘플 코드:**
```python
import requests

session = requests.Session()
session.verify = False

api_url = 'https://infomaxy.einfomax.co.kr/api/stock/hist'
params = {
    "code": "005930",  # 삼성전자 (복수: "005930,000660")
    "startDate": "20260214",
    "endDate": "20260220"
}
headers = {"Authorization": f"bearer {TOKEN}"}

r = session.get(api_url, params=params, headers=headers, timeout=30)
data = r.json()
# data['results'] = [{date, code, open_price, high_price, ...}, ...]
```

---

### 2. 투자자별 수급 - `/api/stock/investor`

**용도**: DB `investor_trading` 업데이트

```
URL: /api/stock/investor
```

**파라미터:**
| 파라미터 | 타입 | 필수 | 설명 |
|---------|------|------|------|
| code | String | O | 6자리 종목코드 |
| investor | String | | 투자자 구분 (미입력시 전체 반환) |
| startDate | Number | | 시작일, 미입력시 endDate-30 |
| endDate | Number | | 종료일, 미입력시 today |

**투자자 구분 코드 (API → DB 매핑):**
| API `investor` 값 | DB `investor_type` | 설명 |
|------------------|-------------------|------|
| `외국인` | `FOREIGN` | 외국인 |
| `기관계` | `INSTITUTION` | 기관 전체 **(연기금 포함!)** |
| `연기금` | `PENSION` | 연기금 |
| `개인` | `RETAIL` | 개인 |

> ⚠️ **중요**: API의 `기관계` = DB의 `INSTITUTION` = 연기금 포함 기관 전체
> 순수 기관 = INSTITUTION - PENSION (계산 필요)

**전체 투자자 구분 목록 (API 지원):**
`기관계 / 금융투자 / 보험 / 투신 / 사모 / 은행 / 종금저축 / 연기금 / 정부 / 기타법인 / 개인 / 외국인합 / 외국인 / 기타외국인`

**응답 필드:**
| API 필드 | 타입 | DB 컬럼 | 비고 |
|---------|------|---------|------|
| date | Number | time | |
| code | String | stock_code | |
| investor | String | investor_type | 위 매핑 적용 |
| ask_value | Number | - | 누적 매도거래대금 (천원) |
| bid_value | Number | - | 누적 매수거래대금 (천원) |
| ask_volume | Number | - | 누적 매도거래량 |
| bid_volume | Number | - | 누적 매수거래량 |
| **계산** | | net_buy_value | `bid_value - ask_value` |

**샘플 코드:**
```python
params = {
    "code": "005930",
    "investor": "외국인,기관계,연기금,개인",  # 한 번에 4개 조회
    "startDate": "20260214",
    "endDate": "20260220"
}
# investor 미입력시 전체 투자자 일괄 반환 (권장)
```

---

### 3. 종목 마스터 - `/api/stock/code`

**용도**: DB `stocks` 테이블 신규 상장 종목 추가 / 최신화

```
URL: /api/stock/code
```

**파라미터:**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| search | String | 통합검색 (미입력시 전체) |
| code | String | 6자리 종목코드 검색 |
| name | String | 종목명 검색 |
| market | String | 시장 [1:거래소 2:거래소기타 5:KRX 7:코스닥 8:코스닥기타] |
| type | String | 종목 구분 [ST:주식 EF:ETF EN:ETN MF:뮤추얼 RT:리츠 ...] |

**응답 필드:**
| 필드 | DB 컬럼 | 비고 |
|-----|---------|------|
| code | stock_code | |
| kr_name | stock_name | |
| market | market | |
| equity_type | - | ST/EF/EN 등 |
| isin | - | ISIN 코드 |
| listed_date | listing_date | |

**활용:**
```python
# 전체 상장 종목 조회
params = {"search": "", "type": "ST"}  # 주식만
params = {"search": "", "type": "EF"}  # ETF만

# 94개 누락 ETF 데이터 보충 시 활용
params = {"type": "EF"}  # 전체 ETF 목록 → DB와 비교
```

---

### 4. 폐지 종목 - `/api/stock/expired`

**용도**: DB `stocks` 상장폐지 처리 (is_active=False, delisting_date 업데이트)
> ✅ 현재 DB에 23개 미매칭 종목 처리에 활용 가능

```
URL: /api/stock/expired
```

**파라미터:**
| 파라미터 | 타입 | 설명 |
|---------|------|------|
| name | String | 종목명 검색 |
| type | String | 종목 구분 [ST/EF/EN...] |
| code | String | 종목코드 |
| startDate | Number | 폐지일 기준 시작일, 미입력시 today-365 |
| endDate | Number | 폐지일 기준 종료일, 미입력시 today |

**응답 필드:** `isin, code, market_type, equity_type, kr_name, listed_date, delisted_date`

---

## 🟡 우선순위 2: 보조 API

### 5. 주식 기본정보 - `/api/stock/info`

**용도**: 특정 날짜 다종목 스냅샷 조회 (시가총액 포함)

```
URL: /api/stock/info
파라미터: code (필수, ,로 복수), date (YYYYMMDD, 미입력시 today-1)
```

**응답 필드**: `date, code, kr_name, market, open/high/low/close_price, trading_volume, trading_value, listed_shares`

> 💡 **시가총액 계산**: `close_price × listed_shares` (market_cap_daily에 저장)

---

### 6. 외국인 지분율 - `/api/stock/foreign`

**용도**: `floating_shares` 테이블 업데이트 참고 / 외국인 보유 현황

```
URL: /api/stock/foreign
파라미터: code (필수), startDate, endDate (최대 30일)
```

**응답 필드:**
| 필드 | 설명 | DB 활용 |
|-----|------|--------|
| listed_shares | 상장주식수 | floating_shares.total_shares |
| frn_ownership_vol | 외국인 보유 수량 | 참고용 |
| frn_ownership_ratio | 외국인 보유율(%) | 참고용 |
| frn_limit_ratio | 외국인 투자 한도(%) | 참고용 |

---

### 7. ETF 일별 NAV - `/api/etf/hist`

**용도**: 94개 누락 ETF의 일별 NAV 데이터 수집
> ✅ ETF는 `hist` API로 OHLCV를 가져오되, NAV는 이 API로 별도 수집

```
URL: /api/etf/hist
파라미터: code (필수), startDate, endDate
```

**응답 필드:** `date, code, kr_name, nav, tracking_error_rate, disparate_ratio, net_asset`

---

### 8. ETF 구성종목(PDF) - `/api/etf/port`

**용도**: `etf_portfolios` 테이블 업데이트

```
URL: /api/etf/port
파라미터: code (필수), date (미입력시 today), sort (value/volume)
```

**응답 필드:** `date, code, kr_name, constituents, etf_value, port_code, port_name, port_value, port_volume`

---

### 9. 지수 일별 - `/api/index/hist`

**용도**: KOSPI/KOSDAQ 지수 데이터 (향후 index_components 테이블 활용)

```
URL: /api/index/hist
파라미터: code (필수, 6자리 지수코드), startDate, endDate
주요 지수코드: KGG01P(KOSPI), QGG01P(KOSDAQ)
```

---

## 🟢 우선순위 3: 향후 활용 API

### 10. 순위 - `/api/stock/rank`
```
URL: /api/stock/rank
파라미터: market, type, date, rank (등락률/시총/거래대금/거래량/가격)
```
→ 전체 시장 한 번에 snapshot 가져올 때 유용 (시가총액 포함)

### 11. 신용거래 - `/api/stock/lending`
```
URL: /api/stock/lending
응답: 융자/대주 신규·상환·잔고·잔고율·공여율
```

### 12. 대차거래 - `/api/stock/borrowing`
```
URL: /api/stock/borrowing
응답: 당일/전일 대차 잔고량·잔고금액, 체결량·체결금액, 상환량·상환금액
```

### 13. 지수 투자자별 수급 - `/api/index/investor/hist`
```
URL: /api/index/investor/hist
파라미터: sclasscd (지수코드), investor, startDate, endDate
응답: 지수별 투자자 매수/매도 거래량·금액 (단위: 천주, 백만원)
```

---

## 🔄 일별 업데이트 전략

### 매일 실행 순서 (장 마감 후 18:00~)

```
1단계: stocks 테이블 업데이트
   - /api/stock/code  → 신규 상장 종목 추가
   - /api/stock/expired → 상장폐지 종목 is_active=False 처리

2단계: 시계열 데이터 업데이트 (3,820개 종목)
   - /api/stock/hist  → ohlcv_daily 업데이트
   - /api/stock/info  → market_cap_daily 업데이트 (listed_shares × close_price)
   - /api/stock/investor (외국인,기관계,연기금,개인)  → investor_trading 업데이트

3단계: 보조 데이터 (비정기)
   - /api/stock/foreign → floating_shares 업데이트 (주간 또는 변동시)
   - /api/etf/port → etf_portfolios 업데이트 (주간)
```

### 증분 업데이트 로직

```python
# DB에서 마지막 수집일 확인
last_date = SELECT MAX(time) FROM ohlcv_daily  # → 2026-02-13

# 마지막 수집일 다음날부터 오늘까지 수집
start_date = last_date + 1 day
end_date = today - 1 day  # 당일 데이터는 장 마감 후

# ⚠️ 30일 제한: start~end가 30일 초과하면 30일씩 나눠서 요청
```

### 종목별 API 호출 횟수 계산 (Lite 플랜 기준)

| 대상 | 종목 수 | API 호출 | 소요 시간 |
|------|--------|---------|---------|
| OHLCV (hist) | 3,820개 | 3,820회 | ~64분 (60회/분) |
| 투자자 수급 | 3,820개 | 3,820회 | ~64분 |
| **합계** | | **~7,640회** | **~2시간** |

> ⚠️ Lite 플랜(60회/분)으로는 매일 업데이트 시간이 빠듯함 → `code` 파라미터에 `,`로 복수 종목 지정하여 호출 횟수 줄이기 검토 필요

---

## 💡 활용 아이디어 (프로젝트별)

### 현재 부족한 데이터 해결

| 문제 | 해결 API |
|------|---------|
| 94개 ETF 시계열 없음 | `/api/stock/hist` + `/api/etf/hist` |
| 23개 상장폐지 종목 미처리 | `/api/stock/expired` |
| floating_shares 비정기 업데이트 | `/api/stock/foreign` (listed_shares 활용) |

### 향후 신규 데이터

| 데이터 | API | DB 테이블 |
|--------|-----|---------|
| ETF 구성종목 | `/api/etf/port` | etf_portfolios |
| KOSPI/KOSDAQ 지수 | `/api/index/hist` | (신규 테이블) |
| 신용·대차 잔고 | `/api/stock/lending` `/api/stock/borrowing` | (신규 테이블) |
| 외국인 지분율 일별 | `/api/stock/foreign` | (신규 컬럼 or 테이블) |

---

## 📝 기존 테스트 스크립트 현황

| 스크립트 | 용도 | 상태 |
|---------|------|------|
| `scripts/test_infomax_api.py` | hist API 연결 테스트 + 2월 데이터 조회 | ✅ 완성 |
| `scripts/test_investor_api.py` | investor API 전체 투자자 구분 코드 테스트 | ✅ 완성 |
| `scripts/test_pension_fund.py` | 연기금 데이터 존재 여부 테스트 | ✅ 완성 |
| `scripts/test_pension_fund_comprehensive.py` | 전체 종목 연기금 전수 조사 | ✅ 완성 |
| `collectors/infomax.py` | 실제 수집기 (ETL용) | ⏳ 미완성 |
