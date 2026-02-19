# 인포맥스 API 정리

## 📌 필요한 API 2개

### 1. 일봉 OHLCV - `/api/stock/hist`

**엔드포인트:**
```
HOST: https://infomaxy.einfomax.co.kr
URL: /api/stock/hist
```

**요청 파라미터:**
| 파라미터 | 타입 | 필수 | 설명 |
|---------|------|------|------|
| code | String | O | 6자리 종목코드 or ISIN 코드 |
| endDate | Number | | 조회 종료일 (YYYYMMDD), 미입력시 today |
| startDate | Number | | 조회 시작일 (YYYYMMDD), 미입력시 endDate-30 |

**응답 데이터:**
| 필드 | 타입 | 설명 | DB 컬럼 매핑 |
|------|------|------|-------------|
| date | Number | 일자 | time |
| code | String | 종목코드 | stock_code |
| open_price | Number | 시가 | open_price |
| high_price | Number | 고가 | high_price |
| low_price | Number | 저가 | low_price |
| close_price | Number | 현재가(종가) | close_price |
| trading_volume | Number | 거래량 | volume |
| trading_value | Number | 거래대금 | trading_value |
| base_price | Number | 기준가 | - |
| change | Number | 전일대비 | - |
| change_rate | Number | 등락률 | - |
| listed_shares | Number | 상장주식수 | - |

**샘플 코드:**
```python
import requests

session = requests.Session()
session.verify = False

api_url = 'https://infomaxy.einfomax.co.kr/api/stock/hist'
params = {
    "code": "005930",  # 삼성전자
    "startDate": "20240101",
    "endDate": "20240131"
}
headers = {"Authorization": 'bearer TOKEN'}

r = session.get(api_url, params=params, headers=headers)
data = r.json()
```

---

### 2. 투자자별 수급 - `/api/stock/investor`

**엔드포인트:**
```
HOST: https://infomaxy.einfomax.co.kr
URL: /api/stock/investor
```

**요청 파라미터:**
| 파라미터 | 타입 | 필수 | 설명 |
|---------|------|------|------|
| code | String | O | 6자리 종목코드 or ISIN 코드 |
| investor | String | | 투자자 (복수 코드 조회시 ,로 구분) |
| endDate | Number | | 조회 종료일 (YYYYMMDD), 미입력시 today-1 |
| startDate | Number | | 조회 시작일 (YYYYMMDD), 미입력시 endDate-30 |

**투자자 구분 코드:**
- 기관계
- 금융투자
- 보험
- 투신
- 사모
- 은행
- 종금저축은행
- 기타금융
- 연기금
- 기타법인
- 개인
- 외국인
- 기타외국인

**응답 데이터:**
| 필드 | 타입 | 설명 | DB 컬럼 매핑 |
|------|------|------|-------------|
| date | Number | 일자 | time |
| code | String | 종목코드 | stock_code |
| investor | String | 투자자 | investor_type |
| ask_volume | Number | 누적 매도거래량 | sell_volume |
| ask_value | Number | 누적 매도거래대금(천원) | sell_value |
| bid_volume | Number | 누적 매수거래량 | buy_volume |
| bid_value | Number | 누적 매수거래대금(천원) | buy_value |

**계산 필요:**
- net_buy_volume = bid_volume - ask_volume (순매수량)
- net_buy_value = bid_value - ask_value (순매수금액)

**investor_type 매핑:**
| API 값 | DB 값 | 설명 |
|--------|-------|------|
| 외국인 | FOREIGN | 외국인 |
| 기관계 - 연기금 | INSTITUTION | 기관 (순수) |
| 연기금 | PENSION | 연기금 |
| 개인 | RETAIL | 개인 |

---

## 🔑 인증

**헤더:**
```python
headers = {
    "Authorization": "bearer YOUR_API_TOKEN"
}
```

**토큰 발급:**
- 인포맥스 단말기 9000번 화면에서 신청
- 또는 api_infomax@yna.co.kr 문의

---

## ⚠️ 제한사항

### 과거 데이터 조회 범위:
- **일별 조회성 패키지**: 조회 시점으로부터 최대 과거 **30일**
- 틱 계열 패키지: 조회 시점으로부터 최대 과거 7일
- 그 외 패키지: 조회 시점으로부터 최대 과거 4개월

### 사용량 제한 (예시 - Lite):
- 0.2 GB / 일
- 60회 / 분

---

## 📝 참고사항

1. **SSL 인증**: `session.verify = False` 필요
2. **응답 형식**: JSON
3. **에러 코드**:
   - `access_denied`: 토큰 오류
   - `error params`: 필수 파라미터 누락
   - `error timeout`: 조회 시간 초과
