-- ==========================================
-- KRX 휴장일 테이블 (krx_holidays)
-- ==========================================
-- 설계 원칙:
--   1) Finance_Data DB가 휴장일 SSoT (단일 진실 공급원)
--   2) 모든 휴장일 소비처(daily_update, backfill_dividends, LENS export)는 이 테이블 조회
--   3) 산출 출처 추적 (source 컬럼) — 디버그/감사용
--   4) 토/일은 저장하지 않음 (캘린더에서 자명, 현재 LENS export 정책과 일치)
--
-- source 우선순위 (한 날짜에 여러 소스 매칭 시):
--   1) 'ohlcv_gap'   — 과거 ohlcv_daily 갭 (가장 정확, 임시공휴일 자동 포착)
--   2) 'manual'      — 사람이 직접 INSERT (재계산 시 보호됨)
--   3) 'holidays_kr' — 미래 holidays.KR 라이브러리
--   4) 'rule_0501'   — 근로자의 날 (라이브러리 미수록)
--   5) 'rule_1231'   — 연말 폐장 (KRX 관행)
-- ==========================================

CREATE TABLE IF NOT EXISTS krx_holidays (
    date         DATE        PRIMARY KEY,                 -- PK가 자동 인덱스 생성
    reason       TEXT        NOT NULL,                    -- 한국어 사유 (예: '근로자의 날', '어린이날')
    source       TEXT        NOT NULL
                 CHECK (source IN ('ohlcv_gap', 'manual', 'holidays_kr', 'rule_0501', 'rule_1231')),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE krx_holidays IS 'KRX 휴장일 SSoT — Finance_Data 내부 + LENS export 모두 이 테이블 참조';
COMMENT ON COLUMN krx_holidays.source IS 'ohlcv_gap | manual | holidays_kr | rule_0501 | rule_1231';
