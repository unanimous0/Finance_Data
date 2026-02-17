"""
설정 관리 모듈

환경변수를 로드하고 애플리케이션 설정을 관리합니다.
"""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """애플리케이션 설정"""

    # Database Configuration
    DB_HOST: str = Field(default="localhost", description="PostgreSQL 호스트")
    DB_PORT: int = Field(default=5432, description="PostgreSQL 포트")
    DB_NAME: str = Field(default="korea_stock_data", description="데이터베이스 이름")
    DB_USER: str = Field(default="postgres", description="데이터베이스 사용자")
    DB_PASSWORD: str = Field(default="", description="데이터베이스 비밀번호")

    # Database Pool Settings
    DB_POOL_SIZE: int = Field(default=5, description="연결 풀 크기")
    DB_MAX_OVERFLOW: int = Field(default=10, description="최대 오버플로우")

    # API Keys
    INFOMAX_API_KEY: str = Field(default="", description="인포맥스 API 키")
    INFOMAX_API_SECRET: str = Field(default="", description="인포맥스 API 시크릿")
    INFOMAX_BASE_URL: str = Field(default="https://api.infomax.co.kr", description="인포맥스 API URL")

    HTS_API_KEY: str = Field(default="", description="HTS API 키")
    HTS_API_SECRET: str = Field(default="", description="HTS API 시크릿")

    # Application Settings
    APP_ENV: str = Field(default="development", description="환경 (development/staging/production)")
    LOG_LEVEL: str = Field(default="INFO", description="로그 레벨")
    TZ: str = Field(default="Asia/Seoul", description="타임존")

    # Scheduler Settings
    SCHEDULER_ENABLED: bool = Field(default=True, description="스케줄러 활성화 여부")
    DAILY_COLLECTION_TIME: str = Field(default="18:00", description="일별 데이터 수집 시간")

    # Data Collection Settings
    COLLECTION_RETRY_COUNT: int = Field(default=3, description="수집 재시도 횟수")
    COLLECTION_TIMEOUT_SECONDS: int = Field(default=300, description="수집 타임아웃 (초)")
    BATCH_SIZE: int = Field(default=100, description="배치 크기")

    # Monitoring & Alerting
    SLACK_WEBHOOK_URL: str = Field(default="", description="Slack 웹훅 URL")
    EMAIL_ALERT_ENABLED: bool = Field(default=False, description="이메일 알림 활성화")
    EMAIL_SMTP_HOST: str = Field(default="", description="SMTP 호스트")
    EMAIL_SMTP_PORT: int = Field(default=587, description="SMTP 포트")
    EMAIL_FROM: str = Field(default="", description="발신 이메일")
    EMAIL_TO: str = Field(default="", description="수신 이메일")

    # Cache Settings (Redis)
    REDIS_HOST: str = Field(default="localhost", description="Redis 호스트")
    REDIS_PORT: int = Field(default=6379, description="Redis 포트")
    REDIS_DB: int = Field(default=0, description="Redis DB 번호")
    CACHE_TTL_SECONDS: int = Field(default=3600, description="캐시 TTL (초)")

    # API Settings (FastAPI)
    API_ENABLED: bool = Field(default=False, description="API 활성화 여부")
    API_HOST: str = Field(default="0.0.0.0", description="API 호스트")
    API_PORT: int = Field(default=8000, description="API 포트")
    API_CORS_ORIGINS: str = Field(default="http://localhost:3000,http://localhost:8080", description="CORS 허용 Origin")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

    @property
    def database_url(self) -> str:
        """PostgreSQL 연결 URL"""
        password = f":{self.DB_PASSWORD}" if self.DB_PASSWORD else ""
        return f"postgresql://{self.DB_USER}{password}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def is_production(self) -> bool:
        """프로덕션 환경 여부"""
        return self.APP_ENV == "production"

    @property
    def is_development(self) -> bool:
        """개발 환경 여부"""
        return self.APP_ENV == "development"


# 전역 설정 인스턴스
settings = Settings()


if __name__ == "__main__":
    # 설정 테스트
    print("=== 설정 확인 ===")
    print(f"Database URL: {settings.database_url}")
    print(f"Environment: {settings.APP_ENV}")
    print(f"Log Level: {settings.LOG_LEVEL}")
    print(f"Scheduler Enabled: {settings.SCHEDULER_ENABLED}")
