from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Kiwoom API
    KIWOOM_APP_KEY: str = ""
    KIWOOM_SECRET_KEY: str = ""
    KIWOOM_MOCK: bool = True

    @property
    def KIWOOM_BASE_URL(self) -> str:
        return "https://mockapi.kiwoom.com" if self.KIWOOM_MOCK else "https://api.kiwoom.com"

    @property
    def KIWOOM_WS_URL(self) -> str:
        # 키움 WebSocket은 10000 포트 사용
        host = "mockapi.kiwoom.com" if self.KIWOOM_MOCK else "api.kiwoom.com"
        return f"wss://{host}:10000/api/dostk/websocket"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://trading:trading1234@postgres:5432/trading_db"
    REDIS_URL: str = "redis://:redis1234@redis:6379/0"

    # Trading
    TOTAL_INVESTMENT: float = 10_000_000
    BUY_RATIO_PER_ROUND: float = 0.02      # 1회 매수 = 총 투자금의 2%
    MAX_BUY_ROUNDS: int = 5                 # 종목당 최대 5회
    MAX_POSITION_RATIO: float = 0.10        # 종목당 최대 10%
    SELL_RATIOS: list[float] = [0.05, 0.10, 0.15, 0.20]  # 1~4차 매도 수익률
    SELL_QUANTITY_RATIO: float = 0.20       # 1회 매도시 보유 수량의 20%
    MA_PERIOD: int = 20                     # 이동평균선 기간 (5차 매도)

    # Stock screening
    MAX_MARKET_CAP: int = 1_000_000_000_000   # 시가총액 1조 이하
    MIN_STOCK_PRICE: int = 2000               # 주당 최소 2000원
    HIGH_DROP_THRESHOLD: float = 0.50        # 고점 대비 50% 미만
    LOW_RISE_THRESHOLD: float = 2.0          # 저점 대비 2배 이상

    # 추가 매수 조건
    EXTRA_BUY_MIN_SELL_ROUNDS: int = 3       # 3회 이상 매도 후
    EXTRA_BUY_DROP_THRESHOLD: float = 0.90  # 저점의 90% 이하

    # Server
    SECRET_KEY: str = "change_this_to_random_secret_key"

    # Auth
    JWT_SECRET: str = "change_this_to_random_jwt_secret_at_least_32_chars_long"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 12   # 12 시간
    ADMIN_EMAIL: str = "admin@example.com"
    ADMIN_PASSWORD: str = "change_me_on_first_deploy"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
