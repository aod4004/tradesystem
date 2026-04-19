"""
키움증권 WebSocket 클라이언트 — 실시간 시세(0B) 및 주문체결(00) 수신

- URL: wss://api.kiwoom.com:10000/api/dostk/websocket  (실거래)
       wss://mockapi.kiwoom.com:10000/api/dostk/websocket  (모의)
- 인증: HTTP upgrade 헤더에 authorization: Bearer {token}
- 구독 메시지:
    {"trnm": "REG", "grp_no": "1", "refresh": "1",
     "data": [{"item": ["005930"], "type": ["0B"]}]}
- 수신 메시지: {"trnm": "REAL", "data": [{"type":"0B","item":"005930","values":{"10":"...",...}}]}

주식체결(0B) 필드 코드:
    10=현재가, 11=전일대비, 12=등락율, 13=누적거래량, 15=거래량, 16=시가, 17=고가, 18=저가, 20=체결시간

주문체결(00) 필드 코드:
    9001=종목코드, 302=종목명, 913=주문상태(접수/체결/확인/취소/거부),
    907=매도수구분(1매도/2매수), 900=주문수량, 901=주문가격,
    910=체결가, 911=체결량, 9203=주문번호
"""
import json
import asyncio
import websockets
from sqlalchemy import select

from app.config import settings
from app.core.kiwoom_client import get_kiwoom_client, to_int
from app.db.database import AsyncSessionLocal
from app.db.models import Position, Order, OrderType, PositionStatus, OrderStatus
from app.strategy.signal import check_sell_signal, check_extra_buy_signal
from app.strategy.executor import execute_sell_order, execute_extra_buy_order
from app.ws.manager import manager


class KiwoomWebSocket:
    def __init__(self):
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._subscribed: set[str] = set()
        self._running = False
        self._ma20_cache: dict[str, float] = {}

    async def start(self):
        self._running = True
        asyncio.create_task(self._connect_loop())

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _connect_loop(self):
        while self._running:
            try:
                await self._run()
            except Exception as e:
                print(f"[kiwoom_ws] 연결 오류: {e} — 5초 후 재연결")
                await asyncio.sleep(5)

    async def _run(self):
        token = await get_kiwoom_client().get_token()
        headers = {"authorization": f"Bearer {token}"}

        async with websockets.connect(
            settings.KIWOOM_WS_URL,
            extra_headers=headers,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            self._ws = ws
            print(f"[kiwoom_ws] 연결됨 — {settings.KIWOOM_WS_URL}")

            # 주문체결(00) + 잔고(04) — 토큰 귀속, item 불필요
            await self._send({
                "trnm": "REG",
                "grp_no": "1",
                "refresh": "1",
                "data": [{"item": [""], "type": ["00", "04"]}],
            })

            # 보유 종목 실시간 체결(0B) 구독
            await self._subscribe_active_positions()

            async for raw in ws:
                await self._handle_message(raw)

    async def _send(self, msg: dict):
        if self._ws:
            await self._ws.send(json.dumps(msg))

    async def _subscribe_active_positions(self):
        async with AsyncSessionLocal() as db:
            positions = (
                await db.execute(
                    select(Position).where(Position.status == PositionStatus.ACTIVE)
                )
            ).scalars().all()
        for pos in positions:
            await self.subscribe_price(pos.stock_code)

    async def subscribe_price(self, stock_code: str):
        if stock_code in self._subscribed or not self._ws:
            return
        await self._send({
            "trnm": "REG",
            "grp_no": "2",
            "refresh": "1",
            "data": [{"item": [stock_code], "type": ["0B"]}],
        })
        self._subscribed.add(stock_code)

    async def unsubscribe_price(self, stock_code: str):
        if stock_code not in self._subscribed or not self._ws:
            return
        await self._send({
            "trnm": "REMOVE",
            "grp_no": "2",
            "data": [{"item": [stock_code], "type": ["0B"]}],
        })
        self._subscribed.discard(stock_code)

    # ------------------------------------------------------------------ #
    #  수신 처리
    # ------------------------------------------------------------------ #
    async def _handle_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except Exception:
            return

        trnm = msg.get("trnm")
        if trnm != "REAL":
            # REG/REMOVE 응답은 로그만
            if msg.get("return_code", 0) != 0:
                print(f"[kiwoom_ws] {trnm} 응답 오류: {msg.get('return_msg')}")
            return

        for item in msg.get("data", []) or []:
            t = item.get("type")
            if t == "0B":
                await self._on_price(item)
            elif t == "00":
                await self._on_order_event(item)
            elif t == "04":
                await self._on_balance_event(item)

    async def _on_price(self, item: dict):
        """주식체결(0B)"""
        code = item.get("item", "")
        values = item.get("values", {}) or {}
        current_price = abs(to_int(values.get("10")))   # 부호는 전일대비 방향
        if not code or current_price <= 0:
            return

        await manager.broadcast("price_update", {
            "code": code,
            "current_price": current_price,
            "change_rate": float(values.get("12") or 0),
        })

        await self._check_and_execute_sell(code, current_price)

    async def _check_and_execute_sell(self, code: str, current_price: int):
        async with AsyncSessionLocal() as db:
            position = (
                await db.execute(
                    select(Position).where(
                        Position.stock_code == code,
                        Position.status == PositionStatus.ACTIVE,
                    )
                )
            ).scalar_one_or_none()
            if not position:
                return

            ma20 = self._ma20_cache.get(code)
            sell_round = check_sell_signal(
                current_price, position.avg_buy_price,
                position.sell_rounds_done, ma20,
            )
            if sell_round:
                await manager.broadcast("sell_signal", {
                    "code": code,
                    "sell_round": sell_round,
                    "current_price": current_price,
                    "avg_buy_price": position.avg_buy_price,
                    "gain_rate": round(
                        (current_price - position.avg_buy_price) / position.avg_buy_price * 100, 2
                    ),
                })
                await execute_sell_order(db, position, sell_round, current_price)

            if check_extra_buy_signal(current_price, position):
                await manager.broadcast("extra_buy_signal", {
                    "code": code,
                    "current_price": current_price,
                })
                await execute_extra_buy_order(db, position, current_price)

    async def _on_order_event(self, item: dict):
        """
        주문체결(00) — 접수/체결/확인/취소/거부 이벤트
        필드: 9001(종목), 9203(주문번호), 913(상태), 905(주문구분),
             910(체결가), 911(체결량), 900(주문수량), 901(주문가)
        """
        values = item.get("values", {}) or {}
        order_no = values.get("9203", "")
        state = values.get("913", "")
        filled_price = abs(to_int(values.get("910")))
        filled_qty = to_int(values.get("911"))

        await manager.broadcast("order_event", {
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

    async def _apply_fill(self, order_no: str, price: int, qty: int):
        """체결 이벤트 — Order 갱신 + Position 갱신(생성 포함)"""
        if qty <= 0:
            return
        async with AsyncSessionLocal() as db:
            order = (
                await db.execute(select(Order).where(Order.kiwoom_order_no == order_no))
            ).scalar_one_or_none()
            if not order:
                return

            # 주문 체결 누적
            order.filled_price = price
            order.filled_qty += qty
            if order.filled_qty >= order.order_qty:
                order.status = OrderStatus.FILLED

            # Position 갱신
            position = None
            if order.position_id:
                position = (
                    await db.execute(select(Position).where(Position.id == order.position_id))
                ).scalar_one_or_none()

            if position is None:
                position = (
                    await db.execute(
                        select(Position).where(
                            Position.stock_code == order.stock_code,
                            Position.status == PositionStatus.ACTIVE,
                        )
                    )
                ).scalar_one_or_none()

            if position is None and order.order_type == OrderType.BUY:
                position = Position(
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
                # 완전 체결된 정규 차수(1~5) 매수면 차수 갱신
                if order.order_round > 0 and order.status == OrderStatus.FILLED:
                    if position.buy_rounds_done < order.order_round:
                        position.buy_rounds_done = order.order_round
                # 추가 매수(order_round=0)는 extra_buy_rounds 로 관리
                if order.order_round == 0 and order.status == OrderStatus.FILLED:
                    position.extra_buy_rounds += 1
            else:  # SELL
                position.quantity = max(0, position.quantity - qty)
                if order.order_round > 0 and order.status == OrderStatus.FILLED:
                    if position.sell_rounds_done < order.order_round:
                        position.sell_rounds_done = order.order_round
                    # 3차 매도 완료 시 추가매수 기준 저점 기록
                    if order.order_round == 3:
                        position.extra_buy_low = price
                if position.quantity <= 0 or position.sell_rounds_done >= 5:
                    position.status = PositionStatus.CLOSED
                    from datetime import datetime
                    position.closed_at = datetime.utcnow()
                    # 청산된 종목은 실시간 구독 해제
                    await self.unsubscribe_price(order.stock_code)

            await db.commit()

    async def _apply_cancel(self, order_no: str, state: str):
        """주문 취소/거부 — Order만 CANCELLED 처리 (Position은 체결분만 반영되어 있어 롤백 불필요)"""
        async with AsyncSessionLocal() as db:
            order = (
                await db.execute(select(Order).where(Order.kiwoom_order_no == order_no))
            ).scalar_one_or_none()
            if not order:
                return
            order.status = OrderStatus.CANCELLED
            await db.commit()
            print(f"[kiwoom_ws] 주문 {state}: ord_no={order_no} ({order.stock_code})")

    async def _on_balance_event(self, item: dict):
        """잔고(04) — 체결로 잔고가 변동되면 수신"""
        values = item.get("values", {}) or {}
        await manager.broadcast("balance_event", {
            "code": values.get("9001", ""),
            "name": values.get("302", ""),
            "quantity": to_int(values.get("930")),
            "avg_price": to_int(values.get("931")),
            "current_price": abs(to_int(values.get("10"))),
            "profit_rate": float(values.get("8019") or 0),
        })

    def update_ma20(self, code: str, ma20: float):
        self._ma20_cache[code] = ma20


kiwoom_ws = KiwoomWebSocket()
