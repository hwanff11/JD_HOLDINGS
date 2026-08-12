from __future__ import annotations

from decimal import Decimal

from jd_holdings.config import StrategyConfig

from .broker import Broker
from .database import SQLiteRepository


def managed_cash_balance(config: StrategyConfig, repository: SQLiteRepository) -> Decimal:
    """Reconstruct JDSS-owned cash from the immutable order ledger.

    The broker account may contain personal cash or securities. JDSS therefore starts from
    its configured allocation and applies only fills created by this repository. Using the
    cumulative fill quantity of each order also makes the result stable across restarts.
    """
    balance = config.total_strategy_capital
    with repository.transaction() as connection:
        rows = connection.execute(
            """
            SELECT side, filled_qty, average_fill_price, price
            FROM orders
            WHERE filled_qty > 0
            ORDER BY order_id
            """
        ).fetchall()
    for row in rows:
        quantity = int(row["filled_qty"])
        raw_price = row["average_fill_price"] or row["price"]
        if raw_price is None:
            raise RuntimeError("체결수량이 있는 주문에 체결가격이 없습니다")
        price = Decimal(str(raw_price))
        gross = Decimal(quantity) * price
        side = str(row["side"]).upper()
        if side == "BUY":
            balance -= gross * (Decimal("1") + config.global_.buy_fee)
        elif side == "SELL":
            balance += gross * (Decimal("1") - config.global_.sell_fee)
        else:
            raise RuntimeError(f"지원하지 않는 주문 방향입니다: {side}")
    return balance


def managed_market_value(
    config: StrategyConfig,
    repository: SQLiteRepository,
    broker: Broker,
) -> Decimal:
    """Market value of only the positions explicitly owned by JDSS ledgers."""
    value = Decimal("0")
    for symbol in config.enabled_symbols:
        booster = repository.get_position(symbol)
        core = repository.get_core_position(symbol)
        quantity = booster.quantity + int(core["qty"])
        if quantity > 0:
            value += Decimal(quantity) * broker.get_price(symbol)
    if config.idle_cash.enabled:
        idle = repository.get_idle_cash_state()
        if idle.managed_quantity > 0:
            value += Decimal(idle.managed_quantity) * broker.get_price(idle.symbol)
    return value


def managed_equity(
    config: StrategyConfig,
    repository: SQLiteRepository,
    broker: Broker,
) -> Decimal:
    return managed_cash_balance(config, repository) + managed_market_value(
        config, repository, broker
    )


def available_managed_cash(
    config: StrategyConfig,
    repository: SQLiteRepository,
    broker: Broker,
    *,
    reserved: Decimal = Decimal("0"),
) -> Decimal:
    """Spendable cash bounded by both JDSS ownership and actual broker liquidity."""
    ledger_cash = managed_cash_balance(config, repository) - reserved
    broker_cash = broker.get_buying_power("USD") - reserved
    return max(Decimal("0"), min(ledger_cash, broker_cash))
