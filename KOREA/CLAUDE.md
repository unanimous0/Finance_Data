# Finance_Data/KOREA — Claude Code 작업 규칙

## 🚨 절대 규칙: 시간/날짜는 항상 KST 실제 시각으로 확인

**작업 시작 전, 시간이 의심되면 반드시 `date '+%Y-%m-%d %A %H:%M:%S KST'` 실행.**

### 자주 한 실수 (재발 금지)

1. **모니터 알림 timestamp를 현재 시각으로 착각** — `tail -F` 초기 버퍼나 옛 이벤트가 늦게 들어올 수 있음. 알림 timestamp ≠ 현재 시각.
2. **로그 마지막 mtime을 현재로 착각** — 파일 mtime은 마지막 수정 시각.
3. **앞 메시지의 KST 시각을 끌어다 씀** — 시간 흘러서 더 이상 유효 X.
4. **UTC 시각을 KST로 오인** — 9시간 차이.

### 작업 시작 패턴

```bash
date '+%Y-%m-%d %A %H:%M:%S KST'   # 1. 진짜 시각 확인
# 2. 그 시각 기준으로 사용자에게 답변
# 3. 다음 cron 트리거까지 남은 시간 정확히 계산
```

### 시간 의존 작업 예시

- "지금 stockfut 도는 중인가" → 현재 시각 확인 (23:30 ± 10분 안인지)
- "다음 daily_update 언제" → 현재 시각 + 04:30 cron 비교
- "데이터 받아온 시점" → 보고서 mtime ≠ 현재. KST date로 비교
- "X시간 후에 끝남" → 시작 시각 + 소요 시간 = 종료 KST

---

## 운영 cron (5/16 기준)

| 시각 (KST) | 작업 | 소요 |
|---|---|---|
| 23:30 (월~금) | stockfut (LS) | ~10분 |
| 04:30 (매일) | daily_update 본체 (인포맥스/DART/KRX) | ~3시간 |
| 04:30 후 (직렬) | 분봉 일배치 (LS) | ~50분 |
| 일 03:00 | DB 백업 | 짧음 |

→ LS 사용 시간대: **23:30~23:40 + 07:30~08:30** (분봉 일배치 시작은 본체 후)
→ LENS 사용 가능: 그 외 시간 (단 LENS 24/7 가동 시 LS token 공유 충돌 가능 — `IGW00121` 발생 시 ls_api.py가 자동 처리)

---

## scheduler 재시작 전 절대 체크

```bash
pgrep -f "daily_update|backfill_" && echo "⛔ 진행 중 job 있음 — 재시작 차단"
```

진행 중 자식 job이 있으면 `tmux send-keys C-c` 또는 `kill`이 자식까지 죽임. graceful shutdown handler 추가했지만 BlockingScheduler가 가려서 작동 안 함 (별도 fix 필요).

---

## 메모리 시스템

`/home/una0/.claude/projects/-home-una0-projects-Finance-Data/memory/` 에 누적된 운영 정책/과거 사고 기록 있음. `MEMORY.md`가 인덱스.
