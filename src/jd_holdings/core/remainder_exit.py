from __future__ import annotations

from decimal import Decimal

from jd_holdings.config import RemainderExitConfig

from .take_profit import ceil_to_tick


def remainder_exit_due(elapsed_trading_sessions: int, rule: RemainderExitConfig) -> bool:
    """Return whether the post-TP1 remainder exit may replace TP2."""
    if elapsed_trading_sessions < 0:
        raise ValueError("경과 거래일은 0 이상이어야 합니다")
    return rule.enabled and elapsed_trading_sessions >= rule.wait_trading_days


def remainder_exit_price(
    average_price: Decimal,
    rule: RemainderExitConfig,
) -> Decimal:
    """Calculate the executable limit price for the post-TP1 remainder exit."""
    if average_price <= 0:
        raise ValueError("평균 매입가는 0보다 커야 합니다")
    if rule.target_from_avg < 0:
        raise ValueError("잔여청산 목표수익률은 0 이상이어야 합니다")
    return ceil_to_tick(average_price * (Decimal("1") + rule.target_from_avg))
