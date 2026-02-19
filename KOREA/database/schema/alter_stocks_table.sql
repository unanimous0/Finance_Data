-- stocks 테이블 수정: 실제 데이터 형식에 맞춰 조정
-- 2026-02-19

-- 1. standard_code 컬럼 추가 (국제표준코드, ISIN)
ALTER TABLE stocks
ADD COLUMN standard_code VARCHAR(12) UNIQUE;

-- 2. market 컬럼 NULL 허용으로 변경
ALTER TABLE stocks
ALTER COLUMN market DROP NOT NULL;

-- 3. listing_date 컬럼 NULL 허용으로 변경 (이미 NULL 가능하면 스킵)
-- ALTER TABLE stocks
-- ALTER COLUMN listing_date DROP NOT NULL;

-- 4. sector_id 컬럼 NULL 허용으로 변경 (이미 NULL 가능하면 스킵)
-- ALTER TABLE stocks
-- ALTER COLUMN sector_id DROP NOT NULL;

-- 5. 수정 확인
\d stocks
