"""
사전 매도 주문 등록 모듈.

매도 조건(평단×1.05/1.10/1.15/1.20 + MA20/60/120) 가격에 미리 지정가 매도 주문을
키움 호가창에 걸어두는 방식. 실시간 시세 tick 기반 매도 트리거를 대체한다.

호출 시점:
  1. 매일 08:35 (MA 캐시 직후) — 모든 active Position (job_plan_sell_orders)
  2. 매수 체결 직후 — 평단 변경된 단일 Position (UserKiwoomWS._schedule_sell_replan)

핵심 설계:
  - sell_rounds_done 의 의미를 popcount(sold_triggers) 로 변경. 한도 검사 정합성 확보.
  - target_price 는 호가단위 round. 평단 이하인 후보(예: MA 가격이 평단 아래)는 제외.
  - Position 별 asyncio.Lock 으로 동시 정비 방지 (분할 체결 폭주 보호).
  - 기존 SUBMITTED 매도(취소 가능 — sell_trigger_bit IS NOT NULL)는 모두 취소 후 재등록.
"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.core.kiwoom_client import KiwoomClient
from app.core.notifier import notify_user_fire
from app.db.models import Position, Order, OrderType, OrderStatus, PositionStatus
from app.strategy.tick_size import round_to_tick


# Position id → Lock. 같은 포지션의 plan 호출이 동시에 들어오면 직렬화한다.
_position_locks: dict[int, asyncio.Lock] = {}


def _get_position_lock(position_id: int) -> asyncio.Lock:
    lock = _position_locks.get(position_id)
    if lock is None:
        lock = asyncio.Lock()
        _position_locks[position_id] = lock
    return lock


def _popcount(n: int) -> int:
    return bin(n).count("1")


def calculate_sell_targets(
    avg_buy_price: float,
    sold_triggers: int,
    ma_values: dict[int, float | None],
) -> list[tuple[int, int]]:
    """반환: [(trigger_bit, target_price), ...] 가격 오름차순.

    조건:
      - sold_triggers 의 비트가 set 된 트리거는 제외
      - target_price 는 호가단위로 round (RC4003 회피)
      - target_price > avg_buy_price 만 포함 (평단 이하 매도는 손실이라 의미 없음)
      - 최대 (MAX_SELL_TRANCHES - popcount(sold_triggers)) 개

    비트 레이아웃 (Position.sold_triggers, Order.sell_trigger_bit 와 공유):
      비트 0~3 = 수익률 +5%/+10%/+15%/+20%
      비트 4~6 = MA20/MA60/MA120 터치
    """
    if avg_buy_price <= 0:
        return []

    candidates: list[tuple[int, int]] = []

    # 수익률 조건 — 비트 0~3
    for i, threshold in enumerate(settings.SELL_RATIOS):
        bit = i
        if sold_triggers & (1 << bit):
            continue
        target = round_to_tick(avg_buy_price * (1.0 + threshold))
        if target > avg_buy_price:
            candidates.append((bit, target))

    # MA 조건 — 비트 4+i
    n_ratios = len(settings.SELL_RATIOS)
    for i, period in enumerate(settings.SELL_MA_PERIODS):
        bit = n_ratios + i
        if sold_triggers & (1 << bit):
            continue
        ma = ma_values.get(period)
        if ma is None or ma <= 0:
            continue
        target = round_to_tick(ma)
        if target > avg_buy_price:
            candidates.append((bit, target))

    candidates.sort(key=lambda x: x[1])
    remaining_slots = settings.MAX_SELL_TRANCHES - _popcount(sold_triggers)
    if remaining_slots <= 0:
        return []
    return candidates[:remaining_slots]


async def plan_sell_orders_for_position(
    db: AsyncSession,
    position: Position,
    client: KiwoomClient,
    ma_values: dict[int, float | None],
) -> dict:
    """단일 Position 의 사전 매도 주문 정비.

    1. Position 별 lock 획득 (동시 정비 직렬화)
    2. DB 의 SUBMITTED 매도 주문(sell_trigger_bit 있음) 모두 키움 cancel API 호출
    3. 키움 가용수량 복원 대기 (300ms)
    4. 새 평단/MA 기준으로 후보 산출 (calculate_sell_targets)
    5. 각 후보를 지정가 매도 주문으로 등록 (DB Order + 키움 place_order)

    반환: {"cancelled": N, "registered": [(bit, price, qty), ...], "skipped": str | None}
    """
    lock = _get_position_lock(position.id)
    async with lock:
        # lock 사이 변경 가능성 — 최신 상태 재조회
        position = (
            await db.execute(select(Position).where(Position.id == position.id))
        ).scalar_one_or_none()
        if position is None or position.status != PositionStatus.ACTIVE:
            return {"cancelled": 0, "registered": [], "skipped": "position_inactive"}
        if position.quantity <= 0:
            return {"cancelled": 0, "registered": [], "skipped": "no_quantity"}

        # 1. 기존 사전 매도 주문 취소 (sell_trigger_bit 가 있는 SUBMITTED)
        existing = (
            await db.execute(
                select(Order).where(
                    Order.user_id == position.user_id,
                    Order.position_id == position.id,
                    Order.order_type == OrderType.SELL,
                    Order.status == OrderStatus.SUBMITTED,
                    Order.sell_trigger_bit.is_not(None),
                )
            )
        ).scalars().all()

        cancelled = 0
        for ord_row in existing:
            if not ord_row.kiwoom_order_no:
                # 주문번호 없는 SUBMITTED — 등록 자체가 실패했던 흔적. DB 만 정리.
                ord_row.status = OrderStatus.CANCELLED
                continue
            try:
                await client.cancel_order(
                    orig_order_no=ord_row.kiwoom_order_no,
                    stock_code=ord_row.stock_code,
                    cancel_qty=0,   # 0 = 잔량 전부
                )
                cancelled += 1
            except Exception as e:
                # 이미 체결됐거나 사라진 주문일 수 있음 — WS 체결/취소 이벤트가 처리.
                # 단 해당 매도가 살아있으면 재등록과 중복될 수 있음 → 일단 진행.
                print(
                    f"[sell_planner] user={position.user_id} {position.stock_code} "
                    f"취소 실패 (계속 진행) ord={ord_row.kiwoom_order_no}: {e}"
                )

        if cancelled > 0:
            await asyncio.sleep(0.3)   # 키움 가용수량 복원 대기

        # 2. 후보 산출
        targets = calculate_sell_targets(
            avg_buy_price=position.avg_buy_price,
            sold_triggers=position.sold_triggers,
            ma_values=ma_values,
        )
        if not targets:
            await db.commit()
            return {"cancelled": cancelled, "registered": [], "skipped": "no_targets"}

        # 3. 매도 1회 수량 = 보유 × 20%, 최소 1주
        sell_qty = max(1, round(position.quantity * settings.SELL_QUANTITY_RATIO))

        # 잔량 보호 — 5개 슬롯이라도 sell_qty × N > 보유 수량이면 키움이 800033 으로 거부.
        # 보유 수량 안에서 등록 가능한 최대 개수로 자른다.
        max_slots_by_qty = position.quantity // sell_qty if sell_qty > 0 else 0
        if max_slots_by_qty < len(targets):
            targets = targets[:max_slots_by_qty]
        if not targets:
            await db.commit()
            return {"cancelled": cancelled, "registered": [], "skipped": "qty_too_small"}

        # 4. 등록 — order_round 는 popcount + idx + 1 (가격 낮 → 높 순)
        base_round = _popcount(position.sold_triggers)
        registered: list[tuple[int, int, int]] = []
        for idx, (bit, target_price) in enumerate(targets):
            sell_round = base_round + idx + 1
            try:
                resp = await client.place_order(
                    stock_code=position.stock_code,
                    order_type="sell",
                    quantity=sell_qty,
                    price=target_price,
                    trade_type="0",
                )
                order_no = resp.get("ord_no", "")
                order = Order(
                    user_id=position.user_id,
                    position_id=position.id,
                    stock_code=position.stock_code,
                    stock_name=position.stock_name,
                    order_type=OrderType.SELL,
                    order_round=sell_round,
                    sell_trigger_bit=bit,
                    order_price=target_price,
                    order_qty=sell_qty,
                    kiwoom_order_no=order_no,
                    status=OrderStatus.SUBMITTED,
                )
                db.add(order)
                registered.append((bit, target_price, sell_qty))
                print(
                    f"[sell_planner] 사전 매도 등록: {position.stock_code} "
                    f"{sell_qty}주 @{target_price:,}원 (bit {bit}, tranche {sell_round}) "
                    f"ord_no={order_no}"
                )
            except Exception as e:
                print(
                    f"[sell_planner] user={position.user_id} {position.stock_code} "
                    f"등록 실패 (bit {bit} @{target_price}): {e}"
                )
                notify_user_fire(
                    position.user_id,
                    f"❌ 사전 매도 주문 등록 실패\n"
                    f"{position.stock_name} ({position.stock_code})\n"
                    f"{sell_qty}주 @ {target_price:,}원\n사유: {e}",
                    dedup_key=f"sell_plan_fail:{position.id}:{bit}",
                )

        await db.commit()
        return {"cancelled": cancelled, "registered": registered, "skipped": None}


async def plan_sell_orders_all(
    db: AsyncSession,
    user_id: int,
    client: KiwoomClient,
) -> dict:
    """유저의 모든 active Position 에 대해 plan_sell_orders_for_position 호출."""
    from app.ws.kiwoom_ws import kiwoom_pool   # 순환 import 방지

    positions = (
        await db.execute(
            select(Position).where(
                Position.user_id == user_id,
                Position.status == PositionStatus.ACTIVE,
            )
        )
    ).scalars().all()

    summary = {
        "positions": len(positions),
        "total_cancelled": 0,
        "total_registered": 0,
        "errors": 0,
    }
    for p in positions:
        ma_values = kiwoom_pool.get_ma(p.stock_code)
        try:
            result = await plan_sell_orders_for_position(db, p, client, ma_values)
        except Exception as e:
            print(
                f"[sell_planner] user={user_id} pos={p.id} ({p.stock_code}) 정비 실패: {e}"
            )
            notify_user_fire(
                user_id,
                f"❌ 사전 매도 주문 정비 실패\n"
                f"{p.stock_name} ({p.stock_code})\n사유: {e}",
                dedup_key=f"sell_plan_pos_fail:{p.id}",
            )
            summary["errors"] += 1
            continue
        summary["total_cancelled"] += result["cancelled"]
        summary["total_registered"] += len(result["registered"])
    return summary
