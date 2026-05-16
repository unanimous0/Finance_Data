# Finance_Data/KOREA — Claude Code 작업 규칙

## 🚨 절대 규칙: 모든 응답 첫 줄에 `date` 무조건 실행

**예외 없음.** "시간 의존 작업인지" 판단하지 마. 무조건 첫 명령:

```bash
date '+%Y-%m-%d %A %H:%M:%S KST'
```

판단 없이 실행. 결과 시각 기준으로 사용자에게 답변. 시간 의존 아닌 작업이면 결과는 무시하면 됨 — 무시 비용은 0, 안 한 비용은 사고.

### 왜 강제 (CLAUDE.md 규칙 만들어놓고 어긴 사례)

- 5/14~5/16 사이 시간/날짜 4시간~10시간씩 틀림 반복
- "시간 의존 작업인지 판단 후 date 호출"이 무용지물 — 판단 자체가 종종 틀림
- 따라서 판단 단계 제거. **무조건 매 응답 첫 명령으로 date**.

### 자주 한 실수 (재발 금지 — date로 확실히 검증)

1. **모니터 알림 timestamp를 현재 시각으로 착각** — `tail -F` 초기 버퍼나 옛 이벤트가 늦게 들어올 수 있음. 알림 timestamp ≠ 현재 시각.
2. **로그 마지막 mtime을 현재로 착각** — 파일 mtime은 마지막 수정 시각.
3. **앞 메시지의 KST 시각을 끌어다 씀** — 시간 흘러서 더 이상 유효 X.
4. **UTC 시각을 KST로 오인** — 9시간 차이.
5. **cron이 도는지 안 도는지 시간 정확히 안 보고 답함** — stockfut 월~금만, daily_update 매일 등 cron schedule + 현재 시각 + 요일 종합 판단.

### 시간 의존 작업 예시

- "지금 stockfut 도는 중인가" → 현재 시각 + 요일 확인 (23:30 ± 10분 + 월~금)
- "다음 daily_update 언제" → 현재 시각 + 04:30 cron 비교
- "데이터 받아온 시점" → 보고서 mtime ≠ 현재. KST date로 비교
- "X시간 후에 끝남" → 시작 시각 + 소요 시간 = 종료 KST
- "오늘 무슨 데이터 들어옴" → 오늘 요일 + 어제 영업일 확인

---

## 운영 cron (5/17 기준)

| 시각 (KST) | 작업 | 소요 |
|---|---|---|
| 23:30 (월~금) | stockfut (LS) | ~10분 |
| 04:30 (매일) | daily_update 본체 (인포맥스/DART/KRX) + Phase 5 수정주가 자동 | ~3시간 |
| 04:30 후 (직렬) | 분봉 일배치 (LS) | ~50분 |
| 일 03:00 | DB 백업 | 짧음 |

→ LS 사용 시간대: **23:30~23:40 + 07:30~08:30 (Phase 5 의심 종목만 추가 ~수 분)**
→ LENS 사용 가능: 그 외 시간 (단 LENS 24/7 가동 시 LS token 공유 충돌 가능 — `IGW00121` 발생 시 ls_api.py가 자동 처리)

## 수정주가 query (5/17~)

| 데이터 | raw | adjusted |
|---|---|---|
| 일봉 | `SELECT close_price FROM ohlcv_daily` | `SELECT adj_close FROM ohlcv_daily` |
| 분봉 | `SELECT close FROM ohlcv_intraday` | `SELECT close FROM ohlcv_intraday_adjusted` (view, 자동) |

분봉 view는 raw × adj_factor 자동 곱셈 + volume은 raw 유지 + raw_* 별도 노출.
corporate_actions 테이블에서 이벤트 발생 종목 + 일자 + factor 조회 가능.

---

## scheduler 재시작 전 절대 체크

```bash
pgrep -f "daily_update|backfill_" && echo "⛔ 진행 중 job 있음 — 재시작 차단"
```

진행 중 자식 job이 있으면 `tmux send-keys C-c` 또는 `kill`이 자식까지 죽임. graceful shutdown handler 추가했지만 BlockingScheduler가 가려서 작동 안 함 (별도 fix 필요).

---

## 메모리 시스템

`/home/una0/.claude/projects/-home-una0-projects-Finance-Data/memory/` 에 누적된 운영 정책/과거 사고 기록 있음. `MEMORY.md`가 인덱스.
