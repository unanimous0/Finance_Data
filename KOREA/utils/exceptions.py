"""
커스텀 예외 클래스 모듈

프로젝트에서 사용하는 예외 정의
"""


class KoreaStockDataError(Exception):
    """기본 예외 클래스"""

    pass


class DatabaseError(KoreaStockDataError):
    """데이터베이스 관련 예외"""

    pass


class ConnectionError(DatabaseError):
    """데이터베이스 연결 오류"""

    pass


class DataCollectionError(KoreaStockDataError):
    """데이터 수집 관련 예외"""

    pass


class APIError(DataCollectionError):
    """API 호출 오류"""

    def __init__(self, message: str, status_code: int = None, response=None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class DataValidationError(KoreaStockDataError):
    """데이터 검증 오류"""

    def __init__(self, message: str, field: str = None, value=None):
        super().__init__(message)
        self.field = field
        self.value = value


class SchemaValidationError(DataValidationError):
    """스키마 검증 오류"""

    pass


class BusinessRuleError(DataValidationError):
    """비즈니스 규칙 위반"""

    pass


class ConfigurationError(KoreaStockDataError):
    """설정 오류"""

    pass
