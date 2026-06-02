# Finance_Data/KOREA — Claude Code 작업 규칙

> **SSoT 안내**: 프로젝트 상태/Phase/데이터 현황은 `MEMORY.md` (auto-memory)와 `PROJECT.md` / `TODO.md` 참조. 본 파일은 운영 규칙 + 명령 cheatsheet만.

## 🚨 절대 규칙: 모든 응답 첫 줄에 `date` 실행

```bash
date '+%Y-%m-%d %A %H:%M:%S KST'
```

판단 없이 실행. 시간 의존 아닌 작업이면 결과 무시하면 됨 — 무시 비용 0, 안 한 비용 사고.
배경/자주 한 실수: memory `feedback_time_check.md`.

---

## 운영 cron (요약)

| 시각 (KST) | 잡 | 빈도 |
|---|---|---|
| **02:00** | daily_update 본체 (외인 STEP skip) + 분봉 직렬 | 매일 |
| **08:30** | 아침 종합 보충 = ETF PDF/마스터 + 누락 보충(OHLCV/수급/외인) | 매일 |
| 23:30 | stockfut_today | 평일 |
| 03:00 | weekly_backup | 일요일 |
| 03:30 | update_listed_shares | 일요일 |
| 03:30 | quarterly_sector | 분기 첫 일요일 (1/4/7/10월) |
| 04:00 | quarterly_financials | 분기 마감 후 첫 일요일 (4/6/9/12월) |

**외인 지분율은 02:00이 아니라 08:30에 수집**: 인포맥스 외인은 익일 05:30 이후 등록 → 02:00 본체는 항상 빈 응답이라 STEP skip(`collect_foreign=False`). 08:30 종합 보충이 `run_update(missing_only=True)`로 외인 + 02:00에 빠진 OHLCV/수급을 함께 메움 (최근 3영업일 무조건 검토 + 가장 뒤처진 테이블 last+1, 최대 10일 cap).

상세 (요일별 동작, 2-pass 의미, 대기 로직, 동시성 가드, 일별 콜 분배): **`docs/스케줄러_운영.md`**

---

## scheduler 코드 변경 → 재시작 → 검증 루틴

**scheduler가 참조하는 코드를 바꿨으면 반드시 재시작해야 반영됨** (lazy import라도 프로세스 새로 떠야 새 코드 로드). 안 하면 "바꿨는데 안 도는" 뻘짓.

### 1) 재시작 전 절대 체크 — 진행 중 job 차단
```bash
pgrep -f "daily_update|backfill_|backup_db|etf_snapshot|update_listed_shares|crawl_sector|collect_financials|stockfut" && echo "⛔ 진행 중 job 있음 — 재시작 차단"
```
진행 중 자식 job이 있으면 `C-c`/`kill`이 자식까지 죽임 (graceful handler 미작동, 5/24 03:00 백업이 이렇게 절단된 사례 있음).

### 2) 잡 fire 시각 회피 (재시작 30분 전후 피하기)
- 02:00 daily_update / 03:00 weekly_backup / 03:30 update_listed_shares / 08:30 etf_snapshot / 23:30 stockfut (평일)
- 정각/30분 시각에서 5분 이상 떨어진 시점에 재시작

### 3) 재시작 (tmux 세션 `kdata_scheduler`)
```bash
tmux send-keys -t kdata_scheduler C-c    # 종료
# (몇 초 대기 후)
tmux send-keys -t kdata_scheduler "python schedulers/daily_scheduler.py 2>&1 | tee -a logs/scheduler.log" Enter
```

### 4) 재시작 후 반영 검증 — 빠짐없이 반영됐는지 확인
```bash
bash scripts/verify_scheduler_sync.sh
```
가동 중 프로세스 시작 시각 vs scheduler 참조 .py 전체 mtime 비교. 시작 후 수정된 파일 있으면 ⛔ + 종료코드 1 (재시작 필요). 모두 이전이면 ✅. **변경/재시작 작업 끝에 항상 실행할 것.**

### 시작 시 자동 보충 (`startup_catchup`)
재시작 시 누락된 주간 잡 자동 보충:
- weekly_backup: 최신 백업 파일 mtime > 8일 → 즉시 실행
- update_listed_shares: floating_shares max(updated_at) > 8일 → 즉시 실행
- daily_update / etf_snapshot은 자체 갭 회수 로직 있어 catch-up 불필요
- stockfut은 historical 불가 → catch-up 불가능

tmux 세션: `kdata_scheduler`.

---

## 자주 쓰는 명령

```bash
# 환경
cd /home/una0/projects/Finance_Data/KOREA
source venv/bin/activate
psql -d korea_stock_data                            # peer 인증

# 수동 수집
python scripts/daily_update.py                      # 기본: end=어제(영업일)
python scripts/daily_update.py 20260523             # 특정 날짜
python scripts/daily_update.py --missing-only       # 갭 재수집
python scripts/update_listed_shares.py
python scripts/etf_snapshot.py

# 백필 (STOP/CONT 가능)
python scripts/backfill_etf_pdf.py
python scripts/backfill_30sec_bars.py
python scripts/backfill_adjusted_daily.py

# 검증/모니터링
python scripts/check_collection_status.py
python scripts/data_quality_report.py
pytest                                              # 전체 테스트
pytest tests/test_validators -v

# DB
python scripts/backup_db.py                         # pg_dump -Fc (주간)
bash scripts/restore_db.sh <dump_file>              # pg_restore + 인덱스 자동 재생성

# scheduler
tmux attach -t kdata_scheduler
bash scripts/verify_scheduler_sync.sh                # 재시작 후 코드 반영 검증
```

---

## 쿼리 가이드 포인터

- 수정주가 query (일봉/분봉, raw vs adj): `docs/데이터_적재_가이드.md`
- 투자자 수급 query (INSTITUTION 연기금 포함/제외) + 단위 규약 (×1000): `docs/인포맥스_API_정리.md`
- 인포맥스 API 전반: `docs/인포맥스_API_정리.md`

---

## 메모리 시스템

`/home/una0/.claude/projects/-home-una0-projects-Finance-Data/memory/` 누적된 운영 정책/사고 기록. `MEMORY.md`가 인덱스.
