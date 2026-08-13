from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from jd_holdings.config import StrategyConfig
from jd_holdings.core.twin_core import target_quantity
from jd_holdings.core.v322_allocation import V322Policy, hwm_risk_budget

from .broker import Broker
from .database import SQLiteRepository

OPEN_ORDER_STATUSES = ("CREATED", "SUBMITTED", "PENDING", "PARTIAL_FILLED", "UNKNOWN")
HIGH_WATER_KEY = "v322_high_water_equity"
RISK_BUDGET_KEY = "v322_risk_budget"


def _raw_managed_cash_from_connection(
    config: StrategyConfig, connection: Any
) -> Decimal:
    """Reconstruct all USD cash owned by JDSS, including retained profits."""
    policy = V322Policy.from_config(config)
    balance = policy.initial_capital
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


def _managed_cost_basis_from_connection(
    config: StrategyConfig, connection: Any
) -> Decimal:
    """Return capital currently deployed by JDSS-owned positions."""
    booster_rows = connection.execute("SELECT current_cost_basis FROM positions").fetchall()
    core_rows = connection.execute("SELECT cost_basis FROM core_positions").fetchall()
    total = sum(
        (Decimal(str(row["current_cost_basis"] or "0")) for row in booster_rows),
        Decimal("0"),
    )
    total += sum(
        (Decimal(str(row["cost_basis"] or "0")) for row in core_rows),
        Decimal("0"),
    )
    if config.idle_cash.enabled:
        idle = connection.execute(
            "SELECT managed_qty, avg_price FROM idle_cash_state WHERE symbol = ?",
            (config.idle_cash.symbol,),
        ).fetchone()
        if idle:
            total += Decimal(int(idle["managed_qty"])) * Decimal(str(idle["avg_price"]))
    return max(Decimal("0"), total)


def _system_decimal(connection: Any, key: str, default: Decimal) -> Decimal:
    row = connection.execute("SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()
    return Decimal(str(row["value"])) if row else default


def _risk_budget_from_connection(config: StrategyConfig, connection: Any) -> Decimal:
    policy = V322Policy.from_config(config)
    return _system_decimal(connection, RISK_BUDGET_KEY, policy.initial_capital)


def _reserved_open_buy_from_connection(
    config: StrategyConfig, connection: Any
) -> Decimal:
    placeholders = ",".join("?" for _ in OPEN_ORDER_STATUSES)
    rows = connection.execute(
        f"""
        SELECT price, qty, filled_qty
        FROM orders
        WHERE side = 'BUY' AND status IN ({placeholders}) AND price IS NOT NULL
        """,  # nosec B608 - placeholders come from a fixed constant tuple.
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


def _committed_core_buy_quantity_from_connection(
    connection: Any, symbol: str
) -> int:
    """Return core BUY shares that are ordered or filled but not in the core ledger."""
    rows = connection.execute(
        """
        SELECT orders.qty, orders.filled_qty, orders.status,
               COALESCE(core_fill_progress.applied_filled_qty, 0) AS applied_filled_qty
        FROM orders
        LEFT JOIN core_fill_progress
          ON core_fill_progress.client_order_id = orders.client_order_id
        WHERE orders.symbol = ? AND orders.side = 'BUY'
          AND orders.purpose = 'CORE_REBALANCE_BUY'
        """,
        (symbol.upper(),),
    ).fetchall()
    committed = 0
    for row in rows:
        applied = int(row["applied_filled_qty"])
        if str(row["status"]) in OPEN_ORDER_STATUSES:
            committed += max(0, int(row["qty"]) - applied)
        else:
            committed += max(0, int(row["filled_qty"]) - applied)
    return committed


def raw_managed_cash_balance(
    config: StrategyConfig, repository: SQLiteRepository
) -> Decimal:
    with repository.transaction() as connection:
        return _raw_managed_cash_from_connection(config, connection)


def managed_principal_cost_basis(
    config: StrategyConfig, repository: SQLiteRepository
) -> Decimal:
    with repository.transaction() as connection:
        return _managed_cost_basis_from_connection(config, connection)


def managed_cash_balance(config: StrategyConfig, repository: SQLiteRepository) -> Decimal:
    """All JDSS USD cash, including the 25% of HWM profit excluded from risk sizing."""
    return max(Decimal("0"), raw_managed_cash_balance(config, repository))


def current_v322_capital_state(
    config: StrategyConfig, repository: SQLiteRepository
) -> tuple[Decimal, Decimal]:
    policy = V322Policy.from_config(config)
    with repository.transaction() as connection:
        high_water = _system_decimal(
            connection, HIGH_WATER_KEY, policy.initial_capital
        )
        risk_budget = _system_decimal(
            connection, RISK_BUDGET_KEY, policy.initial_capital
        )
    return high_water, risk_budget


def record_v322_equity(
    config: StrategyConfig,
    repository: SQLiteRepository,
    marked_equity: Decimal,
) -> tuple[Decimal, Decimal]:
    """Advance HWM only from a completed-session marked equity observation."""
    policy = V322Policy.from_config(config)
    if marked_equity < 0:
        raise ValueError("marked_equity는 0 이상이어야 합니다")
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        previous = _system_decimal(
            connection, HIGH_WATER_KEY, policy.initial_capital
        )
        high_water = max(previous, marked_equity, policy.initial_capital)
        risk_budget = hwm_risk_budget(high_water, marked_equity, policy)
        for key, value in (
            (HIGH_WATER_KEY, high_water),
            (RISK_BUDGET_KEY, risk_budget),
        ):
            connection.execute(
                """
                INSERT INTO system_state(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, str(value), now),
            )
    return high_water, risk_budget


def reserved_open_buy_amount(
    config: StrategyConfig, repository: SQLiteRepository
) -> Decimal:
    with repository.transaction() as connection:
        return _reserved_open_buy_from_connection(config, connection)


def committed_core_buy_quantity(
    repository: SQLiteRepository, symbol: str
) -> int:
    with repository.transaction() as connection:
        return _committed_core_buy_quantity_from_connection(connection, symbol)


def available_managed_cash(
    config: StrategyConfig,
    repository: SQLiteRepository,
    broker: Broker,
    *,
    additional_reservation: Decimal = Decimal("0"),
) -> Decimal:
    """Spendable cash bounded by HWM75 risk budget, reservations and broker liquidity."""
    with repository.transaction() as connection:
        raw_cash = _raw_managed_cash_from_connection(config, connection)
        invested = _managed_cost_basis_from_connection(config, connection)
        risk_budget = _risk_budget_from_connection(config, connection)
        reserved = _reserved_open_buy_from_connection(config, connection)
        cash_available = raw_cash - reserved - additional_reservation
        risk_available = risk_budget - invested - reserved - additional_reservation
        ledger_available = min(cash_available, risk_available)
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
    """Atomically enforce HWM75 deployable-risk ceiling and reserve a BUY."""
    broker_available = broker.get_buying_power("USD")
    required = Decimal(quantity) * price * (Decimal("1") + config.global_.buy_fee)
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        raw_cash = _raw_managed_cash_from_connection(config, connection)
        invested = _managed_cost_basis_from_connection(config, connection)
        risk_budget = _risk_budget_from_connection(config, connection)
        reserved = _reserved_open_buy_from_connection(config, connection)
        if purpose == "CORE_REBALANCE_BUY":
            core = connection.execute(
                "SELECT qty, target_weight FROM core_positions WHERE symbol = ?",
                (symbol.upper(),),
            ).fetchone()
            if core is None:
                raise RuntimeError(f"V3.2.2 코어 목표 종목이 아닙니다: {symbol.upper()}")
            target = target_quantity(
                risk_budget,
                Decimal(str(core["target_weight"])),
                price,
                config.global_.buy_fee,
            )
            committed = _committed_core_buy_quantity_from_connection(
                connection, symbol
            )
            remaining_target = max(0, target - int(core["qty"]) - committed)
            if quantity > remaining_target:
                raise RuntimeError(
                    "V3.2.2 목표수량을 초과하는 코어 매수를 차단했습니다 "
                    f"(요청={quantity}주, 잔여={remaining_target}주, "
                    f"보유={int(core['qty'])}주, 주문중={committed}주)"
                )
        available = max(
            Decimal("0"),
            min(raw_cash - reserved, risk_budget - invested - reserved, broker_available),
        )
        if required > available:
            raise RuntimeError(
                "JDSS HWM75 위험예산이 부족하여 매수 주문을 차단했습니다 "
                f"(필요={required:.2f}, 사용가능={available:.2f}, 위험예산={risk_budget:.2f})"
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
    """Market value of every position explicitly owned by the JDSS ledgers."""
    value = Decimal("0")
    for core in repository.core_positions():
        quantity = int(core["qty"])
        if quantity > 0:
            value += Decimal(quantity) * broker.get_price(str(core["symbol"]))
    for symbol in config.enabled_symbols:
        booster = repository.get_position(symbol)
        if booster.quantity > 0:
            value += Decimal(booster.quantity) * broker.get_price(symbol)
    if config.idle_cash.enabled:
        idle = repository.get_idle_cash_state()
        if idle.managed_quantity > 0:
            value += Decimal(idle.managed_quantity) * broker.get_price(idle.symbol)
    return value


def marked_managed_equity(
    config: StrategyConfig,
    repository: SQLiteRepository,
    broker: Broker,
) -> Decimal:
    """Full JDSS marked equity, including profit retained outside the risk budget."""
    return managed_cash_balance(config, repository) + managed_market_value(
        config, repository, broker
    )


def managed_equity(
    config: StrategyConfig,
    repository: SQLiteRepository,
    broker: Broker,
) -> Decimal:
    """Current HWM75 deployable sizing base, capped by current marked equity."""
    _, risk_budget = current_v322_capital_state(config, repository)
    return max(
        Decimal("0"),
        min(risk_budget, marked_managed_equity(config, repository, broker)),
    )
