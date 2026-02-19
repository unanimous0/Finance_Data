"""
개발 환경 체크 스크립트
Windows에서 git clone 후 필요한 환경이 설치되었는지 확인
"""
import sys
import subprocess
import shutil
from pathlib import Path

def check_command(command: str, version_flag: str = "--version") -> tuple[bool, str]:
    """명령어가 설치되어 있는지 확인"""
    try:
        result = subprocess.run(
            [command, version_flag],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            # 첫 줄만 반환
            version = result.stdout.split('\n')[0] if result.stdout else result.stderr.split('\n')[0]
            return True, version.strip()
        return False, ""
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return False, ""

def check_service(service_name: str) -> tuple[bool, str]:
    """Windows 서비스 상태 확인"""
    try:
        result = subprocess.run(
            ["sc", "query", service_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            output = result.stdout
            if "RUNNING" in output:
                return True, "실행 중"
            elif "STOPPED" in output:
                return True, "중지됨"
            else:
                return True, "알 수 없음"
        return False, "설치되지 않음"
    except Exception:
        return False, "확인 실패"

def main():
    print("=" * 80)
    print("🔍 개발 환경 체크")
    print("=" * 80)

    # 1. 시스템 정보
    print("\n📌 시스템 정보")
    print(f"  OS: {sys.platform}")
    print(f"  Python: {sys.version.split()[0]}")
    print(f"  작업 디렉토리: {Path.cwd()}")

    # 2. 필수 명령어 체크
    print("\n📌 필수 도구 설치 확인")

    commands = [
        ("python3", "--version", "Python 3"),
        ("pip3", "--version", "pip"),
        ("psql", "--version", "PostgreSQL 클라이언트"),
        ("git", "--version", "Git"),
    ]

    for cmd, flag, name in commands:
        installed, version = check_command(cmd, flag)
        if installed:
            print(f"  ✅ {name}: {version}")
        else:
            print(f"  ❌ {name}: 설치되지 않음")

    # 3. PostgreSQL 서비스 확인 (Windows)
    print("\n📌 PostgreSQL 서비스 상태")

    # 여러 가능한 PostgreSQL 서비스 이름 확인
    pg_services = [
        "postgresql-x64-17",
        "postgresql-x64-16",
        "postgresql-x64-15",
        "postgresql",
    ]

    pg_found = False
    for service in pg_services:
        installed, status = check_service(service)
        if installed and status != "설치되지 않음":
            print(f"  ✅ {service}: {status}")
            pg_found = True
        elif installed:
            print(f"  ❌ {service}: {status}")

    if not pg_found:
        print(f"  ❌ PostgreSQL 서비스를 찾을 수 없습니다")

    # 4. Python 패키지 확인
    print("\n📌 Python 가상환경 및 패키지")

    venv_path = Path("venv")
    if venv_path.exists():
        print(f"  ✅ 가상환경: {venv_path.absolute()}")

        # 가상환경의 패키지 확인
        try:
            result = subprocess.run(
                [str(venv_path / "bin" / "python"), "-m", "pip", "list"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                packages = result.stdout.split('\n')
                key_packages = ['SQLAlchemy', 'psycopg2-binary', 'pandas', 'pydantic', 'openpyxl']
                print(f"  설치된 패키지 수: {len([p for p in packages if p.strip() and not p.startswith('Package')])}")

                for pkg in key_packages:
                    if any(pkg.lower() in line.lower() for line in packages):
                        print(f"    ✅ {pkg}")
                    else:
                        print(f"    ❌ {pkg}")
        except Exception as e:
            print(f"  ⚠️  패키지 확인 실패: {e}")
    else:
        print(f"  ❌ 가상환경: 생성되지 않음")

    # 5. 환경변수 파일 확인
    print("\n📌 환경 설정 파일")

    env_file = Path(".env")
    if env_file.exists():
        print(f"  ✅ .env 파일: 존재")
        # .env 파일 내용 일부 확인 (민감 정보 제외)
        with open(env_file) as f:
            lines = f.readlines()
            for line in lines[:10]:  # 처음 10줄만
                if line.startswith('DB_') and '=' in line:
                    key = line.split('=')[0]
                    value = line.split('=')[1].strip()
                    if 'PASSWORD' in key or 'KEY' in key or 'SECRET' in key:
                        value = "***" if value else "(비어있음)"
                    print(f"    {key}: {value}")
    else:
        print(f"  ❌ .env 파일: 없음")

    # 6. 데이터 파일 확인
    print("\n📌 데이터 파일")

    raw_data_dir = Path("raw_data")
    if raw_data_dir.exists():
        files = list(raw_data_dir.glob("*"))
        print(f"  ✅ raw_data 폴더: {len(files)}개 파일")
        for f in files:
            size_kb = f.stat().st_size / 1024
            print(f"    - {f.name} ({size_kb:.1f} KB)")
    else:
        print(f"  ❌ raw_data 폴더: 없음")

    # 7. 데이터베이스 연결 테스트
    print("\n📌 데이터베이스 연결 테스트")

    try:
        # .env 파일 로드
        env_vars = {}
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        env_vars[key] = value

        db_host = env_vars.get('DB_HOST', 'localhost')
        db_port = env_vars.get('DB_PORT', '5432')
        db_name = env_vars.get('DB_NAME', 'korea_stock_data')
        db_user = env_vars.get('DB_USER', 'postgres')

        print(f"  연결 정보: {db_user}@{db_host}:{db_port}/{db_name}")

        # psql 명령으로 연결 테스트
        installed, _ = check_command("psql", "--version")
        if installed:
            result = subprocess.run(
                ["psql", "-h", db_host, "-p", db_port, "-U", db_user, "-d", db_name, "-c", "SELECT version();"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                print(f"  ✅ 데이터베이스 연결: 성공")
            else:
                print(f"  ❌ 데이터베이스 연결: 실패")
                print(f"     오류: {result.stderr.strip()}")
        else:
            print(f"  ⚠️  psql 명령이 없어 연결 테스트 건너뜀")
    except Exception as e:
        print(f"  ❌ 연결 테스트 실패: {e}")

    # 8. 요약 및 권장사항
    print("\n" + "=" * 80)
    print("📋 요약 및 다음 단계")
    print("=" * 80)

    issues = []

    # PostgreSQL 체크
    pg_installed, _ = check_command("psql", "--version")
    if not pg_installed:
        issues.append("PostgreSQL 클라이언트 설치 필요")

    if not pg_found:
        issues.append("PostgreSQL 서버 설치 또는 시작 필요")

    # 가상환경 체크
    if not venv_path.exists():
        issues.append("Python 가상환경 생성 필요")

    # .env 파일 체크
    if not env_file.exists():
        issues.append(".env 파일 생성 필요")

    # raw_data 폴더 체크
    if not raw_data_dir.exists() or not list(raw_data_dir.glob("*")):
        issues.append("raw_data 폴더에 데이터 파일 추가 필요")

    if issues:
        print("\n⚠️  해결해야 할 사항:")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")

        print("\n💡 권장 작업 순서:")
        if "PostgreSQL" in str(issues):
            print("  1. PostgreSQL 17 설치 (Windows용)")
            print("     다운로드: https://www.postgresql.org/download/windows/")
        if "가상환경" in str(issues):
            print("  2. Python 가상환경 생성: python -m venv venv")
            print("  3. 패키지 설치: venv\\Scripts\\pip install -r requirements.txt")
        if ".env" in str(issues):
            print("  4. .env 파일 생성 및 DB 정보 입력")
        if "PostgreSQL 서버" in str(issues):
            print("  5. PostgreSQL 서비스 시작")
        if "raw_data" in str(issues):
            print("  6. raw_data 폴더 생성 및 데이터 파일 추가")
    else:
        print("\n✅ 모든 환경이 준비되었습니다!")

    print("=" * 80)

if __name__ == "__main__":
    main()
