"""
종목 마스터 데이터 확인 스크립트
"""
import pandas as pd
import sys
from pathlib import Path

# 파일 경로
file_path = Path(__file__).parent.parent / "raw_data" / "1-종목코드_종목명.xlsx"

print(f"파일 경로: {file_path}")
print(f"파일 존재: {file_path.exists()}\n")

if not file_path.exists():
    print("❌ 파일을 찾을 수 없습니다.")
    sys.exit(1)

# Excel 파일 읽기
df = pd.read_excel(file_path)

print("=" * 80)
print("📊 데이터 기본 정보")
print("=" * 80)
print(f"총 레코드 수: {len(df):,}개")
print(f"컬럼 수: {len(df.columns)}개")
print(f"\n컬럼 목록:")
for i, col in enumerate(df.columns, 1):
    print(f"  {i}. {col} (dtype: {df[col].dtype})")

print("\n" + "=" * 80)
print("📋 샘플 데이터 (처음 10개)")
print("=" * 80)
print(df.head(10).to_string())

print("\n" + "=" * 80)
print("📋 샘플 데이터 (마지막 10개)")
print("=" * 80)
print(df.tail(10).to_string())

print("\n" + "=" * 80)
print("📊 데이터 통계")
print("=" * 80)

# NULL 값 확인
print("\n결측치 확인:")
null_counts = df.isnull().sum()
for col, count in null_counts.items():
    if count > 0:
        print(f"  {col}: {count}개 ({count/len(df)*100:.2f}%)")
    else:
        print(f"  {col}: 없음 ✅")

# 중복 확인
print(f"\n중복 레코드: {df.duplicated().sum()}개")

# 각 컬럼별 고유값 개수
print("\n고유값 개수:")
for col in df.columns:
    unique_count = df[col].nunique()
    print(f"  {col}: {unique_count:,}개")

print("\n" + "=" * 80)
print("✅ 분석 완료")
print("=" * 80)
