"""
키움증권 WebSocket 클라이언트 — 실시간 시세 수신 및 매도 신호 처리
"""
import json
import asyncio
import websockets
from sqlalchemy import select

from app.config import settings
from app.core.kiwoom_client import get_kiwoom_client
from app.db.database import AsyncSessionLocal
from app.db.models import Position, PositionStatus
from app.strategy.signal import check_sell_signal, calculate_ma20, check_extra_buy_signal
from app.strategy.executor import execute_sell_order
from app.ws.manager import manager


class KiwoomWebSocket:
    def __init__(self):
        self._ws = None
        self._subscribed: set[str] = set()
        self._running = False
        self._prices: dict[str, int] = {}          # code → current price
        self._ma20_cache: dict[str, float] = {}    # code → MA20

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
                print(f"[kiwoom_ws] 연결 오류: {e}, 5초 후 재연결")
                await asyncio.sleep(5)

    async def _run(self):
        client = get_kiwoom_client()
        token = await client.get_token()

        async with websockets.connect(
            settings.KIWOOM_WS_URL,
            extra_headers={
                "Authorization": f"Bearer {token}",
                "appkey": settings.KIWOOM_APP_KEY,
                "secretkey": settings.KIWOOM_SECRET_KEY,
            },
        ) as ws:
            self._ws = ws
            print("[kiwoom_ws] WebSocket 연결됨")

            # 보유 종목 구독
            await self._subscribe_active_positions()

            async for raw in ws:
                await self._handle_message(raw)

    async def _subscribe_active_positions(self):
        """보유 중인 종목 실시간 시세 구독"""
        async with AsyncSessionLocal() as db:
            stmt = select(Position).where(Position.status == PositionStatus.ACTIVE)
            positions = (await db.execute(stmt)).scalars().all()

        for pos in positions:
            await self.subscribe(pos.stock_code)

    async def subscribe(self, stock_code: str):
        if stock_code in self._subscribed or not self._ws:
            return
        msg = {
            "header": {
                "appkey": settings.KIWOOM_APP_KEY,
                "secretkey": settings.KIWOOM_SECRET_KEY,
                "tr_type": "1",   # 1=등록
            },
            "body": {
                "tr_id": "H0STCNT0",        # 실시간 체결가
                "tr_key": stock_code,
            },
        }
        await self._ws.send(json.dumps(msg))
        self._subscribed.add(stock_code)

    async def unsubscribe(self, stock_code: str):
        if stock_code not in self._subscribed or not self._ws:
            return
        msg = {
            "header": {
                "appkey": settings.KIWOOM_APP_KEY,
                "secretkey": settings.KIWOOM_SECRET_KEY,
                "tr_type": "2",   # 2=해제
            },
            "body": {
                "tr_id": "H0STCNT0",
                "tr_key": stock_code,
            },
        }
        await self._ws.send(json.dumps(msg))
        self._subscribed.discard(stock_code)

    async def _handle_message(self, raw: str):
        try:
            data = json.loads(raw)
        except Exception:
            return

        tr_id = data.get("header", {}).get("tr_id", "")

        if tr_id == "H0STCNT0":
            await self._on_price_update(data.get("body", {}))
        elif tr_id == "H0STCNI0":
            await self._on_order_filled(data.get("body", {}))

    async def _on_price_update(self, body: dict):
        code = body.get("MKSC_SHRN_ISCD", "")
        price_str = body.get("STCK_PRPR", "0")
        current_price = int(price_str.replace(",", ""))

        if not code or current_price <= 0:
            return

        self._prices[code] = current_price

        # 프론트엔드로 실시간 시세 브로드캐스트
        await manager.broadcast("price_update", {
            "code": code,
            "current_price": current_price,
            "change_rate": float(body.get("PRDY_CTRT", 0)),
        })

        # 매도 신호 확인
        await self._check_and_execute_sell(code, current_price)

    async def _check_and_execute_sell(self, code: str, current_price: int):
        async with AsyncSessionLocal() as db:
            stmt = select(Position).where(
                Position.stock_code == code,
                Position.status == PositionStatus.ACTIVE,
            )
            position = (await db.execute(stmt)).scalar_one_or_none()
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
                    "gain_rate": round((current_price - position.avg_buy_price) / position.avg_buy_price * 100, 2),
                })
                await execute_sell_order(db, position, sell_round, current_price)

            # 추가 매수 신호 확인
            if check_extra_buy_signal(current_price, position):
                await manager.broadcast("extra_buy_signal", {
                    "code": code,
                    "current_price": current_price,
                })

    async def _on_order_filled(self, body: dict):
        """체결 이벤트 처리"""
        await manager.broadcast("order_filled", {
            "code": body.get("MKSC_SHRN_ISCD", ""),
            "order_no": body.get("ODNO", ""),
            "filled_qty": body.get("CNTG_QTY", 0),
            "filled_price": body.get("CNTG_UNPR", 0),
        })

    def update_ma20(self, code: str, ma20: float):
        self._ma20_cache[code] = ma20


kiwoom_ws = KiwoomWebSocket()
