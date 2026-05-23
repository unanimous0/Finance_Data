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
| **02:00** | daily_update 본체 + 분봉 직렬 | 매일 |
| **08:30** | etf_snapshot (today+yesterday 2-pass) | 매일 |
| 23:30 | stockfut_today | 평일 |
| 03:00 | weekly_backup | 일요일 |
| 03:30 | update_listed_shares | 일요일 |
| 03:30 | quarterly_sector | 분기 첫 일요일 (1/4/7/10월) |
| 04:00 | quarterly_financials | 분기 마감 후 첫 일요일 (4/6/9/12월) |

상세 (요일별 동작, 2-pass 의미, 대기 로직, 동시성 가드, 일별 콜 분배): **`docs/스케줄러_운영.md`**

---

## scheduler 재시작 전 절대 체크

```bash
pgrep -f "daily_update|backfill_|backup_db|etf_snapshot|update_listed_shares|crawl_sector|collect_financials|stockfut" && echo "⛔ 진행 중 job 있음 — 재시작 차단"
```

진행 중 자식 job이 있으면 `C-c`/`kill`이 자식까지 죽임 (graceful handler 미작동, 5/24 03:00 백업이 이렇게 절단된 사례 있음).

### 잡 fire 시각 회피 (재시작 30분 전후 피하기)
- 02:00 daily_update / 03:00 weekly_backup / 03:30 update_listed_shares / 08:30 etf_snapshot / 23:30 stockfut (평일)
- 가능한 정각/30분 시각에서 5분 이상 떨어진 시점에 재시작

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
```

---

## 쿼리 가이드 포인터

- 수정주가 query (일봉/분봉, raw vs adj): `docs/데이터_적재_가이드.md`
- 투자자 수급 query (INSTITUTION 연기금 포함/제외) + 단위 규약 (×1000): `docs/인포맥스_API_정리.md`
- 인포맥스 API 전반: `docs/인포맥스_API_정리.md`

---

## 메모리 시스템

`/home/una0/.claude/projects/-home-una0-projects-Finance-Data/memory/` 누적된 운영 정책/사고 기록. `MEMORY.md`가 인덱스.
