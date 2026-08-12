from __future__ import annotations

from decimal import Decimal

from jd_holdings.config import StrategyConfig

from .broker import Broker
from .database import SQLiteRepository


def managed_cash_balance(config: StrategyConfig, repository: SQLiteRepository) -> Decimal:
    """Reconstruct JDSS-owned cash from initial allocation and persisted fills."""
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


def reserved_open_buy_amount(
    config: StrategyConfig, repository: SQLiteRepository
) -> Decimal:
    """Cash committed to locally open BUY orders but not filled yet."""
    reserved = Decimal("0")
    for order in repository.open_orders():
        if str(order["side"]).upper() != "BUY" or order.get("price") is None:
            continue
        remaining = max(0, int(order["qty"]) - int(order["filled_qty"]))
        if remaining <= 0:
            continue
        reserved += (
            Decimal(remaining)
            * Decimal(str(order["price"]))
            * (Decimal("1") + config.global_.buy_fee)
        )
    return reserved


def available_managed_cash(
    config: StrategyConfig,
    repository: SQLiteRepository,
    broker: Broker,
    *,
    additional_reservation: Decimal = Decimal("0"),
) -> Decimal:
    """Spendable cash bounded by JDSS ownership, reservations, and broker liquidity."""
    ledger_available = (
        managed_cash_balance(config, repository)
        - reserved_open_buy_amount(config, repository)
        - additional_reservation
    )
    broker_available = broker.get_buying_power("USD") - additional_reservation
    return max(Decimal("0"), min(ledger_available, broker_available))


def managed_market_value(
    config: StrategyConfig,
    repository: SQLiteRepository,
    broker: Broker,
) -> Decimal:
    """Market value of positions explicitly owned by JDSS ledgers only."""
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
    """JDSS-owned cash plus JDSS-owned market value, excluding personal assets."""
    return managed_cash_balance(config, repository) + managed_market_value(
        config, repository, broker
    )
