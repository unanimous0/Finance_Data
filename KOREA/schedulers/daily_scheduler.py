"""
데이터 수집 + 백업 스케줄러

잡 목록:
    daily_update      — 매일 02:00 KST (월~일)           OHLCV/시가총액/수급/외국인지분율 + 배당 + LENS export
                          + 끝에 분봉 일배치 직렬 호출 (종목/ETF + 지수 + 지수선물 + futures_master export)
                          (LENS 야간 사용 시간 확보 위해 23:00 분봉 일배치 cron을 daily_update 끝으로 통합)
    stockfut_today    — 매일 23:30 KST (월~금)           주식선물 30초봉 (t8406 historical 불가, 당일만, ~10분)
    weekly_backup     — 매주 일요일 03:00 KST             DB 백업 + 7일 보관
    quarterly_sector  — 분기 첫 번째 일요일 03:30 KST     FICS 업종 크롤링 (1/4/7/10월)

새벽 5시 30분 선택 이유:
    - 인포맥스 외인지분율 API 익일 패턴 (새벽~오전 제공) 안전 마진
    - daily_update.py의 갭 backfill 로직이 평일/주말/공휴일 자동 처리 → 매일 돌려도 무해
    - 백업 잡(일 03:00) 및 섹터 잡(03:30)과 충돌 없음

실행법:
    python schedulers/daily_scheduler.py          # 포그라운드 실행 (Ctrl+C로 종료)
    tmux new-session -d -s scheduler "cd /home/una0/projects/Finance_Data/KOREA && source venv/bin/activate && python schedulers/daily_scheduler.py"
"""

import sys
import signal
import logging
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

KST = ZoneInfo("Asia/Seoul")

from schedulers.notifier import notify_job
from schedulers.job_state import infomax_busy


def _read_report_tail(report_path: Path, max_chars: int = 1500) -> str:
    """daily_update 보고서 마지막 부분 읽기 (실패/이상 시 상세 첨부용)."""
    try:
        text = report_path.read_text(encoding="utf-8")
        if len(text) <= max_chars:
            return text
        return "...\n" + text[-max_chars:]
    except Exception as e:
        return f"(보고서 읽기 실패: {e})"


def _adj_applied(events: list[tuple[str, str]]) -> dict:
    """주가이벤트 (날짜, 종목코드) 각각에 수정계수가 반영됐는지 DB로 확인.

    판정: adj_factor(이벤트일) != adj_factor(직전 거래일) → 그 날짜에 자본변동이
    반영된 것. 같으면 미반영.

    "adj_factor == 1.0 이면 미반영"이 아니라 **직전일과의 변화**로 보는 이유:
    factor는 최신가 기준 정규화라 최신 행이 항상 1.0이고(reference_adj_close_semantics),
    이벤트 이후 또 다른 이벤트가 생기면 이벤트일 factor도 1.0이 아니게 된다.
    그때 '==1.0' 기준은 미반영 건을 반영됨으로 잘못 읽는다. 변화량 기준은 안 흔들린다.

    호출 시점 주의: run_adjusted_price_pipeline(daily_update 후반, gap>15% 종목만
    LS sujung=Y 재호출)이 끝난 뒤라야 factor가 확정된다. 이 함수는 daily_update
    완료 후 알림 조립 단계에서만 부른다.

    반환: {(date, code): True(반영)/False(미반영)}. DB 실패 시 {} — 호출부가
    전건을 미반영으로 취급해 알림을 잃지 않는다.
    """
    if not events:
        return {}
    try:
        from scripts.daily_update import get_conn
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT e.d, e.code,
                           o.adj_factor,
                           (SELECT p.adj_factor FROM ohlcv_daily p
                             WHERE p.stock_code = e.code AND p.time < e.d::date
                             ORDER BY p.time DESC LIMIT 1)
                    FROM unnest(%s::text[], %s::text[]) AS e(d, code)
                    LEFT JOIN ohlcv_daily o
                      ON o.stock_code = e.code AND o.time = e.d::date
                """, ([d for d, _ in events], [c for _, c in events]))
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[스케줄러] 수정계수 확인 실패 — 전건 미반영 취급: {e}")
        return {}

    out = {}
    for d, code, cur_f, prev_f in rows:
        # 직전 행이 없으면(신규상장 등) 판단 불가 → 미반영으로 두고 사람이 본다
        out[(d, code)] = (cur_f is not None and prev_f is not None
                          and abs(cur_f - prev_f) > 0.001)
    return out


def _extract_price_events(text: str, max_lines: int = 15) -> str:
    """보고서에서 주가이벤트의심(±30% 초과) 항목만 추출 — 수정계수 반영 여부로 분리.

    형식: "🚨 [주가이벤트의심] N건" 헤더 + "날짜 종목코드 종목명 상승/하락 XX% ... [...의심]" 라인.

    ±30% 초과의 대부분(8/4~8/13 실측 15건 중 12건)은 액면병합·분할이고 수정계수가
    이미 붙어 처리가 끝난 건이다. 전건을 🚨로 올리면 정작 손봐야 할 미반영 건이
    그 사이에 묻힌다 — 실제로 미반영 3건(씨씨에스·더테크놀로지·시스웍)이 모두
    묻혀 있었다. 그래서 **미반영 건만 🚨**로 올리고 반영된 건은 마커 없는 정보
    줄로 내린다(마커가 없으면 notifier가 전송하지 않음 → 반영 건만 있는 날은 침묵).
    """
    import re
    hm = re.search(r"\[주가이벤트의심\]\s*([\d,]+)\s*건", text)
    if not hm:
        return ""
    count = hm.group(1)
    # 데이터 라인: 날짜 + ... + 상승/하락 N% + [무상감자.../주식병합... 의심]
    lines = re.findall(
        r"^\s*(\d{4}-\d{2}-\d{2}\s+\S+\s+.*?(?:상승|하락)\s+[\d.]+%.*?\[[^\]]*의심\])\s*$",
        text, re.MULTILINE)
    if not lines:
        # 라인 파싱 실패 = 내용을 모른다 → 종전대로 🚨 (놓치는 것보다 낫다)
        return f"🚨 주가이벤트의심 {count}건 (±30% 초과 — 수정주가 확인)"

    parsed = []
    for l in lines:
        flat = " ".join(l.split())
        m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\S+)", flat)
        parsed.append((m.group(1) if m else None, m.group(2) if m else None, flat))

    applied = _adj_applied([(d, c) for d, c, _ in parsed if d and c])
    pending = [p for p in parsed if not applied.get((p[0], p[1]), False)]
    done = [p for p in parsed if applied.get((p[0], p[1]), False)]

    blocks = []
    if pending:
        head = (f"🚨 주가이벤트의심 {len(pending)}건 "
                f"— 수정계수 미반영 (확인 필요)")
        body = "\n".join("• " + p[2] for p in pending[:max_lines])
        if len(pending) > max_lines:
            body += f"\n… 외 {len(pending) - max_lines}건"
        blocks.append(head + "\n" + body)
    if done:
        # 마커 없음 — 이 블록만 있는 날은 '깨끗한 성공'으로 침묵
        head = f"· 주가이벤트 {len(done)}건 — 수정계수 반영됨 (조치 불필요)"
        body = "\n".join("  " + p[2] for p in done[:max_lines])
        if len(done) > max_lines:
            body += f"\n  … 외 {len(done) - max_lines}건"
        blocks.append(head + "\n" + body)
    return "\n\n".join(blocks)


def _extract_step_errors(text: str, max_lines: int = 8) -> str:
    """보고서 끝의 부가 단계 오류 블록 추출 (daily_update가 덧붙임).

    형식: "⚠️  부가 단계 오류 N건" 헤더 + "  • 배당: DartApiError: ..." 라인.
    헤더 문자열은 `scripts/daily_update.py`의 `STEP_ERROR_HEADER`와 짝 — 한쪽만 바꾸면
    조용히 감지가 끊긴다.
    """
    import re
    hm = re.search(r"부가 단계 오류\s*([\d,]+)\s*건", text)
    if not hm:
        return ""
    lines = re.findall(r"^\s*•\s*(\S[^\n]*)$", text[hm.end():], re.MULTILINE)
    head = f"⛔ 부가 단계 오류 {hm.group(1)}건 (수집 본체와 별개 — 하위 파이프라인 실패)"
    if not lines:
        return head
    shown = [l.strip()[:180] for l in lines[:max_lines]]
    body = "\n".join("• " + l for l in shown)
    if len(lines) > max_lines:
        body += f"\n… 외 {len(lines) - max_lines}건"
    return head + "\n" + body


def _compact_daily_update_summary(report_path: Path) -> tuple[str, bool]:
    """daily_update 보고서에서 성공용 요약 + 주가이벤트의심 + 부가 단계 오류 추출.

    반환: (요약 텍스트, 이상 감지 여부)
    """
    try:
        text = report_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"(보고서 읽기 실패: {e})", True

    # 부가 단계(배당·휴장일·수정주가 등) 오류 — daily_update가 보고서 끝에 덧붙인 블록.
    # 휴장일에도 이 단계들은 도니 skip 보고서보다 **먼저** 뽑는다.
    step_error_block = _extract_step_errors(text)

    # skip 보고서는 한 줄 (파일명에 _skip 명시된 경우만)
    if "_skip.txt" in str(report_path):
        first = text.strip().splitlines()[0] if text.strip() else ""
        line = f"건너뜀: {first[:200]}"
        if step_error_block:
            return f"{line}\n\n{step_error_block}", True
        return line, False

    # "수집 요약" 섹션 파싱. 항목별 컬럼 구조가 다름:
    #   OHLCV/수급/외인: "라벨  성공  실패  전체  신규/변경  스킵"
    #   시가총액:        "시가총액  (OHLCV와 동일)  전체  신규/변경  스킵"  (성공/실패 없음)
    import re
    bullet_lines = []
    anomaly = False

    def _n(s):
        return int(s.replace(",", ""))

    # OHLCV / 투자자별 수급 — 성공/실패 구조
    for label in ("OHLCV", "투자자별 수급"):
        m = re.search(rf"^\s*{re.escape(label)}[^\d\n]+([\d,]+)\s+([\d,]+)\s+([\d,]+)", text, re.MULTILINE)
        if not m:
            continue
        ok, fail = m.group(1), m.group(2)
        if _n(fail) > 0:
            bullet_lines.append(f"• {label}: {ok}개 (실패 {fail}개)")
            anomaly = True
        else:
            bullet_lines.append(f"• {label}: {ok}개")

    # 시가총액 — "(OHLCV와 동일)" 뒤 전체레코드 (성공/실패 컬럼 없음)
    m = re.search(r"^\s*시가총액\s+\(OHLCV와 동일\)\s+([\d,]+)", text, re.MULTILINE)
    if m:
        bullet_lines.append(f"• 시가총액: {m.group(1)}개")

    # 외국인 지분율 — 02:00 본체는 collect_foreign=False로 skip → 전부 0이면 08:30 보충 안내
    m = re.search(r"^\s*외국인 지분율\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", text, re.MULTILINE)
    if m:
        ok_fo, fail_fo, total_fo = _n(m.group(1)), _n(m.group(2)), _n(m.group(3))
        if ok_fo == 0 and total_fo == 0:
            bullet_lines.append("• 외국인 지분율: 08:30 종합 보충에서 수집")
        elif fail_fo > 0:
            bullet_lines.append(f"• 외국인 지분율: {m.group(1)}개 (실패 {m.group(2)}개)")
            anomaly = True
        else:
            bullet_lines.append(f"• 외국인 지분율: {m.group(1)}개")

    # 주가이벤트의심 항목 추출 (±30% 초과 — 수정주가 확인 필요) → summary에 포함
    event_block = _extract_price_events(text)
    if event_block:
        anomaly = True
        bullet_lines.append("")
        bullet_lines.append(event_block)

    # 부가 단계 오류 → 무조건 이상으로 승격. 수집 자체는 성공했어도 배당/휴장일 같은
    # 하위 파이프라인이 죽어 있으면 알려야 한다 (10일 무증상 사고 재발 방지).
    if step_error_block:
        anomaly = True
        bullet_lines.append("")
        bullet_lines.append(step_error_block)

    if not bullet_lines:
        return "(요약 추출 실패 — 보고서 형식 변경 의심)", True

    return "\n".join(bullet_lines), anomaly

# logs 폴더 생성 (logging 설정 전에 먼저 생성)
(project_root / "logs").mkdir(exist_ok=True)

# APScheduler 로그 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            project_root / "logs" / "scheduler.log",
            encoding="utf-8"
        ),
    ]
)
logger = logging.getLogger(__name__)


def job_daily_update():
    """매일 02:00 KST — daily_update 본체(인포맥스/DART) → 분봉 일배치(LS) 직렬.
    LENS 야간 사용 시간 확보 위해 분봉 일배치를 23:00 별도 cron에서 daily_update 끝으로 이전.
    daily_update ~3시간 → 분봉 일배치 ~50분 → 총 02:00~06:00 종료 (느린 날 최대 ~07:30).
    키B(와이프) 사용 — 08:50 이전 종료 보장 (LENS REST 09:00~15:45 키B 충돌 회피).
    """
    from scripts.daily_update import main as run_daily
    started = datetime.now(KST)
    logger.info("="*60)
    logger.info(f"[스케줄러] 일별 업데이트 시작: {started}")
    logger.info("="*60)

    # daily_update 본체 (외인 STEP skip — 08:30 종합 보충에서 수집)
    #
    # SystemExit도 반드시 잡는다: daily_update.main()은 실패 시 sys.exit(1)로 끝난다
    # (CLI 계약상 정상). SystemExit은 Exception이 아니라 BaseException 상속이라
    # `except Exception`으로는 안 잡히고, 그대로 APScheduler까지 올라가면 아래
    # notify_job에 도달하지 못해 **실패 알림이 통째로 유실된다**.
    # 2026-07-29 02:00 DNS 장애 때 실제로 무알림 실패 발생 (7/28 데이터 결손을
    # 사용자가 하루 뒤 질문으로 알게 됨).
    main_status, main_err = "ok", None
    report_path = None
    try:
        # 인포맥스(수급/지수·선물 등) 한도를 쓰는 구간 — 백필에 마커로 알림
        with infomax_busy("daily_update 본체"):
            report_path = run_daily(collect_foreign=False)
    except SystemExit as e:
        if e.code not in (0, None):
            main_status = "fail"
            main_err = f"daily_update sys.exit({e.code}) — 상세는 reports/*_ERROR.txt"
            logger.error(f"[스케줄러] daily_update 본체 실패: sys.exit({e.code})")
    except Exception as e:
        main_status, main_err = "fail", str(e)
        logger.error(f"[스케줄러] daily_update 본체 실패: {e}")

    # 본체 결과를 보고서 파일에서 추출해서 알림.
    #
    # 보고서 경로는 daily_update가 **직접 알려준다** (반환값 / 실패 시 모듈 전역).
    # 이전엔 `daily_update_{어제|오늘|그저께}.txt` 로 파일명을 추측했는데, 보고서 이름은
    # 실행일이 아니라 **마지막 거래일** 기준이라 월요일 02:00처럼 마지막 거래일이 사흘 전
    # (금)이면 후보에서 빠져 detail이 빈 채로 알림이 나갔다 (2026-08-10 실측, 매주 월요일 재현).
    # 이름 규칙에 기대는 대신 실제 경로를 받아 쓰면 요일·휴장 길이와 무관하게 맞는다.
    detail = ""
    status = main_status
    found_report = None
    found_suffix = ""
    if report_path is None:
        # 실패 경로(sys.exit)는 반환값이 없다 — 전역에서 회수. 보고서를 쓰기 전에 죽었으면 None.
        try:
            from scripts import daily_update as _daily_update_mod
            report_path = _daily_update_mod.LAST_REPORT_PATH
        except Exception as e:
            logger.warning(f"[스케줄러] 보고서 경로 회수 실패: {e}")

    if report_path is not None:
        p = Path(report_path)
        if p.exists():
            found_report = p
            # 접미사로 분기 (정식 "" / 휴장·영업일없음 "_skip" / 실패 "_ERROR")
            for suffix in ("_skip", "_ERROR"):
                if p.stem.endswith(suffix):
                    found_suffix = suffix
                    break
        else:
            logger.warning(f"[스케줄러] 보고서 경로가 가리키는 파일 없음: {p}")

    if found_report:
        if found_suffix == "_skip":
            # 휴장 / 영업일 없음 — 짧은 한 줄 + noop으로 표시
            summary, _ = _compact_daily_update_summary(found_report)
            detail = summary
            if main_status == "ok":
                status = "noop"
        elif main_status == "ok":
            # 성공 — 요약 (이상 시 주가이벤트의심 항목이 summary에 이미 포함됨)
            summary, _anomaly = _compact_daily_update_summary(found_report)
            detail = summary
        else:
            # 실패 — 전체 tail 첨부
            detail = _read_report_tail(found_report)

    if main_status == "fail" and main_err:
        detail = (detail + f"\n\n에러: {main_err}")[-1500:]

    # daily_update 끝나고 분봉 일배치 직렬 호출 (한낮 LS 부하 회피, 사용자 활동 시작 전)
    mb_started = datetime.now(KST)
    logger.info("="*60)
    logger.info(f"[스케줄러] 분봉 일배치 시작 (daily_update 후속): {mb_started}")
    logger.info("="*60)
    try:
        job_minute_bars_daily()
        # 분봉은 알림 별도로 보내지 않음 (daily_update 알림에 묶이는 흐름).
        # 실패 시에만 별도 알림.
    except Exception as e:
        logger.error(f"[스케줄러] 분봉 일배치 실패: {e}")
        notify_job("minute_bars (daily_update 후속)", "fail", mb_started, detail=f"에러: {e}")

    # 최종 알림 (본체 보고서 요약). 외인/누락 보충은 08:30 종합 보충 잡에서 별도 알림.
    notify_job("daily_update", status, started, detail=detail.strip())


def job_weekly_backup():
    """매주 일요일 03:00 실행되는 백업 작업. 알림은 실패 시에만."""
    from scripts.backup_db import run_backup, cleanup_old_backups
    started = datetime.now(KST)
    logger.info("="*60)
    logger.info(f"[스케줄러] 주간 백업 시작: {started}")
    logger.info("="*60)
    try:
        backup_file = run_backup()
        cleanup_old_backups()
        logger.info(f"[스케줄러] 백업 완료: {backup_file.name}")
        # 성공 시 알림 안 보냄 (사용자 정책)
    except Exception as e:
        logger.error(f"[스케줄러] 백업 실패: {e}")
        notify_job("weekly_backup", "fail", started, detail=f"에러: {e}")


def job_minute_bars_daily():
    """daily_update 후속 — 분봉 일배치 (종목/ETF + 지수선물).
    LS 백필 진행 중이면 SIGSTOP → 일배치 → SIGCONT (사용자 정책).
    당일만 가능한 두 종류는 별도 cron:
      주식선물(t8406) → job_stockfut_today 23:30
      지수(t8418)     → job_index_minute_bars_today 15:40 (직전 1세션만 제공)"""
    from datetime import timedelta as _td
    from scripts.daily_update import (
        run_minute_bars_pipeline,
        run_futures_minute_bars_pipeline,
        export_futures_master_json,
        _ls_backfill_pause, _ls_backfill_resume,
        get_conn, last_business_day_on_or_before)
    yesterday = datetime.now(KST).date() - _td(days=1)
    conn = get_conn()
    try:
        target = last_business_day_on_or_before(conn, yesterday)
    finally:
        conn.close()
    logger.info("="*60)
    logger.info(f"[스케줄러] 분봉 일배치 시작: {datetime.now(KST)} target_date={target}")
    logger.info("="*60)

    # outer pause: 모든 파이프라인 동안 백필 STOP
    paused = _ls_backfill_pause()
    try:
        # 지수(t8418)는 여기서 빠졌다 — job_index_minute_bars_today(15:40)로 이관.
        # t8418이 직전 1세션만 주므로 다음날 새벽 호출은 **항상 빈 응답**이고,
        # 갭이 누적돼 매일 지나간 날짜를 헛되이 재요청했다(empty 2→4→6→8…).
        for fn, label in [
            (run_minute_bars_pipeline,         "종목/ETF"),
            (run_futures_minute_bars_pipeline, "지수선물"),
        ]:
            try:
                result = fn(target)
                logger.info(f"[스케줄러] {label} 분봉 일배치 완료: {result}")
                # 30초 완결성: 재시도 후에도 남은 genuine 결측이면 알림 (7/20 사고 재발 감지)
                if label == "종목/ETF" and isinstance(result, dict) and result.get("still_missing", 0) > 0:
                    comp = result.get("completeness", {})
                    codes_str = ", ".join(comp.get("still_missing_codes", [])[:20])
                    notify_job(
                        "30초 완결성", "fail", datetime.now(KST),
                        detail=(f"target={target} — daily는 있는데 30초봉 결측 "
                                f"{result['still_missing']}종목 (재시도 후 잔여). "
                                f"복구 {comp.get('recovered', 0)} / lag {comp.get('lag', 0)}\n"
                                f"결측: {codes_str}"))
            except Exception as e:
                logger.error(f"[스케줄러] {label} 분봉 일배치 실패: {e}")

        # LS-using export — 같은 LS 가드 안에서 호출 (5xx 충돌 회피)
        try:
            export_futures_master_json()
            logger.info("[스케줄러] futures_master.json export 완료")
        except Exception as fm_err:
            logger.error(f"[스케줄러] futures_master.json export 실패: {fm_err}")
    finally:
        _ls_backfill_resume(paused)

    # 봉수 완결성 — 세 소스(종목/지수/선물)를 target 하루치로 한 번에 검사.
    # 주식선물은 전날 23:30에 이미 적재되므로 이 시점이면 네 경로가 모두 관측 가능하다.
    # "존재하지만 잘려 있음"은 기존 완결성 체크로 원리적으로 안 보여서
    # 지수/지수선물이 8개월간 무증상이었다 (tr_cont 페이징 사고, 2026-08-30).
    try:
        from scripts.daily_update import (check_intraday_bar_counts,
                                          check_intraday_close_match, get_conn)
        conn = get_conn()
        try:
            bc = check_intraday_bar_counts(conn, target)
            # 봉수 체크는 "몇 개 왔나"만 본다. 개수는 맞고 **내용이 다른** 사고
            # (2026-09-01 301 오배정 — 761봉 정상인데 코스닥 종합이었음)는
            # 종가 대조로만 잡힌다. 두 체크가 서로 다른 실패 모드를 담당한다.
            cm = check_intraday_close_match(conn, target)
        finally:
            conn.close()
        logger.info(f"[스케줄러] 봉수 완결성: issue={bc['issue_count']} "
                    f"genuine={bc['genuine_sources']} short_codes={bc['short_code_total']} "
                    f"/ 종가 대조: issue={cm['issue_count']}")
        if bc["issue_count"] > 0 or cm["issue_count"] > 0:
            lines = [f"target={target}", "[봉수]"]
            for name, s in bc["sources"].items():
                if not s.get("present"):
                    lines.append(f"• {name}: 데이터 없음")
                    continue
                lines.append(f"• {name}: {s['codes']}코드 / median {s['median']:.0f}봉 "
                             f"(기대 {s['expected']}, {s['ratio']*100:.0f}%)")
            if bc["genuine_sources"]:
                lines.append(f"⚠️ 소스 통째 절단 의심: {', '.join(bc['genuine_sources'])}")
            if bc["short_code_total"]:
                sample = ", ".join(f"{c['code']}({c['bars']}봉)"
                                   for c in bc["short_codes"][:10])
                lines.append(f"⚠️ 개별 코드 미달 {bc['short_code_total']}건: {sample}")

            lines.append("")
            lines.append("[종가 대조]")
            for name, s in cm["sources"].items():
                if not s.get("present"):
                    lines.append(f"• {name}: 데이터 없음")
                    continue
                lines.append(f"• {name}: {s['checked']}건 중 불일치 {s['mismatched']} "
                             f"(허용 {s['tol_pct']:.1f}%)")
                for w in s["worst"][:5]:
                    lines.append(f"  ⚠️ {w['code']} 분봉 {w['intraday']:,} vs "
                                 f"일별 {w['daily']:,} ({w['diff_pct']:+.2f}%)")
            notify_job("30초봉 무결성", "ok", datetime.now(KST),
                       detail="\n".join(lines), warn=True)
    except Exception as e:
        logger.error(f"[스케줄러] 30초봉 무결성 체크 실패: {e}")


INDEX_BARS_EXPECTED = 761      # 09:00:30~15:30 30초봉 (페이징 완주 실측)
INDEX_BARS_MIN = 700           # 이 아래면 미완성으로 보고 재시도
INDEX_RETRY_DELAYS = (600, 900, 1800)   # 15:50 / 16:05 / 16:35 무렵


def job_index_minute_bars_today():
    """평일 15:40 KST — 지수(KOSPI200/KOSDAQ150) 30초봉 **당일** 수집.

    왜 당일 마감 직후인가:
      t8418은 직전 1세션만 제공한다(2026-08-30 실측 — 8/27·8/26 요청 0봉,
      nday/sdate 변형도 무효). 기존엔 다음날 새벽 배치가 '어제'를 요청해서
      평일엔 늘 빈 응답이었고, 토요일 실행분만 타깃(금요일)과 API 보유 세션이
      맞아떨어져 **금요일치만** 쌓였다(7/6 이후 영업일 30일 누락).
      장 마감(15:30) 직후가 유일하게 데이터가 오는 타이밍이다.

    미완성 대비 재시도: 마감 직후엔 아직 봉이 덜 찼을 수 있어
    INDEX_BARS_MIN 미만이면 지연을 두고 재요청한다. UPSERT라 재호출은 안전.
    """
    import time as _time
    from datetime import timedelta as _td
    from scripts.daily_update import (run_index_minute_bars_pipeline, get_conn,
                                      is_market_closed, _ls_backfill_pause,
                                      _ls_backfill_resume)
    started = datetime.now(KST)
    today = started.date()

    conn = get_conn()
    try:
        if is_market_closed(conn, today):
            logger.info(f"[스케줄러] 지수 30초봉 — {today} 휴장일, skip")
            return
    finally:
        conn.close()

    logger.info("=" * 60)
    logger.info(f"[스케줄러] 지수 30초봉 당일 수집 시작: {started} (target={today})")
    logger.info("=" * 60)

    def _bars_today() -> dict:
        c = get_conn()
        try:
            with c.cursor() as cur:
                lo = datetime(today.year, today.month, today.day, tzinfo=KST)
                cur.execute("""
                    SELECT index_code, count(*) FROM index_ohlcv_intraday
                    WHERE interval_seconds = 30 AND time >= %s AND time < %s
                    GROUP BY 1
                """, (lo, lo + _td(days=1)))
                return {r[0]: r[1] for r in cur.fetchall()}
        finally:
            c.close()

    # 기대 코드 수는 파이프라인 스코프에서 가져온다 (하드코딩 2 → 405 추가 시 어긋남)
    from scripts.daily_update import INDEX_INTRADAY_CODES
    want_codes = set(INDEX_INTRADAY_CODES)

    attempts, result = 0, {}
    paused = _ls_backfill_pause()
    try:
        for delay in (0,) + INDEX_RETRY_DELAYS:
            if delay:
                logger.info(f"[스케줄러] 지수 30초봉 미완성 — {delay}s 후 재시도")
                _time.sleep(delay)
            attempts += 1
            try:
                result = run_index_minute_bars_pipeline(today, only_days=[today])
            except Exception as e:
                logger.error(f"[스케줄러] 지수 30초봉 수집 실패({attempts}회): {e}")
                result = {"error": str(e)}
            counts = _bars_today()
            logger.info(f"[스케줄러] 지수 30초봉 {attempts}회차: {result} / 봉수={counts}")
            if want_codes <= set(counts) and min(counts.values()) >= INDEX_BARS_MIN:
                break
    finally:
        _ls_backfill_resume(paused)

    counts = _bars_today()
    ok = bool(counts) and want_codes <= set(counts) and min(counts.values()) >= INDEX_BARS_MIN
    lines = [f"target={today} / 시도 {attempts}회"]
    if counts:
        lines += [f"• {c}: {n:,}봉 (기대 {INDEX_BARS_EXPECTED})" for c, n in sorted(counts.items())]
    else:
        lines.append("• 적재 0")
    missing = sorted(want_codes - set(counts))
    if missing:
        lines.append(f"• 미적재 코드: {', '.join(missing)}")

    # 성공도 반드시 기록한다 — 이 잡은 실패 시에만 notify_job 을 불러서
    # 성공하면 job_events 에 아무 흔적이 없었고(2026-08-31 첫 실행), 일일 요약에도
    # 안 떠서 "정상"인지 "잡이 아예 안 떴는지" 구분할 수 없었다.
    # 지수는 t8418 특성상 그날 못 받으면 영구 손실이라 침묵이 두 뜻을 가지면 위험하다.
    # notify_job 3단계 정책상 깨끗한 성공은 기록만 되고 전송은 안 된다.
    detail = "\n".join(lines)
    if ok:
        notify_job("지수 30초봉", "ok", started, detail=detail)
        logger.info(f"[스케줄러] 지수 30초봉 완료: {counts}")
    else:
        notify_job("지수 30초봉", "fail", started,
                   detail=detail + "\n\n⚠️ 당일 확보 실패 — t8418은 과거를 주지 않아 "
                                   "이 날짜는 소급 불가")


def _purge_stockfut_day(day) -> int:
    """LS 휴장일 fallback으로 오염된 하루치 30초봉을 삭제하고 삭제 행수 반환.

    fallback 데이터는 직전 영업일의 완전 복제라 남겨두면
    (a) 휴장일에 가짜 시세가 존재하고
    (b) 다음 영업일 검증의 prev_biz 기준이 오염된 날을 가리키게 된다.
    감지만 하고 두면 안 되므로 즉시 되돌린다 (2026-07-17 제헌절 사례).
    """
    from scripts.daily_update import get_conn
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM futures_ohlcv_intraday
                WHERE interval_seconds = 30
                  AND time >= %s::date::timestamp AT TIME ZONE 'Asia/Seoul'
                  AND time <  (%s::date + 1)::timestamp AT TIME ZONE 'Asia/Seoul'
            """, (day, day))
            deleted = cur.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def _verify_stockfut_loaded(today, result) -> tuple[bool, str, str]:
    """주식선물 당일 적재 검증. (ok, 메시지, reason) 반환.

    1) actives 중 95% 이상의 contract가 DB에 30초봉 row를 가지는가
    2) 직전 영업일 데이터와 100% 동일하지 않은가 (LS t8406 휴장일 fallback 감지)
       — LS는 휴장일 query 시 직전 영업일 데이터를 반환하는 동작이 있음 (5/25 사례).

    reason: ok / skipped / no_actives / insufficient / fallback_duplicate / error
    호출부는 reason으로 "재시도해도 소용없는 실패"(fallback_duplicate)를 구분한다.
    """
    if not result or result.get("skipped"):
        return False, f"skip={result}", "skipped"
    actives = result.get("actives", 0)
    if actives == 0:
        return False, "actives=0", "no_actives"
    try:
        from scripts.daily_update import get_conn
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                # 1) 적재된 distinct code 수 (KST date 기준)
                cur.execute(
                    "SELECT COUNT(DISTINCT futures_code) FROM futures_ohlcv_intraday "
                    "WHERE (time AT TIME ZONE 'Asia/Seoul')::date = %s AND interval_seconds = 30",
                    (today,))
                loaded = cur.fetchone()[0]

                # 2) 직전 영업일 찾기 (futures_ohlcv_intraday에 데이터 있는 날)
                cur.execute(
                    "SELECT MAX((time AT TIME ZONE 'Asia/Seoul')::date) "
                    "FROM futures_ohlcv_intraday "
                    "WHERE (time AT TIME ZONE 'Asia/Seoul')::date < %s AND interval_seconds = 30",
                    (today,))
                prev_biz = cur.fetchone()[0]

                # 3) 직전 영업일과 close/volume 비교
                duplicate_of_prev = False
                if prev_biz is not None:
                    cur.execute("""
                        SELECT COUNT(*) FROM (
                            SELECT t.futures_code,
                                   (t.time AT TIME ZONE 'Asia/Seoul')::time AS tt,
                                   t.close, t.volume
                            FROM futures_ohlcv_intraday t
                            WHERE (t.time AT TIME ZONE 'Asia/Seoul')::date = %s
                              AND t.interval_seconds = 30
                            EXCEPT
                            SELECT p.futures_code,
                                   (p.time AT TIME ZONE 'Asia/Seoul')::time AS tt,
                                   p.close, p.volume
                            FROM futures_ohlcv_intraday p
                            WHERE (p.time AT TIME ZONE 'Asia/Seoul')::date = %s
                              AND p.interval_seconds = 30
                        ) diff
                    """, (today, prev_biz))
                    diff_count = cur.fetchone()[0]
                    duplicate_of_prev = (diff_count == 0)
        finally:
            conn.close()

        threshold = max(1, int(actives * 0.95))
        if loaded < threshold:
            return False, f"actives={actives}, loaded={loaded}, threshold={threshold}", "insufficient"
        if duplicate_of_prev:
            return False, (f"actives={actives}, loaded={loaded}, 직전 영업일({prev_biz})과 "
                           f"100% 동일 — LS 휴장일 fallback 의심"), "fallback_duplicate"
        return (True,
                f"actives={actives}, loaded={loaded}, threshold={threshold}, prev_biz={prev_biz} OK",
                "ok")
    except Exception as e:
        return False, f"verify error: {e}", "error"


def job_stockfut_today():
    """매일 23:30 KST (평일) — 주식선물 30초봉 당일 적재 (LS t8406).
    historical 불가능 → 매일 받지 않으면 영구 손실.
    휴장일이면 skip (LS t8406이 휴장일에 직전 영업일 데이터를 반환하는 동작 회피).
    실행 후 검증 → 누락 시 5분 간격 최대 3회 재시도 (UPSERT라 안전)."""
    from scripts.daily_update import (run_stockfut_minute_today_pipeline,
                                       _ls_backfill_pause, _ls_backfill_resume,
                                       is_market_closed, get_conn)
    import time as _time
    MAX_ATTEMPTS = 3
    RETRY_WAIT_SEC = 300  # 5분

    started = datetime.now(KST)
    today = started.date()
    logger.info("="*60)
    logger.info(f"[스케줄러] 주식선물 당일 30초봉 시작: {started}")
    logger.info("="*60)

    # 휴장일 체크 — krx_holidays 테이블 기반 (mon-fri cron이라 주말은 안 들어옴)
    conn = get_conn()
    holiday_reason = None
    try:
        closed = is_market_closed(conn, today)
        if closed:
            with conn.cursor() as cur:
                cur.execute("SELECT reason FROM krx_holidays WHERE date = %s", (today,))
                r = cur.fetchone()
                if r:
                    holiday_reason = r[0]
    finally:
        conn.close()
    if closed:
        reason_str = f" ({holiday_reason})" if holiday_reason else ""
        logger.info(f"[스케줄러] {today} 휴장일 — stockfut skip (LS 휴장일 fallback 회피)")
        notify_job("stockfut_today", "noop", started,
                   detail=f"{today} 휴장일{reason_str}")
        return

    paused = _ls_backfill_pause()
    last_result = None
    last_err = None
    verify_msg = ""
    reason = ""
    purged = None
    ok = False
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            logger.info(f"[스케줄러] stockfut attempt {attempt}/{MAX_ATTEMPTS}")
            try:
                last_result = run_stockfut_minute_today_pipeline(today)
                last_err = None
            except Exception as e:
                last_err = e
                last_result = None
                logger.error(f"[스케줄러] stockfut attempt {attempt} 예외: {e}")

            ok, verify_msg, reason = _verify_stockfut_loaded(today, last_result)
            logger.info(f"[스케줄러] stockfut 검증: ok={ok}, reason={reason}, {verify_msg}")
            if ok:
                break

            # LS 휴장일 fallback = 직전 영업일 복제본이 적재된 상태.
            # 재시도해도 같은 복제본만 다시 받으므로 즉시 되돌리고 중단한다.
            if reason == "fallback_duplicate":
                try:
                    purged = _purge_stockfut_day(today)
                    logger.warning(f"[스케줄러] stockfut fallback 감지 — {today} 30초봉 "
                                   f"{purged:,}행 삭제 후 중단 (재시도 무의미)")
                except Exception as e:
                    purged = -1
                    logger.error(f"[스케줄러] stockfut fallback 행 삭제 실패: {e}")
                break

            if attempt < MAX_ATTEMPTS:
                logger.warning(f"[스케줄러] stockfut 검증 실패 — {RETRY_WAIT_SEC}s 후 재시도")
                _time.sleep(RETRY_WAIT_SEC)
    finally:
        _ls_backfill_resume(paused)

    if ok:
        status = "ok"
        # 성공 — bullet 요약
        actives = last_result.get("actives", 0) if last_result else 0
        rows = last_result.get("rows", 0) if last_result else 0
        empty = last_result.get("empty", 0) if last_result else 0
        errors = last_result.get("errors", 0) if last_result else 0
        detail = (f"• 활성 계약: {actives}개\n"
                  f"• 적재 행: {rows:,}행\n"
                  f"• 빈 응답: {empty}개\n"
                  f"• 에러: {errors}개")
    elif reason == "fallback_duplicate":
        status = "fail"
        purge_str = (f"{purged:,}행 삭제 완료" if purged is not None and purged >= 0
                     else "⛔ 삭제 실패 — 수동 확인 필요")
        detail = (f"날짜: {today}\n"
                  f"LS t8406이 직전 영업일 데이터를 그대로 반환 (휴장일 fallback)\n\n"
                  f"⚠️ 검증 결과\n  {verify_msg}\n\n"
                  f"조치\n"
                  f"  • 오염 30초봉: {purge_str}\n"
                  f"  • 재시도 중단 (같은 복제본만 재수신)\n\n"
                  f"{today}가 휴장일이면 정상 동작 (krx_holidays 미등록 상태).\n"
                  f"거래일이었다면 30초봉 영구 손실 — LS 응답 확인 필요.")
    else:
        status = "fail"
        # 실패 — 자세히 + ⚠️ 강조
        detail = (f"날짜: {today}\n"
                  f"시도: {MAX_ATTEMPTS}회 모두 검증 실패\n\n"
                  f"⚠️ 검증 결과\n  {verify_msg}")
        if last_err:
            detail += f"\n\n마지막 에러: {last_err}"
        elif last_result:
            detail += (f"\n\n마지막 결과\n"
                       f"  • 활성 계약: {last_result.get('actives', 0)}개\n"
                       f"  • 적재 행: {last_result.get('rows', 0):,}행\n"
                       f"  • 빈 응답: {last_result.get('empty', 0)}개\n"
                       f"  • 에러: {last_result.get('errors', 0)}개")
    notify_job("stockfut_today", status, started, detail=detail)


def job_etf_snapshot():
    """매일 08:30 KST — 아침 종합 보충.
    1) ETF PDF/마스터 스냅샷 (today + yesterday 2-pass) — 02:00엔 인포맥스 ingest 미완
    2) 종합 누락 보충 (OHLCV/수급/외인) — 외인은 익일 05:30 이후라 08:30에 수집
    두 작업 모두 인포맥스 의존 + 늦은 시각 필요라 같은 잡에 통합 (잡 개수 최소화)."""
    from scripts.etf_snapshot import main as etf_main
    from scripts.daily_update import run_supplement_pipeline
    started = datetime.now(KST)
    logger.info("="*60)
    logger.info(f"[스케줄러] 아침 종합 보충 시작 (ETF + 누락 보충): {started}")
    logger.info("="*60)

    status = "ok"
    parts = []

    # 이 잡 전체가 인포맥스 한도를 쓴다 → 마커로 외부 프로세스(백필)에 알림.
    # in-process라 pgrep으로는 안 보이므로 파일 마커가 유일한 신호다.
    with infomax_busy("아침 종합 보충 (ETF + 누락 보충)"):
        # 1) ETF 스냅샷
        try:
            etf_main()
            logger.info(f"[스케줄러] ETF 스냅샷 완료: {datetime.now(KST)}")
            parts.append("[ETF]\n" + _etf_snapshot_summary(started.date()))
        except Exception as e:
            logger.error(f"[스케줄러] ETF 스냅샷 실패: {e}")
            status = "fail"
            parts.append(f"[ETF] 에러: {e}")

        # 2) 종합 누락 보충 (OHLCV/수급/외인)
        try:
            sup = run_supplement_pipeline()
            logger.info(f"[스케줄러] 종합 누락 보충 완료: {sup}")
            if sup.get("target_days", 0) == 0:
                parts.append("[누락 보충] 없음 (최신)")
            else:
                line = (f"[누락 보충] {sup['days_processed']}일\n"
                        f"• OHLCV {sup['ohlcv']:,}개\n"
                        f"• 수급 {sup['investor']:,}개\n"
                        f"• 외인 {sup['foreign']:,}개")
                # 빈 응답으로 못 채운 종목이 있으면 반드시 노출.
                # 적재 행수만 보면 "부분 수집인데 완료"로 읽힌다
                # (2026-07-30: 7/29 외인 1,230종목 누락인데 'foreign: 4060'만 통보됨).
                # 단 상장 전 날짜(lag)는 데이터가 존재할 수 없어 ⚠️ 대상이 아니다
                # — 신규 상장 때마다 뜨던 가짜 경고 제거 (2026-08-14).
                f_fail = sup.get("foreign_fail", 0)
                if f_fail:
                    days = ", ".join(f"{x['date']}:{x['fail']:,}"
                                     for x in sup.get("foreign_fail_days", []))
                    line += (f"\n⚠️ 외인 빈응답 {f_fail:,}종목 ({days})\n"
                             f"   인포맥스 등록 지연 가능 — 다음 보충에서 재시도됨")
                f_lag = sup.get("foreign_lag", 0)
                if f_lag:
                    days = ", ".join(f"{x['date']}:{x['fail']:,}"
                                     for x in sup.get("foreign_lag_days", []))
                    line += (f"\n· 외인 미상장 구간 {f_lag:,}종목 ({days}) — "
                             f"상장 전 날짜, 정상")
                parts.append(line)
        except Exception as e:
            logger.error(f"[스케줄러] 종합 누락 보충 실패: {e}")
            status = "fail"
            parts.append(f"[누락 보충] 에러: {e}")

    notify_job("아침 종합 보충", status, started, detail="\n\n".join(parts))


def _etf_snapshot_summary(today_date) -> str:
    """오늘+직전 영업일의 ETF PDF / 마스터 적재 row 수 조회."""
    try:
        from scripts.daily_update import get_conn
        import datetime as _dt
        # 오늘 + 직전 영업일(가장 최근 weekday)
        yest = today_date - _dt.timedelta(days=1)
        while yest.weekday() >= 5:
            yest -= _dt.timedelta(days=1)
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT snapshot_date, COUNT(DISTINCT etf_code) AS etfs, COUNT(*) AS rows "
                    "FROM etf_portfolio_daily WHERE snapshot_date IN (%s, %s) "
                    "GROUP BY snapshot_date ORDER BY snapshot_date",
                    (yest, today_date))
                pdf_rows = cur.fetchall()
                cur.execute(
                    "SELECT snapshot_date, COUNT(*) FROM etf_master_daily "
                    "WHERE snapshot_date IN (%s, %s) GROUP BY snapshot_date ORDER BY snapshot_date",
                    (yest, today_date))
                master_rows = dict(cur.fetchall())
                # 완결성 체크 결과 (Q4-1) — 날짜별 최신 master_completeness 1건
                cur.execute(
                    "SELECT DISTINCT ON (check_date) check_date, issue_count, details "
                    "FROM data_quality_checks "
                    "WHERE table_name='etf_master_daily' AND check_type='master_completeness' "
                    "  AND check_date IN (%s, %s) "
                    "ORDER BY check_date, created_at DESC",
                    (yest, today_date))
                completeness = {d: (cnt, det) for d, cnt, det in cur.fetchall()}
        finally:
            conn.close()
        sections = []
        for d, etfs, rows in pdf_rows:
            m = master_rows.get(d, 0)
            line = (f"📅 {d}\n"
                    f"  • PDF: {etfs}개 ETF / {rows:,}행\n"
                    f"  • 마스터: {m}개")
            cnt, det = completeness.get(d, (None, None))
            if cnt:  # genuine 누락 있을 때만 노출 (lag 는 제외 — 정상 부재)
                codes = ", ".join(f"{g['code']}({g['name']})" for g in (det or {}).get("genuine", []))
                line += f"\n  • ⚠️ 마스터 누락 {cnt}건: {codes}"
            sections.append(line)
        return "\n\n".join(sections) if sections else "적재 결과 없음"
    except Exception as e:
        return f"요약 조회 실패: {e}"


def job_quarterly_financials():
    """분기 재무지표 수집 (FnGuide XML) — 각 분기 제출 마감 직후 일요일 04:00
    타이밍: 6/1~7 (Q1 5/31 마감), 9/1~7 (H1 8/31), 12/1~7 (Q3 11/30), 4/1~7 (연간 3/31)
    """
    from scripts.collect_financials import run as run_financials
    started = datetime.now(KST)
    logger.info("=" * 60)
    logger.info(f"[스케줄러] 분기 재무지표 수집 시작: {started}")
    logger.info("=" * 60)
    status, detail = "ok", ""
    try:
        run_financials(delay=0.3)
        logger.info("[스케줄러] 분기 재무지표 수집 완료")
    except Exception as e:
        logger.error(f"[스케줄러] 분기 재무지표 수집 실패: {e}")
        status, detail = "fail", f"에러: {e}"
    notify_job("quarterly_financials", status, started, detail=detail)


def job_quarterly_sector():
    """분기 첫 번째 일요일 03:30 실행되는 FICS 업종 크롤링"""
    from scripts.crawl_sector import main as run_crawl
    started = datetime.now(KST)
    logger.info("="*60)
    logger.info(f"[스케줄러] 분기별 FICS 업종 크롤링 시작: {started}")
    logger.info("="*60)
    status, detail = "ok", ""
    try:
        run_crawl(missing_only=False)
        logger.info("[스케줄러] FICS 업종 크롤링 완료")
    except Exception as e:
        logger.error(f"[스케줄러] FICS 업종 크롤링 실패: {e}")
        status, detail = "fail", f"에러: {e}"
    notify_job("quarterly_sector", status, started, detail=detail)


def job_update_listed_shares():
    """매주 일요일 03:30 KST — LS t1102로 전종목 상장주식수 갱신 → floating_shares 테이블
    daily_update의 market_cap 계산 (close × total_shares) 데이터 소스."""
    from scripts.update_listed_shares import main as run_update_shares
    started = datetime.now(KST)
    logger.info("="*60)
    logger.info(f"[스케줄러] 상장주식수 갱신 시작: {started}")
    logger.info("="*60)
    status, detail = "ok", ""
    try:
        run_update_shares()
        logger.info("[스케줄러] 상장주식수 갱신 완료")
        # 갱신 row 수 조회 — created_at 기준 (방금 INSERT/UPDATE된 row)
        try:
            from scripts.daily_update import get_conn
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM floating_shares "
                    "WHERE created_at >= NOW() - INTERVAL '6 hours'")
                n = cur.fetchone()[0]
                cur.execute("SELECT MAX(base_date) FROM floating_shares")
                base_date = cur.fetchone()[0]
            conn.close()
            detail = (f"• 갱신: {n:,}개\n"
                      f"• 기준일: {base_date}")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[스케줄러] 상장주식수 갱신 실패: {e}")
        status, detail = "fail", f"에러: {e}"
    notify_job("update_listed_shares", status, started, detail=detail)


def on_job_executed(event):
    logger.info(f"[스케줄러] 작업 완료: {event.job_id} "
                f"(실행시각: {event.scheduled_run_time})")


def job_daily_digest():
    """매일 11:30 KST — 최근 24h 잡 결과를 한 통으로 요약 전송.

    알림 정책(3단계)상 깨끗한 성공은 즉시 알림을 보내지 않는다. 이 요약이
    유일한 생존 신호이므로 이상이 없어도 매일 보낸다 — 요약이 안 오는 것
    자체가 이상 신호가 되게 하려는 의도."""
    from schedulers.notifier import send_daily_digest
    logger.info("[스케줄러] 일일 요약 전송")
    try:
        ok = send_daily_digest(hours=24)
        logger.info(f"[스케줄러] 일일 요약 전송 결과: {ok}")
    except Exception as e:
        logger.error(f"[스케줄러] 일일 요약 실패: {e}")


def on_job_error(event):
    """잡에서 처리되지 않은 예외가 올라온 경우 — 최후 안전망.

    각 job 함수는 자체적으로 예외를 잡아 notify_job을 호출하지만, 그 그물을
    빠져나가는 경우(BaseException 계열, 알림 코드 자체의 버그 등)엔 여기까지 온다.
    로그만 남기면 **조용한 실패**가 되므로 반드시 알림을 보낸다.
    (2026-07-29 daily_update SystemExit 무알림 실패 재발 방지)"""
    logger.error(f"[스케줄러] 작업 오류: {event.job_id} → {event.exception}")
    try:
        started = getattr(event, "scheduled_run_time", None) or datetime.now(KST)
        tb = (getattr(event, "traceback", "") or "")[-1200:]
        notify_job(f"{event.job_id} (미처리 예외)", "fail", started,
                   detail=(f"⚠️ 잡 내부 핸들러를 빠져나온 예외 — 데이터 결손 가능\n\n"
                           f"{type(event.exception).__name__}: {event.exception}\n\n{tb}"))
    except Exception as notify_err:
        logger.error(f"[스케줄러] 작업 오류 알림 실패: {notify_err}")


def startup_catchup():
    """scheduler 시작 시 누락된 주간 잡 즉시 보충.
    APScheduler misfire_grace_time이 시작 시점에 항상 잡아주진 않으므로 명시적 catch-up.

    대상:
    - weekly_backup: 최신 백업 파일 mtime > 8일 → 즉시 실행
    - update_listed_shares: floating_shares max(updated_at) > 8일 → 즉시 실행
    - daily_update: 자체 갭 backfill 로직 있어 제외
    - etf_snapshot: today+yesterday 2-pass로 자체 회수 → 제외
    - stockfut_today: historical 불가 → catch-up 불가
    - quarterly_*: 분기 빈도, 우선순위 낮음 → 제외
    """
    import threading
    now = datetime.now(KST)

    # weekly_backup 체크
    try:
        backup_dir = project_root / "backups"
        dumps = sorted(backup_dir.glob("backup_*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not dumps:
            stale_days = float("inf")
        else:
            stale_days = (now.timestamp() - dumps[0].stat().st_mtime) / 86400
        if stale_days > 8:
            logger.warning(f"[catch-up] 최신 백업 {stale_days:.1f}일 경과 — weekly_backup 보충 실행")
            threading.Thread(target=job_weekly_backup, daemon=True, name="catchup-backup").start()
        else:
            logger.info(f"[catch-up] 최신 백업 {stale_days:.1f}일 — 정상 (8일 이내)")
    except Exception as e:
        logger.error(f"[catch-up] weekly_backup 체크 실패: {e}")

    # update_listed_shares 체크 — floating_shares.base_date 기준
    try:
        from scripts.daily_update import get_conn
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(base_date) FROM floating_shares")
                last_base_date = cur.fetchone()[0]
        finally:
            conn.close()
        if last_base_date is None:
            stale_days = float("inf")
        else:
            stale_days = (now.date() - last_base_date).days
        if stale_days > 8:
            logger.warning(f"[catch-up] 상장주식수 {stale_days:.1f}일 경과 — update_listed_shares 보충 실행")
            threading.Thread(target=job_update_listed_shares, daemon=True, name="catchup-shares").start()
        else:
            logger.info(f"[catch-up] 상장주식수 {stale_days:.1f}일 — 정상 (8일 이내)")
    except Exception as e:
        logger.error(f"[catch-up] update_listed_shares 체크 실패: {e}")


def main():
    scheduler = BlockingScheduler(timezone=KST)

    # 이벤트 리스너
    scheduler.add_listener(on_job_executed, EVENT_JOB_EXECUTED)
    scheduler.add_listener(on_job_error,    EVENT_JOB_ERROR)

    # graceful shutdown — SIGTERM/SIGINT 수신 시 진행 중 job 끝날 때까지 대기 후 종료.
    # tmux send-keys C-c 또는 kill로 재시작해도 자식 daily_update 안 죽임.
    # (5/15 사고 재발 방지 — 그 때 5/14 일별 후속 단계 silent kill 됐었음)
    def _graceful_shutdown(signum, _frame):
        logger.warning(f"[스케줄러] 신호 {signum} 수신 → 진행 중 job 완료 대기 후 종료")
        scheduler.shutdown(wait=True)
        sys.exit(0)
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT,  _graceful_shutdown)

    # 잡 1: 매일 02:00 KST — 데이터 수집 + 배당 + LENS export
    scheduler.add_job(
        job_daily_update,
        trigger=CronTrigger(
            hour=2,
            minute=0,
            timezone=KST,
        ),
        id="daily_update",
        name="일별 데이터 수집 (OHLCV/수급/외인 + 배당 + LENS export)",
        misfire_grace_time=3600,   # 1시간 내 놓쳐도 재실행
        coalesce=True,             # 누적 실행 방지
        max_instances=1,
    )

    # 잡 2: 매주 일요일 03:00 KST — DB 백업
    scheduler.add_job(
        job_weekly_backup,
        trigger=CronTrigger(
            day_of_week="sun",
            hour=3,
            minute=0,
            timezone=KST,
        ),
        id="weekly_backup",
        name="주간 DB 백업",
        misfire_grace_time=7200,   # 2시간 내 놓쳐도 재실행
        coalesce=True,
        max_instances=1,
    )

    # 분봉 일배치 cron 제거 — daily_update 끝(job_daily_update 안)에 직렬 호출로 통합 (LENS 야간 사용 시간 확보)

    # 잡 6: 매일 08:30 KST — ETF PDF/마스터 스냅샷 (daily_update에서 분리, ingest 보장)
    scheduler.add_job(
        job_etf_snapshot,
        trigger=CronTrigger(hour=8, minute=30, timezone=KST),
        id="etf_snapshot",
        name="ETF PDF/마스터 스냅샷 (today + yesterday)",
        misfire_grace_time=1800,
        coalesce=True,
        max_instances=1,
    )

    # 잡 5-1: 평일 15:40 KST — 지수 30초봉 당일 적재 (t8418은 직전 1세션만 제공)
    # 마감 15:30 직후가 유일한 수집 창. 미완성이면 잡 안에서 최대 3회 재시도(~16:35).
    scheduler.add_job(
        job_index_minute_bars_today,
        trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=40, timezone=KST),
        id="index_minute_bars_today",
        name="지수 30초봉 당일 (LS t8418, 과거 조회 불가)",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    # 잡 5: 매일 23:30 KST (월~금) — 주식선물 30초봉 당일 적재
    # 작업 시간 약 10분 → 23:30~23:40. 사용자 LENS는 23:40 ~ 다음날 04:30 자유.
    scheduler.add_job(
        job_stockfut_today,
        trigger=CronTrigger(day_of_week="mon-fri", hour=23, minute=30, timezone=KST),
        id="stockfut_today",
        name="주식선물 30초봉 당일 (LS t8406, historical 불가)",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    # 잡 4: 분기 재무지표 수집 — 각 분기 마감 후 첫 번째 일요일 04:00 (4/6/9/12월)
    scheduler.add_job(
        job_quarterly_financials,
        trigger=CronTrigger(
            month="4,6,9,12",
            day="1-7",
            day_of_week="sun",
            hour=4,
            minute=0,
            timezone=KST,
        ),
        id="quarterly_financials",
        name="분기 재무지표 수집 (FnGuide)",
        misfire_grace_time=7200,
        coalesce=True,
        max_instances=1,
    )

    # 잡 5: 분기 첫 번째 일요일 03:30 KST (1/4/7/10월) — FICS 업종 크롤링
    scheduler.add_job(
        job_quarterly_sector,
        trigger=CronTrigger(
            month="1,4,7,10",
            day="1-7",
            day_of_week="sun",
            hour=3,
            minute=30,
            timezone=KST,
        ),
        id="quarterly_sector",
        name="분기별 FICS 업종 크롤링",
        misfire_grace_time=7200,
        coalesce=True,
        max_instances=1,
    )

    # 잡 6: 매주 일요일 03:30 KST — 상장주식수 갱신 (LS t1102 → floating_shares)
    scheduler.add_job(
        job_update_listed_shares,
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=30, timezone=KST),
        id="update_listed_shares",
        name="상장주식수 갱신 (LS t1102)",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    # 잡 7: 매일 11:30 KST — 일일 요약 (최근 24h 잡 결과 한 통)
    # 깨끗한 성공은 즉시 알림이 없으므로 이게 유일한 '살아있음' 신호다.
    # 11:30인 이유: 08:30 아침 종합 보충이 늦으면 10:50까지 가므로(실측) 여유를 둔다.
    # 24h 창이라 전날 23:30 stockfut / 02:00 daily_update / 03:00~04:00 주간·분기 잡까지 포함.
    scheduler.add_job(
        job_daily_digest,
        trigger=CronTrigger(hour=11, minute=30, timezone=KST),
        id="daily_digest",
        name="일일 요약 알림 (최근 24h)",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )

    now = datetime.now(KST)

    trigger_daily   = CronTrigger(hour=2, minute=0, timezone=KST)
    trigger_backup  = CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=KST)
    trigger_sector     = CronTrigger(month="1,4,7,10", day="1-7", day_of_week="sun", hour=3, minute=30, timezone=KST)
    trigger_financials = CronTrigger(month="4,6,9,12", day="1-7", day_of_week="sun", hour=4, minute=0, timezone=KST)
    next_daily      = trigger_daily.get_next_fire_time(None, now)
    next_backup     = trigger_backup.get_next_fire_time(None, now)
    next_sector     = trigger_sector.get_next_fire_time(None, now)
    next_financials = trigger_financials.get_next_fire_time(None, now)

    logger.info("="*60)
    logger.info("  한국 주식 데이터 수집 + 백업 스케줄러")
    logger.info("="*60)
    logger.info(f"  현재 시각    : {now.strftime('%Y-%m-%d %H:%M:%S KST')}")
    logger.info(f"  다음 수집    : {next_daily.strftime('%Y-%m-%d %H:%M KST') if next_daily else '미정'}")
    logger.info(f"  다음 백업    : {next_backup.strftime('%Y-%m-%d %H:%M KST') if next_backup else '미정'}")
    logger.info(f"  다음 섹터    : {next_sector.strftime('%Y-%m-%d %H:%M KST') if next_sector else '미정'}")
    logger.info(f"  수집 주기    : 매일 02:00 — OHLCV(LS t8451)/수급/외인 + 배당 + LENS export")
    logger.info(f"  백업 주기    : 매주 일요일 03:00  (7일 보관)")
    logger.info(f"  상장주식수   : 매주 일요일 03:30 — LS t1102 → floating_shares")
    logger.info(f"  섹터 주기    : 분기 첫 번째 일요일 03:30 (1/4/7/10월)")
    logger.info(f"  재무지표     : 분기 마감 후 첫 번째 일요일 04:00 (4/6/9/12월) — 다음: {next_financials.strftime('%Y-%m-%d %H:%M KST') if next_financials else '미정'}")
    logger.info(f"  보고서 저장  : reports/daily_update_YYYYMMDD.txt")
    logger.info("  종료: Ctrl+C")
    logger.info("="*60)

    # 시작 시 누락된 주간 잡 보충 (재시작 시점이 잡 fire 시각에 가까웠던 경우 대비)
    startup_catchup()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("\n[스케줄러] 종료됨")


if __name__ == "__main__":
    main()
