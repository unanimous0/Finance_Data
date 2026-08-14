"""Telegram 알림 헬퍼.

스케줄러 잡들이 시작/완료/실패 시 호출. .env의 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 사용.
둘 중 하나라도 비어있으면 silent skip — 운영 중단 없이 알림만 끔.
"""
import json
import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

KST = ZoneInfo("Asia/Seoul")

# 프로젝트 루트의 .env 로드
load_dotenv(Path(__file__).parent.parent / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

logger = logging.getLogger(__name__)

# detail 본문에 이 마커가 있으면 '이상 있는 성공'으로 승격 → 즉시(무음) 전송.
# 호출부가 warn=True를 안 넘겨도 동작하도록 문자열로 감지한다.
#   ⚠️ 경고(외인 빈응답, 마스터 완결성 등) / ⛔ 부가 단계 오류 / 🚨 주가이벤트의심
WARN_MARKERS = ("⚠️", "⛔", "🚨")


def is_configured() -> bool:
    return bool(BOT_TOKEN and CHAT_ID)


def send(text: str, silent: bool = False) -> bool:
    """Telegram 메시지 전송. 설정 없거나 실패 시 False 반환 (예외 안 던짐)."""
    if not is_configured():
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": text[:4000],  # Telegram 단일 메시지 한도 4096자
                "disable_notification": silent,
            },
            timeout=10,
        )
        if not r.ok:
            logger.warning(f"Telegram 전송 실패: {r.status_code} {r.text[:200]}")
        return r.ok
    except Exception as e:
        logger.warning(f"Telegram 전송 예외: {e}")
        return False


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}m"
    h, m = divmod(seconds / 60, 60)
    return f"{int(h)}h{int(m)}m"


# ── 잡 이벤트 적재 (일일 요약용) ──────────────────────────────────────────
# 깨끗한 성공은 즉시 알림을 보내지 않으므로, 하루 1회 요약에서 보여주려면
# 어딘가 남겨야 한다. append-only JSONL — 하루 5줄 남짓이라 부담 없음.
JOB_EVENTS_PATH = Path(__file__).parent.parent / "logs" / "job_events.jsonl"
EVENT_RETENTION_DAYS = 30


def _probe_dividends_since(ts: datetime) -> bool:
    """`ts` 이후 배당 데이터가 실제로 적재됐는지 — DART 배당 단계의 해소 증거.

    created_at은 **UTC 저장**(서버 TimeZone=Etc/UTC)이라 KST aware 값을 그대로 비교하면
    9시간 어긋난다. psycopg2가 aware datetime을 넘길 때 서버가 알아서 맞추도록
    `created_at AT TIME ZONE 'UTC' > %s AT TIME ZONE 'Asia/Seoul'` 대신
    timestamptz로 캐스팅해 비교한다.
    """
    import psycopg2
    from config.settings import settings
    conn = psycopg2.connect(
        host=settings.DB_HOST, dbname=settings.DB_NAME, user=settings.DB_USER)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM dividends "
                "WHERE created_at AT TIME ZONE 'UTC' > %s)", (ts,))
            return bool(cur.fetchone()[0])
    finally:
        conn.close()


# 부가 단계별 "그 뒤로 정상화됐나" 판정기. 단계 이름은 daily_update의
# `_record_step_error(step=...)`와 같은 문자열이어야 한다.
#
# 없는 단계는 조용히 건너뛴다 — 근거 없이 "해소됨"을 붙이면 진짜 장애를 덮는다.
# (증거는 "오류 이후 그 파이프라인의 산출물이 갱신됨". 잡이 다시 돌기를 기다리면
#  daily_update는 24h 주기라 요약 창에서 이미 빠져나간 뒤다 — 2026-08-12 사례)
_STEP_RESOLUTION_PROBES = {
    "배당": _probe_dividends_since,
}


def _resolution_note(event: dict, later_events: list[dict]) -> str:
    """이 문제 이벤트가 요약 시점엔 이미 해소됐는지 판정해 꼬리표를 만든다.

    두 가지 증거만 인정한다:
      1) 같은 잡이 그 뒤에 깨끗하게 한 번 더 돌았다
      2) 실패한 부가 단계의 산출물이 그 뒤에 갱신됐다 (_STEP_RESOLUTION_PROBES)
    """
    import re
    for nxt in later_events:
        if nxt["job"] == event["job"] and not nxt.get("warned") and nxt["status"] != "fail":
            en = datetime.fromisoformat(nxt["ended"])
            return f" (해소됨 — {en:%m-%d %H:%M} 같은 잡 정상 완료)"

    steps = re.findall(r"^\s*•\s*([^:\n]+):", event.get("detail") or "", re.MULTILINE)
    ended = datetime.fromisoformat(event["ended"])
    checked, resolved = [], []
    for step in steps:
        probe = _STEP_RESOLUTION_PROBES.get(step.strip())
        if not probe:
            continue
        checked.append(step.strip())
        try:
            if probe(ended):
                resolved.append(step.strip())
        except Exception as e:
            logger.warning(f"해소 판정 실패({step.strip()}): {e}")
            return ""
    if checked and len(resolved) == len(checked):
        return f" (해소됨 — {'/'.join(resolved)} 이후 정상 적재 확인)"
    return ""


def _record_event(job_name: str, status: str, started: datetime,
                  ended: datetime, duration: float, detail: str,
                  warned: bool, sent: bool) -> None:
    try:
        JOB_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "job": job_name, "status": status, "warned": warned, "sent": sent,
            "started": started.isoformat(), "ended": ended.isoformat(),
            "duration": round(duration, 1),
            # 요약엔 앞 몇 줄만 쓰므로 과하게 저장하지 않는다
            "detail": (detail or "").strip()[:600],
        }
        with JOB_EVENTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"잡 이벤트 기록 실패: {e}")


def notify_job(
    job_name: str,
    status: str,
    started: datetime,
    detail: str = "",
    warn: bool = False,
) -> bool:
    """잡 결과 알림 — 3단계 정책.

    status: 'ok' / 'fail' / 'noop'
    warn  : 성공이지만 사람이 봐야 하는 이상이 있을 때 True

    등급:
      실패            → 즉시 전송 + 소리
      이상 있는 성공  → 즉시 전송 (무음)
      깨끗한 성공     → **전송 안 함**. 하루 1회 요약(send_daily_digest)에만 등장

    배경: 매일 오는 성공 알림에 무뎌져 정작 중요한 걸 놓친다(사용자 피드백).
    다만 "실패만 보내기"는 위험하다 — 침묵이 '정상'인지 '죽어서 못 보냄'인지
    구분되지 않고, 실제로 2026-07-29 외인 부분수집(1,415/2,645)은 **성공 알림
    안에 섞여 있던 문제**라 실패만 봤으면 영영 못 봤다. 그래서 '이상 있는 성공'을
    별도 등급으로 두고, 생존 신호는 일일 요약이 담당한다.

    warn 자동 감지: 기존 호출부들이 이미 detail에 '⚠️'를 넣고 있어(외인 빈응답,
    마스터 완결성 등) 그것도 이상으로 간주한다. 호출부 일괄 수정 없이 동작.

    마커에 '🚨'(주가이벤트의심) 추가 — 2026-08-14. daily_update 요약의 급등락
    블록은 🚨만 쓰는데 마커에 없어 **단독 발생 시 알림이 안 나갔다**.
    실제로 8/13 시스웍(269620) 889→27원(-97%)이 보고서에만 남고 전송 0.
    수정주가 확인이 필요한 건이라 침묵하면 안 된다.
    """
    ended = datetime.now(KST)
    duration = (ended - started).total_seconds()
    warned = (bool(warn)
              or any(m in (detail or "") for m in WARN_MARKERS))

    tag = {"ok": "성공", "fail": "실패", "noop": "건너뜀"}.get(status, status)
    if warned and status != "fail":
        tag = f"{tag}·확인필요"
    head = f"[{tag}] {job_name}"
    time_line = f"{started:%m-%d %H:%M} ~ {ended:%H:%M} ({_fmt_duration(duration)})"

    lines = [head, time_line]
    if detail:
        lines.append("")
        lines.append(detail.strip())

    should_send = (status == "fail") or warned
    sent = False
    if should_send:
        # 실패만 소리, 이상 있는 성공은 무음
        sent = send("\n".join(lines), silent=(status != "fail"))
    _record_event(job_name, status, started, ended, duration, detail, warned, sent)
    return sent


def send_daily_digest(hours: int = 24) -> bool:
    """최근 `hours` 시간의 잡 결과를 한 통으로 묶어 전송 (무음).

    깨끗한 성공은 즉시 알림이 없으므로 이 요약이 유일한 '살아있음' 신호다.
    요약이 안 오면 그 자체가 이상 신호 — 그래서 실패/이상이 없어도 매일 보낸다.
    """
    now = datetime.now(KST)
    cutoff = now - timedelta(hours=hours)
    events, keep = [], []
    prune_before = now - timedelta(days=EVENT_RETENTION_DAYS)
    try:
        if JOB_EVENTS_PATH.exists():
            raw_lines = JOB_EVENTS_PATH.read_text(encoding="utf-8").splitlines()
            for line in raw_lines:
                try:
                    rec = json.loads(line)
                    ts = datetime.fromisoformat(rec["ended"])
                except Exception:
                    continue
                if ts >= prune_before:
                    keep.append(line)
                if ts >= cutoff:
                    events.append(rec)
            # 보관기간 지난 줄 정리 (파일 무한 증가 방지)
            if len(keep) != len(raw_lines):
                JOB_EVENTS_PATH.write_text("\n".join(keep) + ("\n" if keep else ""),
                                           encoding="utf-8")
    except Exception as e:
        logger.warning(f"잡 이벤트 읽기 실패: {e}")

    icon = {"ok": "✅", "fail": "❌", "noop": "⏭"}
    lines = [f"[일일 요약] {now:%m-%d %H:%M} 기준 (최근 {hours}h)"]
    if not events:
        lines.append("")
        lines.append("⚠️ 기록된 잡 없음 — 스케줄러 미가동 의심")
    else:
        for e in events:
            st = datetime.fromisoformat(e["started"])
            en = datetime.fromisoformat(e["ended"])
            mark = icon.get(e["status"], "·")
            if e.get("warned") and e["status"] != "fail":
                mark = "⚠️"
            lines.append(f"{mark} {e['job']}  {st:%H:%M}~{en:%H:%M} "
                         f"({_fmt_duration(e['duration'])})")
        problems = [e for e in events if e["status"] == "fail" or e.get("warned")]
        # 요약 시점엔 이미 고쳐진 건과 지금도 살아있는 건을 갈라서 보여준다.
        # 요약 창이 24h라 "새벽에 터지고 낮에 고친" 사고가 한 번은 실리는데,
        # 표시가 없으면 새 사고인지 지난 사고인지 메시지만 봐선 알 수 없다.
        notes = {}
        for idx, e in enumerate(problems):
            later = [x for x in events
                     if datetime.fromisoformat(x["ended"]) > datetime.fromisoformat(e["ended"])]
            notes[idx] = _resolution_note(e, later)
        open_count = sum(1 for idx in notes if not notes[idx])
        lines.append("")
        if problems:
            done = len(problems) - open_count
            if open_count == 0:
                head = f"확인 필요 없음 — 해소된 이력 {done}건"
            elif done:
                head = f"확인 필요 {open_count}건 (+ 해소 {done}건)"
            else:
                head = f"확인 필요 {open_count}건"
            lines.append(head + ":")
            for idx, e in enumerate(problems):
                body = [ln.strip() for ln in (e.get("detail") or "").splitlines() if ln.strip()]
                # 경고문이 있으면 그걸 보여준다. 첫 줄은 보통 정상 요약이라
                # (예: '외인 4,060개') 정작 봐야 할 '⚠️ 빈응답 1,230종목'이 묻힌다.
                marked = [ln for ln in body if "⚠️" in ln or "⛔" in ln or ln.startswith("에러")]
                lines.append(f"  • {e['job']}: {(marked or body or [e['status']])[0]}{notes[idx]}")
        else:
            lines.append("이상 없음")
    return send("\n".join(lines), silent=True)
