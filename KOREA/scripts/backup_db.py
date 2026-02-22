"""
PostgreSQL 데이터베이스 백업 스크립트

pg_dump -Fc 포맷으로 백업 (압축, pg_restore로 복구 가능)
7일 이상 된 백업 파일 자동 삭제

사용법:
    python scripts/backup_db.py           # 즉시 백업 실행

백업 파일 위치: backups/backup_YYYYMMDD_HHMM.dump

복구 방법:
    pg_restore -h localhost -U <user> -d korea_stock_data -Fc backups/backup_XXX.dump
"""

import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import settings

KST        = ZoneInfo("Asia/Seoul")
BACKUPS_DIR = project_root / "backups"
KEEP_DAYS  = 7

# pg_dump 경로: 서버 버전(17)과 일치하는 것 우선 사용
_PG_DUMP_CANDIDATES = [
    "/opt/homebrew/opt/postgresql@17/bin/pg_dump",  # macOS Homebrew PG17
    "pg_dump",                                        # PATH fallback
]

def _find_pg_dump() -> str:
    for candidate in _PG_DUMP_CANDIDATES:
        if Path(candidate).exists() or candidate == "pg_dump":
            return candidate
    return "pg_dump"

PG_DUMP = _find_pg_dump()


# ── 백업 실행 ─────────────────────────────────────────────────────────────────
def run_backup() -> Path:
    """pg_dump 실행 → backups/backup_YYYYMMDD_HHMM.dump"""
    BACKUPS_DIR.mkdir(exist_ok=True)

    now      = datetime.now(KST)
    out_file = BACKUPS_DIR / f"backup_{now.strftime('%Y%m%d_%H%M')}.dump"

    cmd = [
        PG_DUMP,
        "-h", settings.DB_HOST,
        "-U", settings.DB_USER,
        "-d", settings.DB_NAME,
        "-Fc",              # Custom format: 압축 + pg_restore 호환
        "-f", str(out_file),
    ]

    env = {**os.environ, "PGPASSWORD": settings.DB_PASSWORD}

    print(f"백업 시작 : {now.strftime('%Y-%m-%d %H:%M:%S KST')}")
    print(f"출력 파일 : {out_file.name}")

    started = datetime.now(KST)
    result  = subprocess.run(cmd, env=env, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"pg_dump 실패:\n{result.stderr.strip()}")

    elapsed = (datetime.now(KST) - started).total_seconds()
    size_mb = out_file.stat().st_size / (1024 * 1024)
    print(f"✅ 완료 : {size_mb:.1f} MB  ({elapsed:.0f}초)")

    return out_file


# ── 오래된 백업 삭제 ──────────────────────────────────────────────────────────
def cleanup_old_backups(keep_days: int = KEEP_DAYS):
    """keep_days일보다 오래된 backup_*.dump 파일 삭제 + 빈 파일(실패 잔재) 삭제"""
    cutoff  = datetime.now(KST) - timedelta(days=keep_days)
    deleted = []

    for f in sorted(BACKUPS_DIR.glob("backup_*.dump")):
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=KST)
        if f.stat().st_size == 0 or mtime < cutoff:
            f.unlink()
            deleted.append(f.name)

    if deleted:
        print(f"🗑  삭제 ({keep_days}일 초과): {', '.join(deleted)}")

    # 남은 백업 목록
    remaining = sorted(BACKUPS_DIR.glob("backup_*.dump"))
    print(f"\n보관 중 백업 ({len(remaining)}개):")
    for f in remaining:
        size_mb = f.stat().st_size / (1024 * 1024)
        mtime   = datetime.fromtimestamp(f.stat().st_mtime, tz=KST)
        print(f"  {f.name:<35} {size_mb:>7.1f} MB  [{mtime.strftime('%Y-%m-%d %H:%M')}]")


# ── 복구 안내 출력 ────────────────────────────────────────────────────────────
def print_restore_guide(backup_file: Path):
    print(f"""
복구 방법:
  pg_restore -h {settings.DB_HOST} -U {settings.DB_USER} \\
             -d {settings.DB_NAME} -Fc \\
             {backup_file}
""")


# ── 진입점 ────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  PostgreSQL 데이터베이스 백업")
    print("=" * 60)
    try:
        backup_file = run_backup()
        print()
        cleanup_old_backups()
        print_restore_guide(backup_file)
    except FileNotFoundError:
        print(f"❌ pg_dump를 찾을 수 없습니다: {PG_DUMP}")
        print("   macOS: brew link postgresql@17 --force")
        print("   또는: export PATH=/opt/homebrew/opt/postgresql@17/bin:$PATH")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 백업 실패: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
