"""
한국 거래소 호가단위 (코스피·코스닥 통일, 2023.1~).

매도 목표가가 호가 단위에 맞지 않으면 키움 API 가 RC4003 으로 거부한다.
지정가 매도 주문 등록 전에 가장 가까운 호가로 round 한다.
"""
from __future__ import annotations


# 가격 미만일 때의 호가 단위 — (상한, tick) 쌍, 오름차순
_TICK_TABLE: tuple[tuple[int, int], ...] = (
    (2_000,    1),
    (5_000,    5),
    (20_000,   10),
    (50_000,   50),
    (200_000,  100),
    (500_000,  500),
)
_TICK_ABOVE_TABLE = 1_000   # 50만원 이상


def get_tick_size(price: int) -> int:
    """price 가 속하는 호가 단위(원) 반환. price <= 0 이면 1 반환(안전 기본값)."""
    if price <= 0:
        return 1
    for ceiling, tick in _TICK_TABLE:
        if price < ceiling:
            return tick
    return _TICK_ABOVE_TABLE


def round_to_tick(price: float) -> int:
    """가장 가까운 호가 단위로 반올림. 결과는 항상 양수 정수.

    경계(예: 5000원)에서 호가 단위가 바뀌므로 round 결과가 다른 호가 구간에
    들어가는 경우가 있을 수 있으나, 5000원은 양쪽 구간(5/10원)에서 모두 valid
    하므로 실제 거래에 문제 없음.
    """
    if price <= 0:
        return 0
    tick = get_tick_size(int(price))
    rounded = int(round(price / tick)) * tick
    return max(tick, rounded)
