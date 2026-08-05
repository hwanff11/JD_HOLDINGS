from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from jd_holdings.config import StrategyConfig

from .enums import DecisionType


def max_chase_price(signal_close: Decimal, config: StrategyConfig) -> Decimal:
    return signal_close * (Decimal("1") + config.global_.entry_max_chase_pct)


def calculate_execution_price_ceiling(
    action: DecisionType,
    signal_close: Decimal,
    config: StrategyConfig,
    *,
    stage_trigger_price: Decimal | None = None,
    average_price: Decimal | None = None,
) -> Decimal:
    chase = max_chase_price(signal_close, config)
    if action == DecisionType.FIRST_ENTRY_CANDIDATE:
        return chase
    if action == DecisionType.ADD_ENTRY_CANDIDATE:
        if stage_trigger_price is None:
            raise ValueError("추가매수에는 stage_trigger_price가 필요합니다")
        return min(chase, stage_trigger_price)
    if action == DecisionType.REBUY_CANDIDATE:
        if average_price is None:
            raise ValueError("재매수에는 average_price가 필요합니다")
        rebuy_ceiling = average_price * (Decimal("1") - config.rebuy.min_drop_from_avg)
        return min(chase, rebuy_ceiling)
    raise ValueError(f"매수 실행 상한을 계산할 수 없는 action: {action}")


def floor_to_tick(value: Decimal, tick_size: Decimal = Decimal("0.01")) -> Decimal:
    if tick_size <= 0:
        raise ValueError("tick_size는 양수여야 합니다")
    return (value / tick_size).to_integral_value(rounding=ROUND_DOWN) * tick_size


def calculate_limit_price(
    current_price: Decimal,
    execution_price_ceiling: Decimal,
    config: StrategyConfig,
    tick_size: Decimal = Decimal("0.01"),
) -> Decimal:
    if current_price <= 0:
        raise ValueError("현재가는 양수여야 합니다")
    if current_price > execution_price_ceiling:
        raise ValueError("현재가가 전략상 매수 허용 상한을 초과했습니다")
    raw = current_price * (Decimal("1") + config.global_.buy_limit_buffer)
    return floor_to_tick(min(raw, execution_price_ceiling), tick_size)


def calculate_order_quantity(
    budget: Decimal,
    limit_price: Decimal,
    buy_fee: Decimal,
    maximum_quantity: int | None = None,
) -> int:
    if budget <= 0 or limit_price <= 0:
        return 0
    per_share = limit_price * (Decimal("1") + buy_fee)
    quantity = int((budget / per_share).to_integral_value(rounding=ROUND_DOWN))
    if maximum_quantity is not None:
        quantity = min(quantity, maximum_quantity)
    return max(0, quantity)
