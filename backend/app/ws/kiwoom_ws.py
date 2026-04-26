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

from app.config import settings
from app.core.kiwoom_client import KiwoomClient, to_int
from app.core.notifier import notify_admins_fire, notify_user_fire
from app.db.database import AsyncSessionLocal
from app.db.models import Position, Order, OrderType, PositionStatus, OrderStatus
from app.strategy.signal import check_sell_signal, check_extra_buy_signal
from app.strategy.executor import execute_sell_order, execute_extra_buy_order
from app.strategy.ma20 import compute_and_cache_ma
from app.ws.manager import manager


# 재연결 백오프 — 일반 오류는 짧게(네트워크 일시단절은 빨리 복구).
# 인증 실패는 길게(8005 등 토큰/키 무효는 즉시 재시도해도 같은 결과).
_RECONNECT_BACKOFF_LADDER = (5, 15, 45, 120, 300)
_AUTH_FAIL_BACKOFF_LADDER = (60, 120, 300, 600, 600)
# 연속 LOGIN 인증 실패 N회 누적 시 영구 정지 — 키 재등록(PUT /kiwoom-keys) 전엔
# 자동 재시작 안 함. 키움 콘솔에서 키 재발급이 필요한 상황의 무한 시도 방지.
_AUTH_FAILURE_HARD_STOP_THRESHOLD = 5


class _AuthFailure(RuntimeError):
    """LOGIN return_code != 0 — 키/토큰 무효 가능성. 일반 예외와 분리해 백오프/dedup 차별화."""


def _sell_trigger_label(bit: int) -> str:
    """Position.sold_triggers / Order.sell_trigger_bit 비트를 사람이 읽을 라벨로."""
    n_ratios = len(settings.SELL_RATIOS)
    if 0 <= bit < n_ratios:
        pct = int(settings.SELL_RATIOS[bit] * 100)
        return f"수익률 +{pct}%"
    ma_idx = bit - n_ratios
    if 0 <= ma_idx < len(settings.SELL_MA_PERIODS):
        return f"MA{settings.SELL_MA_PERIODS[ma_idx]} 터치"
    return f"trigger bit {bit}"


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
        self.authenticated = False   # LOGIN 응답 OK 후 True. 재연결 시 False 로 리셋.
        # 조건검색 request-response — 메시지 루프가 응답을 가로채므로 pending Future 로 라우팅.
        # 동일 커넥션에서 동시에 하나만 요청 가능하도록 lock 으로 직렬화.
        self._req_lock = asyncio.Lock()
        self._pending_cnsrlst: asyncio.Future | None = None
        self._pending_cnsrreq: asyncio.Future | None = None
        # 관측성 — health 에 노출 + CNSRREQ 타임아웃 원인 진단용.
        self.last_recv_at: datetime | None = None
        self.last_send_at: datetime | None = None
        self.recv_count = 0
        self.send_count = 0
        self.last_connected_at: datetime | None = None
        # 인증 실패 누적/영구정지 — _AUTH_FAILURE_HARD_STOP_THRESHOLD 회 누적 시
        # _running=False 로 루프 종료. 키 재등록(connect_user) 시 새 인스턴스로 자동 재시작.
        self.auth_failure_count: int = 0
        self.last_auth_error: str | None = None
        self.permanently_stopped: bool = False

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
        # 알림 dedup TTL — 백오프(60s+) 가 dedup TTL(60s 기본) 보다 길면 같은 키가
        # 만료된 직후 또 발송되므로 24시간으로 명시. 날짜 prefix 와 결합되어
        # 사실상 "하루 1회" 보장.
        DEDUP_TTL_24H = 86400
        while self._running:
            had_error = False
            is_auth_failure = False
            err_msg: str = ""
            try:
                await self._run()
            except asyncio.CancelledError:
                break
            except _AuthFailure as e:
                had_error = True
                is_auth_failure = True
                err_msg = str(e)
                self.auth_failure_count += 1
                self.last_auth_error = err_msg
            except Exception as e:
                had_error = True
                err_msg = f"{type(e).__name__}: {e}"

            if not self._running:
                break

            # _run() 정상 종료 (예외 없이 ws 가 close 된 경우) — 즉시 다음 사이클.
            # 백오프/알림 없음. 기존 동작 보존.
            if not had_error:
                continue

            today = datetime.utcnow().strftime("%Y-%m-%d")

            # 영구 정지 — 연속 인증 실패 임계 누적 시 루프 종료.
            # 키 재등록(PUT /kiwoom-keys → kiwoom_pool.connect_user) 이 새 인스턴스로 자동 재시작.
            if self.auth_failure_count >= _AUTH_FAILURE_HARD_STOP_THRESHOLD:
                self.permanently_stopped = True
                self._running = False
                print(
                    f"[kiwoom_ws user={self.user_id}] 인증 실패 "
                    f"{self.auth_failure_count}회 누적 — WS 루프 영구 정지. 키 재등록 필요."
                )
                notify_user_fire(
                    self.user_id,
                    "🛑 키움 WS 자동 재시도 중단\n"
                    f"인증 실패 {self.auth_failure_count}회 누적 — 키 재등록이 필요합니다.\n"
                    "설정 → 키움 키에서 새 키를 저장하면 자동으로 재시작됩니다.",
                    dedup_key=f"ws_login_giveup:{self.user_id}:{today}",
                    dedup_ttl=DEDUP_TTL_24H,
                )
                return

            # 백오프 산정 — 인증 실패는 누적 카운터 기반 사다리, 일반 오류는 5초 고정.
            if is_auth_failure:
                idx = min(self.auth_failure_count - 1, len(_AUTH_FAIL_BACKOFF_LADDER) - 1)
                delay = _AUTH_FAIL_BACKOFF_LADDER[idx]
                print(
                    f"[kiwoom_ws user={self.user_id}] LOGIN 인증 실패 "
                    f"({self.auth_failure_count}/{_AUTH_FAILURE_HARD_STOP_THRESHOLD}) "
                    f"— {delay}초 후 재시도: {err_msg}"
                )
                notify_user_fire(
                    self.user_id,
                    "⚠️ 키움 실시간 인증 실패\n"
                    f"{err_msg}\n"
                    f"누적 {self.auth_failure_count}회 — {delay}초 후 재시도합니다.\n"
                    "재시도가 계속 실패하면 키 재등록이 필요할 수 있습니다.\n"
                    "(설정 → 키움 키)",
                    dedup_key=f"ws_login_failed:{self.user_id}:{today}",
                    dedup_ttl=DEDUP_TTL_24H,
                )
            else:
                delay = _RECONNECT_BACKOFF_LADDER[0]
                print(f"[kiwoom_ws user={self.user_id}] 연결 오류 — {delay}초 후 재연결: {err_msg}")
                notify_user_fire(
                    self.user_id,
                    f"⚠️ 키움 실시간 연결 오류\n{err_msg}\n{delay}초 후 재연결 시도합니다.",
                    dedup_key=f"ws_error:{self.user_id}:{today}",
                    dedup_ttl=DEDUP_TTL_24H,
                )

            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break

    async def _login_once(self, ws) -> None:
        """현재 캐시 토큰으로 LOGIN 1회 시도. 응답 OK 까지 대기.
        return_code != 0 이면 _AuthFailure 던짐 — 호출부에서 토큰 무효화 후 재시도하거나
        그대로 _connect_loop 까지 전파해 백오프/카운터 누적."""
        token = await self.client.get_token()
        await self._send({"trnm": "LOGIN", "token": token})
        while True:
            raw = await ws.recv()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("trnm") != "LOGIN":
                continue
            rc = msg.get("return_code", 0)
            if rc != 0:
                raise _AuthFailure(
                    f"키움 WS 로그인 실패: rc={rc} {msg.get('return_msg')}"
                )
            return

    async def _run(self) -> None:
        self.authenticated = False
        # 재연결 시 이전 커넥션의 구독 목록을 비워야 _subscribe_active_positions 가
        # subscribe_price 의 "이미 구독됨" 스킵 로직에 막히지 않는다. 새 WS 는 구독 상태가 없음.
        self._subscribed.clear()
        # 재연결로 이전 커넥션 위의 pending 요청은 어차피 응답 못 받으니 즉시 실패 처리.
        for fut_attr in ("_pending_cnsrreq", "_pending_cnsrlst"):
            fut = getattr(self, fut_attr, None)
            if fut is not None and not fut.done():
                fut.set_exception(RuntimeError("키움 WS 재연결로 요청 취소됨"))
        async with websockets.connect(
            self.client.ws_url,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            self._ws = ws
            print(f"[kiwoom_ws user={self.user_id}] 연결됨 — {self.client.ws_url}")

            # 1) 로그인 전문 — 키움 WS 는 접속 직후 LOGIN 으로 인증해야 REG 가능.
            #    authorization 헤더가 아니라 body 의 token 필드로 인증한다.
            #    1차 실패 시 stale 캐시 가능성 → 토큰 무효화 후 1회 재시도.
            try:
                await self._login_once(ws)
            except _AuthFailure as e:
                print(
                    f"[kiwoom_ws user={self.user_id}] LOGIN 실패 — "
                    f"토큰 캐시 무효화 후 재시도: {e}"
                )
                await self.client.invalidate_token()
                await self._login_once(ws)   # 2차 실패면 _AuthFailure 그대로 전파
            self.authenticated = True
            self.last_connected_at = datetime.utcnow()
            self.auth_failure_count = 0
            self.last_auth_error = None
            print(f"[kiwoom_ws user={self.user_id}] 로그인 성공")

            # 2) 주문체결(00) + 잔고(04) — 토큰 귀속, item 불필요
            await self._send({
                "trnm": "REG", "grp_no": "1", "refresh": "1",
                "data": [{"item": [""], "type": ["00", "04"]}],
            })

            # 3) 이 유저의 보유 종목 실시간 체결(0B) 구독
            await self._subscribe_active_positions()

            # 4) 조건검색 priming — 키움 모의 WS 는 세션당 CNSRLST 를 최소 1회
            #    수신해야 같은 세션의 CNSRREQ 가 응답하는 것으로 관측.
            #    메시지 루프가 돌아야 응답 처리 가능하므로 background task 로 분리.
            asyncio.create_task(self._prime_condition_search())

            async for raw in ws:
                await self._handle_message(raw)

    async def _prime_condition_search(self) -> None:
        """재연결 시마다 1회 CNSRLST 호출해 조건검색 응답 경로를 활성화."""
        try:
            await asyncio.sleep(0.5)   # 메시지 루프 진입 대기
            items = await self.condition_list(timeout=10.0)
            print(f"[kiwoom_ws user={self.user_id}] 조건검색 priming — {len(items)}개 조건식 수신")
        except Exception as e:
            # priming 실패해도 다른 기능엔 영향 없음. 로그만 남김.
            print(f"[kiwoom_ws user={self.user_id}] 조건검색 priming 실패: {type(e).__name__}: {e}")

    async def _send(self, msg: dict) -> None:
        if self._ws is not None:
            await self._ws.send(json.dumps(msg))
            self.last_send_at = datetime.utcnow()
            self.send_count += 1

    # 키움 WS 는 TRNM=REG 에 rate limit 이 있다 (rc=105110: 허용 요청 건수 초과).
    # 종목당 REG 를 개별로 보내면 20+개 포지션에서 바로 limit 에 걸려 뒤따르는
    # CNSRREQ 등 다른 전문까지 응답 유실. data.item 에 여러 종목을 한 번에
    # 실어 한 번의 REG 로 모두 구독하는 게 안전.
    _REG_BATCH_SIZE = 50

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
        codes = [p.stock_code for p in positions if p.stock_code and p.stock_code not in self._subscribed]
        if not codes:
            return
        for i in range(0, len(codes), self._REG_BATCH_SIZE):
            chunk = codes[i:i + self._REG_BATCH_SIZE]
            await self._send({
                "trnm": "REG", "grp_no": "2", "refresh": "1",
                "data": [{"item": chunk, "type": ["0B"]}],
            })
            self._subscribed.update(chunk)

    async def subscribe_price(self, stock_code: str) -> None:
        """신규 1종목 구독 (체결 이벤트로 새 포지션 생겼을 때 호출).
        여러 종목 동시 구독이 필요하면 _subscribe_active_positions 가 배치로 처리."""
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

        self.last_recv_at = datetime.utcnow()
        self.recv_count += 1

        trnm = msg.get("trnm")
        # 키움 WS 는 application-level PING 을 주기적으로 보낸다 — 그대로 echo 해야 끊기지 않음.
        if trnm == "PING":
            await self._send(msg)
            return
        # 조건검색 응답 — 요청한 쪽의 Future 에 전달
        if trnm == "CNSRLST":
            fut = self._pending_cnsrlst
            if fut is not None and not fut.done():
                fut.set_result(msg)
            return
        if trnm == "CNSRREQ":
            fut = self._pending_cnsrreq
            if fut is not None and not fut.done():
                fut.set_result(msg)
            return
        if trnm != "REAL":
            rc = msg.get("return_code", 0)
            if rc != 0:
                print(f"[kiwoom_ws user={self.user_id}] {trnm} 응답 오류: rc={rc} {msg.get('return_msg')}")
                # pending 조건검색 요청이 있는데 return_code 가 다른 trnm 으로 실려오는 경우
                # (키움이 일관되게 trnm 을 맞춰 보내지 않는 케이스 방어). future 를 실패시켜 20초 대기 안 하도록.
                if self._pending_cnsrreq is not None and not self._pending_cnsrreq.done():
                    self._pending_cnsrreq.set_exception(
                        RuntimeError(f"CNSRREQ 요청 중 이상 응답 (trnm={trnm} rc={rc} {msg.get('return_msg')})")
                    )
                if self._pending_cnsrlst is not None and not self._pending_cnsrlst.done():
                    self._pending_cnsrlst.set_exception(
                        RuntimeError(f"CNSRLST 요청 중 이상 응답 (trnm={trnm} rc={rc} {msg.get('return_msg')})")
                    )
            elif trnm not in ("REG", "REMOVE"):
                # 미처리 trnm + 성공(rc=0) — CNSRREQ 타임아웃 원인 추적용 진단 로그.
                # REG/REMOVE 성공 응답은 정상 케이스라 제외.
                snippet = (raw[:400]).replace("\n", " ")
                print(f"[kiwoom_ws user={self.user_id}] rx unhandled trnm={trnm} rc=0 raw={snippet}")
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
                    gain_rate = round(
                        (current_price - position.avg_buy_price) / position.avg_buy_price * 100, 2
                    )
                    await manager.broadcast("sell_signal", {
                        "user_id": self.user_id,
                        "code": code,
                        "sell_round": sell_round,
                        "trigger_bit": trigger_bit,
                        "current_price": current_price,
                        "avg_buy_price": position.avg_buy_price,
                        "gain_rate": gain_rate,
                    })
                    trigger_label = _sell_trigger_label(trigger_bit)
                    notify_user_fire(
                        self.user_id,
                        f"🎯 매도 조건 도달\n{position.stock_name} ({code})\n"
                        f"{trigger_label} · 현재가 {current_price:,}원 ({gain_rate:+.2f}%)",
                        dedup_key=f"sell_trigger:{position.id}:{trigger_bit}",
                    )
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
            is_new_position = False
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
                is_new_position = True
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
                # 새 포지션이 생겼으면 실시간 구독 추가 (기존 포지션은 이미 구독 중 — subscribe_price 는 멱등)
                await self.subscribe_price(position.stock_code)
                # 신규 포지션이면 MA 캐시 즉시 계산 — 08:30 배치는 매수 체결 이전 스냅샷이라 당일 매수 종목은 빠짐.
                # 동기 대기하면 체결 이벤트 응답이 느려지므로 태스크로 분리.
                if is_new_position:
                    asyncio.create_task(compute_and_cache_ma(position.stock_code, self.client))
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

            # 알림 — 완결된 체결이면 확정, 부분 체결이면 요약
            is_final = order.status == OrderStatus.FILLED
            stage = "체결 완료" if is_final else "부분 체결"
            if order.order_type == OrderType.BUY:
                lines = [
                    f"✅ 매수 {stage}",
                    f"{order.stock_name or order.stock_code} ({order.stock_code})",
                    f"{qty}주 @ {price:,}원",
                ]
                if position is not None:
                    lines.append(
                        f"현재 {position.quantity}주 · 평단 {position.avg_buy_price:,.0f}원"
                    )
            else:
                gain_rate = (
                    (price - position.avg_buy_price) / position.avg_buy_price * 100
                    if position and position.avg_buy_price > 0 else 0.0
                )
                trigger_label = _sell_trigger_label(order.sell_trigger_bit) if order.sell_trigger_bit is not None else ""
                lines = [
                    f"💰 매도 {stage}",
                    f"{order.stock_name or order.stock_code} ({order.stock_code})",
                    f"{qty}주 @ {price:,}원 ({gain_rate:+.2f}%)",
                ]
                if trigger_label:
                    lines.append(trigger_label)
                if position is not None and position.status == PositionStatus.CLOSED:
                    lines.append("포지션 청산 완료")
            notify_user_fire(
                self.user_id, "\n".join(lines),
                dedup_key=f"fill:{order_no}:{order.filled_qty}",
            )

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
            side = "매수" if order.order_type == OrderType.BUY else "매도"
            notify_user_fire(
                self.user_id,
                f"🚫 {side} 주문 {state}\n"
                f"{order.stock_name or order.stock_code} ({order.stock_code})\n"
                f"{order.order_qty}주 @ {order.order_price:,}원",
                dedup_key=f"cancel:{order_no}",
            )

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

    # ------------------------------------------------------------------ #
    #  조건검색 (영웅문4 에 저장된 조건식 목록/결과 조회)
    # ------------------------------------------------------------------ #
    async def condition_list(self, timeout: float = 10.0) -> list[tuple[str, str]]:
        """영웅문4 에 저장된 조건식 목록 조회 (CNSRLST).
        반환: [(seq, name), ...]
        """
        if not self.authenticated or self._ws is None:
            raise RuntimeError("키움 WS 인증 미완료 상태입니다")
        async with self._req_lock:
            loop = asyncio.get_event_loop()
            fut = loop.create_future()
            self._pending_cnsrlst = fut
            try:
                await self._send({"trnm": "CNSRLST"})
                msg = await asyncio.wait_for(fut, timeout=timeout)
            finally:
                self._pending_cnsrlst = None
        if msg.get("return_code", 0) != 0:
            raise RuntimeError(f"CNSRLST 실패: {msg.get('return_msg')}")
        out: list[tuple[str, str]] = []
        for item in msg.get("data") or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                out.append((str(item[0]), str(item[1])))
            elif isinstance(item, dict):
                out.append((str(item.get("seq", "")), str(item.get("name", ""))))
        return out

    async def condition_search(
        self,
        seq: str,
        *,
        stex_tp: str = "K",
        timeout: float = 30.0,
    ) -> list[dict]:
        """조건식 seq 로 일반(비실시간) 검색 (CNSRREQ).
        반환: [{'9001': code, '302': name, '10': price, ...}, ...]  — 키움 원본 필드.
        """
        if not self.authenticated or self._ws is None:
            raise RuntimeError("키움 WS 인증 미완료 상태 (authenticated=False 또는 소켓 없음)")
        last_recv_s = (datetime.utcnow() - self.last_recv_at).total_seconds() if self.last_recv_at else None
        print(
            f"[kiwoom_ws user={self.user_id}] CNSRREQ 전송 준비 — "
            f"authenticated={self.authenticated} recv={self.recv_count} send={self.send_count} "
            f"last_recv_before={last_recv_s}s"
        )
        async with self._req_lock:
            loop = asyncio.get_event_loop()
            fut = loop.create_future()
            self._pending_cnsrreq = fut
            try:
                await self._send({
                    "trnm": "CNSRREQ",
                    "seq": str(seq),
                    "search_type": "0",
                    "stex_tp": stex_tp,
                })
                try:
                    msg = await asyncio.wait_for(fut, timeout=timeout)
                except asyncio.TimeoutError:
                    recv_after = self.recv_count
                    raise RuntimeError(
                        f"CNSRREQ 응답 없음 — {timeout}초 타임아웃 "
                        f"(seq={seq}, user={self.user_id}, recv={recv_after}, "
                        f"last_recv_before={last_recv_s}s). 키움 WS 재연결 또는 조건식 유효성 확인 필요."
                    )
            finally:
                self._pending_cnsrreq = None
        if msg.get("return_code", 0) != 0:
            raise RuntimeError(f"CNSRREQ 실패 (seq={seq}): return_code={msg.get('return_code')} {msg.get('return_msg')}")
        return msg.get("data") or []


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
    #  조건검색 — 해당 유저의 인증된 WS 커넥션 위에서 수행
    # ------------------------------------------------------------------ #
    def get_connection(self, user_id: int) -> UserKiwoomWS | None:
        return self._connections.get(user_id)

    async def condition_list(self, user_id: int) -> list[tuple[str, str]]:
        conn = self._connections.get(user_id)
        if conn is None:
            raise RuntimeError(f"유저 {user_id} 의 키움 WS 커넥션이 없습니다")
        return await conn.condition_list()

    async def condition_search(self, user_id: int, seq: str) -> list[dict]:
        conn = self._connections.get(user_id)
        if conn is None:
            raise RuntimeError(f"유저 {user_id} 의 키움 WS 커넥션이 없습니다")
        return await conn.condition_search(seq)

    # ------------------------------------------------------------------ #
    #  글로벌 MA 캐시 (시장 데이터, 모든 유저 공용)
    # ------------------------------------------------------------------ #
    def update_ma(self, code: str, ma_values: dict[int, float]) -> None:
        self._ma_cache[code] = dict(ma_values)

    def get_ma(self, code: str) -> dict[int, float]:
        return self._ma_cache.get(code, {})


kiwoom_pool = KiwoomPool()
