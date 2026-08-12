from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from jd_holdings.config import StrategyConfig

from .models import TakeProfitPlan


def ceil_to_tick(value: Decimal, tick_size: Decimal = Decimal("0.01")) -> Decimal:
    return (value / tick_size).to_integral_value(rounding=ROUND_CEILING) * tick_size


def calculate_take_profit(
    average_price: Decimal,
    quantity: int,
    atr_pct: Decimal,
    config: StrategyConfig,
    tick_size: Decimal = Decimal("0.01"),
) -> TakeProfitPlan:
    if average_price <= 0:
        raise ValueError("평단은 양수여야 합니다")
    if quantity <= 0:
        raise ValueError("보유수량은 양수여야 합니다")
    if config.take_profit.use_atr:
        tp1_rate = max(
            config.take_profit.tp1_base,
            config.take_profit.tp1_atr_multiplier * atr_pct,
        )
        tp2_rate = max(
            config.take_profit.tp2_base,
            config.take_profit.tp2_atr_multiplier * atr_pct,
        )
    else:
        tp1_rate = config.take_profit.tp1_base
        tp2_rate = config.take_profit.tp2_base
    tp1_quantity = int(
        (Decimal(quantity) * config.take_profit.tp1_fraction).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    tp1_quantity = max(1, min(quantity, tp1_quantity))
    tp2_quantity = quantity - tp1_quantity
    return TakeProfitPlan(
        average_price=average_price,
        atr_pct=atr_pct,
        tp1_rate=tp1_rate,
        tp2_rate=tp2_rate,
        tp1_price=ceil_to_tick(average_price * (Decimal("1") + tp1_rate), tick_size),
        tp2_price=ceil_to_tick(average_price * (Decimal("1") + tp2_rate), tick_size),
        tp1_quantity=tp1_quantity,
        tp2_quantity=tp2_quantity,
    )
