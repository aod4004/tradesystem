from datetime import datetime
from sqlalchemy import String, Integer, BigInteger, Float, Boolean, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum
from app.db.database import Base


class OrderType(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"      # 주문 예정
    SUBMITTED = "submitted"  # 주문 접수
    FILLED = "filled"        # 체결 완료
    CANCELLED = "cancelled"  # 취소


class PositionStatus(str, enum.Enum):
    ACTIVE = "active"        # 보유 중
    CLOSED = "closed"        # 청산 완료


class User(Base):
    """사용자 계정"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    trading_config: Mapped["UserTradingConfig"] = relationship(
        "UserTradingConfig", back_populates="user", uselist=False, cascade="all, delete-orphan",
    )


class UserTradingConfig(Base):
    """유저별 투자금·키움 키. 키움 키 컬럼은 Phase 2.5 에서 실제 사용."""
    __tablename__ = "user_trading_config"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    total_investment: Mapped[float] = mapped_column(Float, default=10_000_000.0)
    kiwoom_app_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kiwoom_secret_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    kiwoom_mock: Mapped[bool] = mapped_column(Boolean, default=True)
    # 카카오톡 "나에게 보내기" OAuth 토큰 (Phase 3)
    kakao_access_token: Mapped[str | None] = mapped_column(String(500), nullable=True)
    kakao_refresh_token: Mapped[str | None] = mapped_column(String(500), nullable=True)
    kakao_access_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    kakao_refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
    )

    user: Mapped["User"] = relationship("User", back_populates="trading_config")


class ScreenedStock(Base):
    """스크리닝 통과 종목"""
    __tablename__ = "screened_stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(50))
    market: Mapped[str] = mapped_column(String(10))          # KOSPI / KOSDAQ

    # 스크리닝 기준 데이터
    current_price: Mapped[int] = mapped_column(Integer)
    high_1y: Mapped[int] = mapped_column(Integer)            # 1년 고점
    low_1y: Mapped[int] = mapped_column(Integer)             # 1년 저점
    market_cap: Mapped[int] = mapped_column(BigInteger)      # 시가총액 (원) — 조 단위라 BigInteger
    net_income: Mapped[float] = mapped_column(Float)         # 순이익 (억원)
    operating_income: Mapped[float] = mapped_column(Float)   # 영업이익 (억원)
    foreign_ratio: Mapped[float] = mapped_column(Float)      # 외국인 비율 (%)

    drop_from_high: Mapped[float] = mapped_column(Float)     # 고점 대비 하락률 (%)
    rise_from_low: Mapped[float] = mapped_column(Float)      # 저점 대비 상승 배수

    screened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # positions / buy_signals 는 stock_code FK 가 0005 에서 제거되므로 관계 선언 제거.
    # 필요할 때 명시적 쿼리로 조인.


class Position(Base):
    """보유 포지션"""
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    stock_code: Mapped[str] = mapped_column(String(10), index=True)   # FK 0005에서 드롭 — 관심종목 대응
    stock_name: Mapped[str] = mapped_column(String(50))

    buy_rounds_done: Mapped[int] = mapped_column(Integer, default=0)   # 완료된 매수 차수
    sell_rounds_done: Mapped[int] = mapped_column(Integer, default=0)  # 완료된 매도 tranche 수 (0~5)
    # 발동된 매도 조건 비트마스크. 비트 레이아웃:
    #   0~3 = 수익률 5/10/15/20%, 4+i = SELL_MA_PERIODS[i] (MA20/60/120) 터치
    sold_triggers: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    quantity: Mapped[int] = mapped_column(Integer, default=0)          # 현재 보유 수량
    avg_buy_price: Mapped[float] = mapped_column(Float, default=0)     # 평균 매입가
    total_buy_amount: Mapped[float] = mapped_column(Float, default=0)  # 총 매입금액

    # 추가 매수 관련
    extra_buy_low: Mapped[float] = mapped_column(Float, nullable=True)  # 추가 매수 기준 저점
    extra_buy_rounds: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[PositionStatus] = mapped_column(SAEnum(PositionStatus), default=PositionStatus.ACTIVE)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    orders: Mapped[list["Order"]] = relationship("Order", back_populates="position")


class Order(Base):
    """주문 이력"""
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    position_id: Mapped[int] = mapped_column(Integer, ForeignKey("positions.id"), nullable=True)
    stock_code: Mapped[str] = mapped_column(String(10), index=True)
    stock_name: Mapped[str] = mapped_column(String(50))

    order_type: Mapped[OrderType] = mapped_column(SAEnum(OrderType))
    order_round: Mapped[int] = mapped_column(Integer)             # 몇 차 매수/매도 (0=추가매수, SELL은 tranche 1~5)
    # 매도 주문일 때 발동된 조건의 비트 index (Position.sold_triggers 와 동일한 비트 레이아웃)
    sell_trigger_bit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_price: Mapped[int] = mapped_column(Integer)             # 주문 가격 (지정가)
    order_qty: Mapped[int] = mapped_column(Integer)               # 주문 수량
    filled_price: Mapped[int] = mapped_column(Integer, nullable=True)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    kiwoom_order_no: Mapped[str] = mapped_column(String(20), nullable=True)

    status: Mapped[OrderStatus] = mapped_column(SAEnum(OrderStatus), default=OrderStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    filled_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    position: Mapped["Position"] = relationship("Position", back_populates="orders")


class BuySignal(Base):
    """매수 신호 기록. stock_code FK 는 0005 에서 드롭 — 유저 관심종목도 담기 위함.
    스크리닝 유래 신호는 ScreenedStock 에 매칭되고, 관심종목 유래 신호는 stock_name 만 유효.
    """
    __tablename__ = "buy_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    stock_code: Mapped[str] = mapped_column(String(10), index=True)
    stock_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="screening")   # 'screening' | 'watchlist'
    signal_date: Mapped[datetime] = mapped_column(DateTime)
    trigger_round: Mapped[int] = mapped_column(Integer)           # 1~5차
    trigger_price: Mapped[float] = mapped_column(Float)           # 조건 트리거 가격
    target_order_price: Mapped[int] = mapped_column(Integer)      # 전날 종가 (주문 가격)
    prev_close: Mapped[int] = mapped_column(Integer)              # 신호 발생일 종가 (양봉 확인용)
    prev_open: Mapped[int] = mapped_column(Integer)               # 신호 발생일 시가
    is_executed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserWatchlist(Base):
    """유저가 직접 등록한 관심 종목 — 스크리닝 후보에 추가되어 동일 매수 전략 적용."""
    __tablename__ = "user_watchlist"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    stock_name: Mapped[str] = mapped_column(String(50))
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SystemConfig(Base):
    """시스템 설정"""
    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True)
    value: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
