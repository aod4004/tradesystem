"""
키움증권 WebSocket 풀 — 유저별 1개 커넥션

- URL: wss://api.kiwoom.com:10000/api/dostk/websocket  (실거래)
       wss://mockapi.kiwoom.com:10000/api/dostk/websocket  (모의)
- 인증: 유저의 token (Phase 2.5 부터 유저별 키움 키)

각 유저 WS 는:
  - 주문체결(00) + 잔고(04) 수신 — 토큰에 귀속되므로 유저 본인 주문만 들어옴
  - 보유 종목의 실시간 체결(0B) 구독 — 매도 조건 실시간 평가

글로벌(시장 데이터) 상태:
  - KiwoomPool._ma_cache[code] = {period: value}  — MA20/60/120 공용 캐시

주식체결(0B) 필드 코드:
    10=현재가, 11=전일대비, 12=등락율, 13=누적거래량, 15=거래량, 16=시가, 17=고가, 18=저가, 20=체결시간

주문체결(00) 필드 코드:
    9001=종목코드, 302=종목명, 913=주문상태(접수/체결/확인/취소/거부),
    907=매도수구분(1매도/2매수), 900=주문수량, 901=주문가격,
    910=체결가, 911=체결량, 9203=주문번호
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import websockets
from sqlalchemy import select

from app.core.kiwoom_client import KiwoomClient, to_int
from app.db.database import AsyncSessionLocal
from app.db.models import Position, Order, OrderType, PositionStatus, OrderStatus
from app.strategy.signal import check_sell_signal, check_extra_buy_signal
from app.strategy.executor import execute_sell_order, execute_extra_buy_order
from app.ws.manager import manager


class UserKiwoomWS:
    """한 유저의 키움 WS 커넥션 (order + balance + per-position price 구독)."""

    def __init__(self, pool: "KiwoomPool", user_id: int, client: KiwoomClient):
        self._pool = pool
        self.user_id = user_id
        self.client = client
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._subscribed: set[str] = set()
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._connect_loop())

    async def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._subscribed.clear()

    async def _connect_loop(self) -> None:
        while self._running:
            try:
                await self._run()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[kiwoom_ws user={self.user_id}] 연결 오류: {e} — 5초 후 재연결")
                await asyncio.sleep(5)

    async def _run(self) -> None:
        token = await self.client.get_token()
        headers = {"authorization": f"Bearer {token}"}
        async with websockets.connect(
            self.client.ws_url,
            extra_headers=headers,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            self._ws = ws
            print(f"[kiwoom_ws user={self.user_id}] 연결됨 — {self.client.ws_url}")

            # 주문체결(00) + 잔고(04) — 토큰 귀속, item 불필요
            await self._send({
                "trnm": "REG", "grp_no": "1", "refresh": "1",
                "data": [{"item": [""], "type": ["00", "04"]}],
            })

            # 이 유저의 보유 종목 실시간 체결(0B) 구독
            await self._subscribe_active_positions()

            async for raw in ws:
                await self._handle_message(raw)

    async def _send(self, msg: dict) -> None:
        if self._ws is not None:
            await self._ws.send(json.dumps(msg))

    async def _subscribe_active_positions(self) -> None:
        async with AsyncSessionLocal() as db:
            positions = (
                await db.execute(
                    select(Position).where(
                        Position.user_id == self.user_id,
                        Position.status == PositionStatus.ACTIVE,
                    )
                )
            ).scalars().all()
        for pos in positions:
            await self.subscribe_price(pos.stock_code)

    async def subscribe_price(self, stock_code: str) -> None:
        if stock_code in self._subscribed or self._ws is None:
            return
        await self._send({
            "trnm": "REG", "grp_no": "2", "refresh": "1",
            "data": [{"item": [stock_code], "type": ["0B"]}],
        })
        self._subscribed.add(stock_code)

    async def unsubscribe_price(self, stock_code: str) -> None:
        if stock_code not in self._subscribed or self._ws is None:
            return
        await self._send({
            "trnm": "REMOVE", "grp_no": "2",
            "data": [{"item": [stock_code], "type": ["0B"]}],
        })
        self._subscribed.discard(stock_code)

    # ------------------------------------------------------------------ #
    #  수신 처리
    # ------------------------------------------------------------------ #
    async def _handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return

        trnm = msg.get("trnm")
        if trnm != "REAL":
            if msg.get("return_code", 0) != 0:
                print(f"[kiwoom_ws user={self.user_id}] {trnm} 응답 오류: {msg.get('return_msg')}")
            return

        for item in msg.get("data", []) or []:
            t = item.get("type")
            if t == "0B":
                await self._on_price(item)
            elif t == "00":
                await self._on_order_event(item)
            elif t == "04":
                await self._on_balance_event(item)

    async def _on_price(self, item: dict) -> None:
        """주식체결(0B)"""
        code = item.get("item", "")
        values = item.get("values", {}) or {}
        current_price = abs(to_int(values.get("10")))
        if not code or current_price <= 0:
            return

        await manager.broadcast("price_update", {
            "user_id": self.user_id,
            "code": code,
            "current_price": current_price,
            "change_rate": float(values.get("12") or 0),
        })
        await self._check_and_execute_sell(code, current_price)

    async def _check_and_execute_sell(self, code: str, current_price: int) -> None:
        async with AsyncSessionLocal() as db:
            positions = (
                await db.execute(
                    select(Position).where(
                        Position.user_id == self.user_id,
                        Position.stock_code == code,
                        Position.status == PositionStatus.ACTIVE,
                    )
                )
            ).scalars().all()
            if not positions:
                return

            ma_values = self._pool.get_ma(code)
            for position in positions:
                decision = check_sell_signal(
                    current_price, position.avg_buy_price,
                    position.sell_rounds_done, position.sold_triggers, ma_values,
                )
                if decision:
                    sell_round, trigger_bit = decision
                    await manager.broadcast("sell_signal", {
                        "user_id": self.user_id,
                        "code": code,
                        "sell_round": sell_round,
                        "trigger_bit": trigger_bit,
                        "current_price": current_price,
                        "avg_buy_price": position.avg_buy_price,
                        "gain_rate": round(
                            (current_price - position.avg_buy_price) / position.avg_buy_price * 100, 2
                        ),
                    })
                    await execute_sell_order(
                        db, position, sell_round, trigger_bit, current_price, self.client,
                    )
                if check_extra_buy_signal(current_price, position):
                    await manager.broadcast("extra_buy_signal", {
                        "user_id": self.user_id,
                        "code": code,
                        "current_price": current_price,
                    })
                    await execute_extra_buy_order(db, position, current_price, self.client)

    async def _on_order_event(self, item: dict) -> None:
        """주문체결(00)"""
        values = item.get("values", {}) or {}
        order_no = values.get("9203", "")
        state = values.get("913", "")
        filled_price = abs(to_int(values.get("910")))
        filled_qty = to_int(values.get("911"))

        await manager.broadcast("order_event", {
            "user_id": self.user_id,
            "code": values.get("9001", ""),
            "order_no": order_no,
            "state": state,
            "filled_price": filled_price,
            "filled_qty": filled_qty,
        })
        if not order_no:
            return
        if state == "체결":
            await self._apply_fill(order_no, filled_price, filled_qty)
        elif state in ("취소", "거부"):
            await self._apply_cancel(order_no, state)

    async def _apply_fill(self, order_no: str, price: int, qty: int) -> None:
        if qty <= 0:
            return
        async with AsyncSessionLocal() as db:
            order = (
                await db.execute(
                    select(Order).where(
                        Order.kiwoom_order_no == order_no,
                        Order.user_id == self.user_id,   # 유저 스코프로 격리
                    )
                )
            ).scalar_one_or_none()
            if not order:
                return

            order.filled_price = price
            order.filled_qty += qty
            if order.filled_qty >= order.order_qty:
                order.status = OrderStatus.FILLED

            position = None
            if order.position_id:
                position = (
                    await db.execute(select(Position).where(Position.id == order.position_id))
                ).scalar_one_or_none()
            if position is None:
                position = (
                    await db.execute(
                        select(Position).where(
                            Position.user_id == order.user_id,
                            Position.stock_code == order.stock_code,
                            Position.status == PositionStatus.ACTIVE,
                        )
                    )
                ).scalar_one_or_none()
            if position is None and order.order_type == OrderType.BUY:
                position = Position(
                    user_id=order.user_id,
                    stock_code=order.stock_code,
                    stock_name=order.stock_name or "",
                    buy_rounds_done=0,
                    sell_rounds_done=0,
                    quantity=0,
                    avg_buy_price=0,
                    total_buy_amount=0,
                )
                db.add(position)
                await db.flush()
            if position is None:
                await db.commit()
                return
            order.position_id = position.id

            if order.order_type == OrderType.BUY:
                position.quantity += qty
                position.total_buy_amount += price * qty
                position.avg_buy_price = (
                    position.total_buy_amount / position.quantity
                    if position.quantity > 0 else 0
                )
                if order.order_round > 0 and order.status == OrderStatus.FILLED:
                    if position.buy_rounds_done < order.order_round:
                        position.buy_rounds_done = order.order_round
                if order.order_round == 0 and order.status == OrderStatus.FILLED:
                    position.extra_buy_rounds += 1
                # 새 포지션이 생겼으면 실시간 구독 추가 (기존 포지션은 이미 구독 중)
                await self.subscribe_price(position.stock_code)
            else:  # SELL
                position.quantity = max(0, position.quantity - qty)
                if order.order_round > 0 and order.status == OrderStatus.FILLED:
                    if position.sell_rounds_done < order.order_round:
                        position.sell_rounds_done = order.order_round
                    if order.sell_trigger_bit is not None:
                        position.sold_triggers = (
                            position.sold_triggers | (1 << order.sell_trigger_bit)
                        )
                    if order.order_round == 3:
                        position.extra_buy_low = price
                if position.quantity <= 0 or position.sell_rounds_done >= 5:
                    position.status = PositionStatus.CLOSED
                    position.closed_at = datetime.utcnow()
                    await self.unsubscribe_price(order.stock_code)

            await db.commit()

    async def _apply_cancel(self, order_no: str, state: str) -> None:
        async with AsyncSessionLocal() as db:
            order = (
                await db.execute(
                    select(Order).where(
                        Order.kiwoom_order_no == order_no,
                        Order.user_id == self.user_id,
                    )
                )
            ).scalar_one_or_none()
            if not order:
                return
            order.status = OrderStatus.CANCELLED
            await db.commit()
            print(f"[kiwoom_ws user={self.user_id}] 주문 {state}: ord_no={order_no} ({order.stock_code})")

    async def _on_balance_event(self, item: dict) -> None:
        values = item.get("values", {}) or {}
        await manager.broadcast("balance_event", {
            "user_id": self.user_id,
            "code": values.get("9001", ""),
            "name": values.get("302", ""),
            "quantity": to_int(values.get("930")),
            "avg_price": to_int(values.get("931")),
            "current_price": abs(to_int(values.get("10"))),
            "profit_rate": float(values.get("8019") or 0),
        })


class KiwoomPool:
    """유저별 UserKiwoomWS 관리 + 글로벌 MA 캐시"""

    def __init__(self) -> None:
        self._connections: dict[int, UserKiwoomWS] = {}
        self._ma_cache: dict[str, dict[int, float]] = {}

    # ------------------------------------------------------------------ #
    #  lifecycle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        """서버 부팅 시 호출 — 키 등록된 모든 트레이딩 유저의 WS 시작."""
        # 순환 import 방지: 런타임 import
        from app.core.kiwoom_client import get_or_create_user_client
        from app.db.user_config import list_trading_users, get_trading_config

        async with AsyncSessionLocal() as db:
            users = await list_trading_users(db)
            for u in users:
                cfg = await get_trading_config(db, u.id)
                if cfg is None or not cfg.kiwoom_app_key or not cfg.kiwoom_secret_key:
                    continue
                client = get_or_create_user_client(u.id, cfg)
                await self.connect_user(u.id, client)

    async def stop(self) -> None:
        for conn in list(self._connections.values()):
            try:
                await conn.stop()
            except Exception as e:
                print(f"[kiwoom_pool] stop 오류 (user={conn.user_id}): {e}")
        self._connections.clear()

    # ------------------------------------------------------------------ #
    #  유저 단위 조작
    # ------------------------------------------------------------------ #
    async def connect_user(self, user_id: int, client: KiwoomClient) -> None:
        """유저 WS 시작. 이미 있으면 교체 (키 변경 시 재연결)."""
        existing = self._connections.get(user_id)
        if existing is not None:
            await existing.stop()
        conn = UserKiwoomWS(self, user_id, client)
        self._connections[user_id] = conn
        await conn.start()

    async def disconnect_user(self, user_id: int) -> None:
        conn = self._connections.pop(user_id, None)
        if conn is not None:
            await conn.stop()

    async def subscribe_price(self, user_id: int, stock_code: str) -> None:
        conn = self._connections.get(user_id)
        if conn is not None:
            await conn.subscribe_price(stock_code)

    # ------------------------------------------------------------------ #
    #  글로벌 MA 캐시 (시장 데이터, 모든 유저 공용)
    # ------------------------------------------------------------------ #
    def update_ma(self, code: str, ma_values: dict[int, float]) -> None:
        self._ma_cache[code] = dict(ma_values)

    def get_ma(self, code: str) -> dict[int, float]:
        return self._ma_cache.get(code, {})


kiwoom_pool = KiwoomPool()
