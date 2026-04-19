from datetime import datetime
from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, Enum as SAEnum
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
    market_cap: Mapped[int] = mapped_column(Integer)         # 시가총액 (원)
    net_income: Mapped[float] = mapped_column(Float)         # 순이익 (억원)
    operating_income: Mapped[float] = mapped_column(Float)   # 영업이익 (억원)
    foreign_ratio: Mapped[float] = mapped_column(Float)      # 외국인 비율 (%)

    drop_from_high: Mapped[float] = mapped_column(Float)     # 고점 대비 하락률 (%)
    rise_from_low: Mapped[float] = mapped_column(Float)      # 저점 대비 상승 배수

    screened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    positions: Mapped[list["Position"]] = relationship("Position", back_populates="stock")
    buy_signals: Mapped[list["BuySignal"]] = relationship("BuySignal", back_populates="stock")


class Position(Base):
    """보유 포지션"""
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(10), ForeignKey("screened_stocks.code"), index=True)
    stock_name: Mapped[str] = mapped_column(String(50))

    buy_rounds_done: Mapped[int] = mapped_column(Integer, default=0)   # 완료된 매수 차수
    sell_rounds_done: Mapped[int] = mapped_column(Integer, default=0)  # 완료된 매도 차수
    quantity: Mapped[int] = mapped_column(Integer, default=0)          # 현재 보유 수량
    avg_buy_price: Mapped[float] = mapped_column(Float, default=0)     # 평균 매입가
    total_buy_amount: Mapped[float] = mapped_column(Float, default=0)  # 총 매입금액

    # 추가 매수 관련
    extra_buy_low: Mapped[float] = mapped_column(Float, nullable=True)  # 추가 매수 기준 저점
    extra_buy_rounds: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[PositionStatus] = mapped_column(SAEnum(PositionStatus), default=PositionStatus.ACTIVE)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    stock: Mapped["ScreenedStock"] = relationship("ScreenedStock", back_populates="positions")
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="position")


class Order(Base):
    """주문 이력"""
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(Integer, ForeignKey("positions.id"), nullable=True)
    stock_code: Mapped[str] = mapped_column(String(10), index=True)
    stock_name: Mapped[str] = mapped_column(String(50))

    order_type: Mapped[OrderType] = mapped_column(SAEnum(OrderType))
    order_round: Mapped[int] = mapped_column(Integer)             # 몇 차 매수/매도
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
    """매수 신호 기록"""
    __tablename__ = "buy_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(10), ForeignKey("screened_stocks.code"), index=True)
    signal_date: Mapped[datetime] = mapped_column(DateTime)
    trigger_round: Mapped[int] = mapped_column(Integer)           # 1~5차
    trigger_price: Mapped[float] = mapped_column(Float)           # 조건 트리거 가격
    target_order_price: Mapped[int] = mapped_column(Integer)      # 전날 종가 (주문 가격)
    prev_close: Mapped[int] = mapped_column(Integer)              # 신호 발생일 종가 (양봉 확인용)
    prev_open: Mapped[int] = mapped_column(Integer)               # 신호 발생일 시가
    is_executed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    stock: Mapped["ScreenedStock"] = relationship("ScreenedStock", back_populates="buy_signals")


class SystemConfig(Base):
    """시스템 설정"""
    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True)
    value: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
