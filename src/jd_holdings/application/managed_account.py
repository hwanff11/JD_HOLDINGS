from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from jd_holdings.config import StrategyConfig

from .broker import Broker
from .database import SQLiteRepository

OPEN_ORDER_STATUSES = ("CREATED", "SUBMITTED", "PENDING", "PARTIAL_FILLED", "UNKNOWN")


def _managed_cash_from_connection(
    config: StrategyConfig, connection: Any
) -> Decimal:
    balance = config.total_strategy_capital
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


def _reserved_open_buy_from_connection(
    config: StrategyConfig, connection: Any
) -> Decimal:
    placeholders = ",".join("?" for _ in OPEN_ORDER_STATUSES)
    rows = connection.execute(
        f"""
        SELECT price, qty, filled_qty
        FROM orders
        WHERE side = 'BUY' AND status IN ({placeholders}) AND price IS NOT NULL
        """,  # nosec B608 - placeholders are generated from a fixed constant tuple.
        OPEN_ORDER_STATUSES,
    ).fetchall()
    reserved = Decimal("0")
    for row in rows:
        remaining = max(0, int(row["qty"]) - int(row["filled_qty"]))
        if remaining <= 0:
            continue
        reserved += (
            Decimal(remaining)
            * Decimal(str(row["price"]))
            * (Decimal("1") + config.global_.buy_fee)
        )
    return reserved


def managed_cash_balance(config: StrategyConfig, repository: SQLiteRepository) -> Decimal:
    """Reconstruct JDSS-owned cash from initial allocation and persisted fills."""
    with repository.transaction() as connection:
        return _managed_cash_from_connection(config, connection)


def reserved_open_buy_amount(
    config: StrategyConfig, repository: SQLiteRepository
) -> Decimal:
    """Cash committed to locally open BUY orders but not filled yet."""
    with repository.transaction() as connection:
        return _reserved_open_buy_from_connection(config, connection)


def available_managed_cash(
    config: StrategyConfig,
    repository: SQLiteRepository,
    broker: Broker,
    *,
    additional_reservation: Decimal = Decimal("0"),
) -> Decimal:
    """Spendable cash bounded by JDSS ownership, reservations, and broker liquidity."""
    with repository.transaction() as connection:
        ledger_available = (
            _managed_cash_from_connection(config, connection)
            - _reserved_open_buy_from_connection(config, connection)
            - additional_reservation
        )
    broker_available = broker.get_buying_power("USD") - additional_reservation
    return max(Decimal("0"), min(ledger_available, broker_available))


def reserve_buy_order_with_managed_cash(
    config: StrategyConfig,
    repository: SQLiteRepository,
    broker: Broker,
    *,
    client_order_id: str,
    signal_id: int | None,
    cycle_id: str | None,
    symbol: str,
    order_type: str,
    price: Decimal,
    quantity: int,
    purpose: str,
) -> bool:
    """Atomically validate JDSS cash and reserve a BUY order in one DB transaction."""
    broker_available = broker.get_buying_power("USD")
    required = Decimal(quantity) * price * (Decimal("1") + config.global_.buy_fee)
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        ledger_available = (
            _managed_cash_from_connection(config, connection)
            - _reserved_open_buy_from_connection(config, connection)
        )
        available = max(Decimal("0"), min(ledger_available, broker_available))
        if required > available:
            raise RuntimeError(
                "JDSS 관리현금이 부족하여 매수 주문을 차단했습니다 "
                f"(필요={required:.2f}, 사용가능={available:.2f})"
            )
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO orders(
                client_order_id, signal_id, cycle_id, symbol, side, order_type,
                price, qty, status, purpose, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'BUY', ?, ?, ?, 'CREATED', ?, ?, ?)
            """,
            (
                client_order_id,
                signal_id,
                cycle_id,
                symbol.upper(),
                order_type,
                str(price),
                quantity,
                purpose,
                now,
                now,
            ),
        )
        return cursor.rowcount == 1


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
