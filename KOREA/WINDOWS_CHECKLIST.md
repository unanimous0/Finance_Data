# 🪟 윈도우 환경 작업 체크리스트

> **목적**: 회사 윈도우 컴퓨터에서 인포맥스 API 및 증권사 HTS 데이터 형식 확인
> **작업 시간**: 약 1-2시간
> **필수 여부**: 필수 (Phase 2 진행 전)

---

## 📋 준비물

- [ ] 회사 윈도우 컴퓨터
- [ ] 인포맥스 API 계정 (API 키, 시크릿)
- [ ] 증권사 HTS 설치 및 API 사용 권한
- [ ] Python 설치 (데이터 저장용)
- [ ] 텍스트 에디터 또는 메모장

---

## 1️⃣ 인포맥스 API 데이터 형식 확인

### 1-1. API 문서 확인
- [ ] 인포맥스 개발자 포털 접속
- [ ] 사용 가능한 API 목록 확인
- [ ] 각 API의 엔드포인트, 파라미터, 응답 형식 확인

### 1-2. 종목 마스터 데이터 샘플 수집
```python
# 예상 코드 (실제는 인포맥스 문서 참조)
import requests

url = "https://api.infomax.co.kr/stocks/master"
headers = {"Authorization": "Bearer YOUR_API_KEY"}
response = requests.get(url, headers=headers)
data = response.json()

# 샘플 5-10건만 저장
with open("sample_stocks_master.json", "w", encoding="utf-8") as f:
    json.dump(data[:10], f, ensure_ascii=False, indent=2)
```

**확인 사항**:
- [ ] 응답 형식: JSON? CSV? XML?
- [ ] 종목코드 컬럼명: `stock_code`? `code`? `symbol`?
- [ ] 종목명 컬럼명: `stock_name`? `name`?
- [ ] 시장구분 컬럼명: `market`? `exchange`?
- [ ] 추가 컬럼: 업종, 시가총액 등 포함 여부
- [ ] 샘플 파일 저장: `sample_stocks_master.json`

### 1-3. 일봉 OHLCV 데이터 샘플 수집
```python
# 삼성전자(005930) 최근 30일 데이터
url = "https://api.infomax.co.kr/market/ohlcv"
params = {"stock_code": "005930", "days": 30}
response = requests.get(url, headers=headers, params=params)
data = response.json()

with open("sample_ohlcv_daily.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
```

**확인 사항**:
- [ ] 날짜 컬럼명: `date`? `time`? `trade_date`?
- [ ] 날짜 형식: `2026-02-17`? `20260217`? UNIX timestamp?
- [ ] 가격 컬럼명: `open_price`? `open`? `시가`?
- [ ] 가격 데이터 타입: INTEGER? DECIMAL? STRING?
- [ ] 거래량 컬럼명: `volume`? `qty`?
- [ ] 거래대금 포함 여부: `trading_value`? `amount`?
- [ ] 추가 컬럼: 전일대비, 등락률 등
- [ ] 샘플 파일 저장: `sample_ohlcv_daily.json`

### 1-4. 투자자별 수급 데이터 샘플 수집
```python
# 삼성전자 투자자별 수급
url = "https://api.infomax.co.kr/market/investor_trading"
params = {"stock_code": "005930", "days": 30}
response = requests.get(url, headers=headers, params=params)
data = response.json()

with open("sample_investor_trading.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
```

**확인 사항**:
- [ ] 투자자 유형 구분: `FOREIGN`? `외국인`? `01`?
  - 외국인: `FOREIGN`? `FOR`? `외국인`?
  - 기관: `INSTITUTION`? `INS`? `기관`?
  - 개인: `RETAIL`? `IND`? `개인`?
  - 연기금: `PENSION`? `PEN`? `연기금`?
- [ ] 순매수 컬럼명: `net_buy_volume`? `net_qty`? `순매수량`?
- [ ] 금액/수량 둘 다 제공?
- [ ] 매수/매도 수량 개별 제공?
- [ ] **중요**: 기관 데이터에 연기금 포함 여부 확인!
- [ ] 샘플 파일 저장: `sample_investor_trading.json`

### 1-5. 기타 데이터 (있으면)
- [ ] 시가총액 데이터
- [ ] 섹터/업종 분류
- [ ] 지수 구성종목 (KOSPI200, KOSDAQ150)
- [ ] 유동주식수

---

## 2️⃣ 증권사 HTS API 데이터 형식 확인

### 2-1. 사용 가능한 HTS 확인
- [ ] 키움증권 OpenAPI
- [ ] 이베스트투자증권 xingAPI
- [ ] 한국투자증권 OpenAPI
- [ ] 기타: ______________

### 2-2. 샘플 데이터 수집 (위와 동일한 방식)
- [ ] 종목 마스터
- [ ] 일봉 OHLCV
- [ ] 투자자별 수급

**파일명**: `hts_sample_*.json`

---

## 3️⃣ 데이터 분석 및 정리

### 3-1. 데이터 비교 문서 작성
각 데이터 소스별 차이점을 정리:

```markdown
# 데이터 소스별 비교

## 종목 마스터
| 항목 | 인포맥스 | HTS | 현재 스키마 |
|------|----------|-----|------------|
| 종목코드 | code | stock_cd | stock_code |
| 종목명 | name | stock_nm | stock_name |
| 시장구분 | market | exch | market |

## 일봉 OHLCV
| 항목 | 인포맥스 | HTS | 현재 스키마 |
|------|----------|-----|------------|
| 날짜 | date | trd_dt | time |
| 시가 | open | open_pr | open_price |
...
```

파일명: `DATA_FORMAT_COMPARISON.md`

### 3-2. 스키마 수정 필요 사항 정리
```markdown
# 스키마 수정 필요 사항

## ohlcv_daily 테이블
- [ ] 전일대비 컬럼 추가 필요
- [ ] 등락률 컬럼 추가 필요
- [ ] 가격 타입 INTEGER → DECIMAL 변경 검토

## investor_trading 테이블
- [ ] 투자자 유형 코드 확인 (FOREIGN vs FOR)
- [ ] 컬럼명 조정
...
```

---

## 4️⃣ 맥으로 데이터 전송

### 4-1. 수집한 파일 목록
- [ ] `sample_stocks_master.json`
- [ ] `sample_ohlcv_daily.json`
- [ ] `sample_investor_trading.json`
- [ ] `hts_sample_*.json` (선택)
- [ ] `DATA_FORMAT_COMPARISON.md`
- [ ] 인포맥스 API 문서 PDF (가능하면)

### 4-2. 전송 방법 (택1)
- [ ] 이메일로 전송
- [ ] 클라우드 (Google Drive, Dropbox 등)
- [ ] USB 메모리
- [ ] GitHub Private Repo

---

## 5️⃣ 완료 체크

- [ ] 모든 샘플 데이터 수집 완료
- [ ] 데이터 형식 비교 문서 작성 완료
- [ ] 맥으로 파일 전송 완료
- [ ] 맥에서 파일 수신 확인 완료

---

## 🎯 맥에서 할 일 (윈도우 작업 후)

1. 전송받은 샘플 데이터 확인
2. `DATA_FORMAT_COMPARISON.md` 검토
3. 필요시 스키마 수정
4. `database/models.py` (SQLAlchemy ORM) 작성
5. Phase 2 진행

---

## 💡 팁

1. **API 키 보안**: 샘플 코드에서 API 키 제거 후 전송
2. **적은 양만 수집**: 각 데이터 5-10건이면 충분
3. **에러 메시지도 저장**: API 에러 응답도 참고용으로 저장
4. **스크린샷**: API 문서 중요한 부분은 스크린샷으로 저장

---

**예상 소요 시간**: 1-2시간 (API 익숙도에 따라)

**완료 후**: DEVELOPMENT_LOG.md에 작업 내용 기록
