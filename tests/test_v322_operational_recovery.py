from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd
import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import (
    OPEN_ORDER_STATUSES,
    SQLiteRepository,
    StateConflictError,
)
from jd_holdings.application.managed_account import (
    committed_core_buy_quantity,
    reserved_open_buy_amount,
)
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.portfolio_service import PortfolioService
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.bot import recover_unapplied_core_fills, restore_dry_run_holdings
from jd_holdings.core.models import OrderReceipt, OrderRequest
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.settings import RuntimeSettings


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(),
        database_path=tmp_path / "operational.db",
        log_path=tmp_path / "operational.log",
    )


class StubPortfolioService(PortfolioService):
    def __init__(self, *args, target: pd.DataFrame, **kwargs):
        self._target = target
        super().__init__(*args, **kwargs)

    def _calculate_target(self, completed):
        del completed
        return {}, self._target

    def _completed_marked_equity(self, raw, timestamp):
        del raw, timestamp
        return Decimal("50000")


def _target_frame(completed: date) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "leverage": 1.5,
                "semiconductor_active": True,
                "jdss_tqqq_active": False,
                "jdss_soxl_active": False,
                "QQQ": 0.75,
                "TQQQ": 0.125,
                "SOXL": 0.125,
            }
        ],
        index=[pd.Timestamp(completed)],
    )


def test_existing_database_migrates_target_qty_and_core_notional(tmp_path, config):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE core_positions (
                symbol TEXT PRIMARY KEY,
                underlying TEXT NOT NULL,
                qty INTEGER NOT NULL DEFAULT 0,
                avg_price TEXT NOT NULL DEFAULT '0',
                cost_basis TEXT NOT NULL DEFAULT '0',
                target_weight TEXT NOT NULL DEFAULT '0',
                trend_active INTEGER NOT NULL DEFAULT 0,
                signal_trade_date TEXT,
                version INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE core_fill_progress (
                client_order_id TEXT PRIMARY KEY,
                applied_filled_qty INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )

    repository = SQLiteRepository(path, config)
    with repository.transaction() as connection:
        core_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(core_positions)")
        }
        fill_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(core_fill_progress)")
        }

    assert "target_qty" in core_columns
    assert "applied_notional" in fill_columns


def test_startup_recovers_persisted_fill_before_holdings_restore(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "crash.db", config)
    client_id = "CORE-CRASH-WINDOW"
    assert repository.reserve_order(
        client_order_id=client_id,
        signal_id=None,
        cycle_id=None,
        symbol="QQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=2,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        client_id,
        status="FILLED",
        broker_order_id="DRY-CRASH",
        filled_qty=2,
        average_fill_price=Decimal("100"),
    )

    assert repository.get_core_position("QQQ")["qty"] == 0
    assert recover_unapplied_core_fills(repository) == (client_id,)
    assert recover_unapplied_core_fills(repository) == ()

    broker = DryRunBroker({"QQQ": Decimal("100")})
    restore_dry_run_holdings(repository, broker)
    assert repository.get_core_position("QQQ")["qty"] == 2
    assert broker.get_holdings("QQQ")[0]["quantity"] == "2"


def test_cumulative_partial_fill_uses_notional_delta(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "partial.db", config)
    client_id = "CORE-CUMULATIVE-NOTIONAL"
    assert repository.reserve_order(
        client_order_id=client_id,
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=2,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        client_id,
        status="PARTIAL_FILLED",
        broker_order_id="DRY-PARTIAL",
        filled_qty=1,
        average_fill_price=Decimal("100"),
    )
    repository.apply_core_fill(client_id)
    repository.update_order(
        client_id,
        status="FILLED",
        filled_qty=2,
        average_fill_price=Decimal("110"),
    )
    repository.apply_core_fill(client_id)

    core = repository.get_core_position("TQQQ")
    assert core["qty"] == 2
    assert Decimal(core["cost_basis"]) == Decimal("220")
    assert Decimal(core["avg_price"]) == Decimal("110")
    with repository.transaction() as connection:
        progress = connection.execute(
            "SELECT * FROM core_fill_progress WHERE client_order_id = ?",
            (client_id,),
        ).fetchone()
    assert Decimal(progress["applied_notional"]) == Decimal("220")


def test_same_quantity_fill_price_correction_updates_cost_and_restart_gap(
    tmp_path, config
):
    repository = SQLiteRepository(tmp_path / "price-correction.db", config)
    client_id = "CORE-SAME-QTY-CORRECTION"
    assert repository.reserve_order(
        client_order_id=client_id,
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=1,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        client_id,
        status="FILLED",
        broker_order_id="DRY-CORRECTION",
        filled_qty=1,
        average_fill_price=Decimal("100"),
    )
    repository.apply_core_fill(client_id)
    repository.update_order(
        client_id,
        status="FILLED",
        filled_qty=1,
        average_fill_price=Decimal("110"),
    )

    assert repository.unapplied_core_fill_order_ids() == [client_id]
    repository.apply_core_fill(client_id)
    assert repository.unapplied_core_fill_order_ids() == []
    core = repository.get_core_position("TQQQ")
    assert core["qty"] == 1
    assert Decimal(core["cost_basis"]) == Decimal("110")
    assert Decimal(core["avg_price"]) == Decimal("110")


def test_order_update_rejects_non_monotonic_or_excess_fill(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "monotonic.db", config)
    client_id = "CORE-MONOTONIC"
    assert repository.reserve_order(
        client_order_id=client_id,
        signal_id=None,
        cycle_id=None,
        symbol="QQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=2,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        client_id,
        status="PARTIAL_FILLED",
        filled_qty=1,
        average_fill_price=Decimal("100"),
    )

    with pytest.raises(StateConflictError, match="이전 저장값"):
        repository.update_order(client_id, status="PENDING", filled_qty=0)
    with pytest.raises(StateConflictError, match="주문수량"):
        repository.update_order(client_id, status="FILLED", filled_qty=3)


def test_terminal_order_status_never_reopens(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "terminal-order.db", config)
    client_id = "CORE-TERMINAL-MONOTONIC"
    assert repository.reserve_order(
        client_order_id=client_id,
        signal_id=None,
        cycle_id=None,
        symbol="QQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=1,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        client_id,
        status="FILLED",
        broker_order_id="DRY-TERMINAL",
        filled_qty=1,
        average_fill_price=Decimal("100"),
    )

    with pytest.raises(StateConflictError, match="되돌릴 수 없습니다"):
        repository.update_order(client_id, status="PENDING", filled_qty=1)


def test_broker_receipt_identity_mismatch_is_persisted_as_unknown(tmp_path, config):
    class CorruptReceiptBroker(DryRunBroker):
        def place_order(self, request):
            return OrderReceipt(
                client_order_id=request.client_order_id,
                broker_order_id="CORRUPT-1",
                status="FILLED",
                quantity=request.quantity + 1,
                filled_quantity=request.quantity + 1,
                average_fill_price=Decimal("100"),
                raw={
                    "clientOrderId": request.client_order_id,
                    "symbol": "SOXL",
                    "side": request.side,
                    "quantity": str(request.quantity + 1),
                },
            )

    repository = SQLiteRepository(tmp_path / "corrupt-receipt.db", config)
    broker = CorruptReceiptBroker({"TQQQ": Decimal("100")})
    manager = OrderManager(repository, broker, _settings(tmp_path))
    request = OrderRequest(
        client_order_id="CORRUPT-RECEIPT",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=1,
        price=Decimal("100"),
        purpose="TEST_BUY",
    )

    with pytest.raises(RuntimeError, match="주문수량"):
        manager.submit(request, cycle_id=None)

    assert repository.get_order_by_client_id(request.client_order_id)["status"] == (
        "UNKNOWN"
    )


def test_pending_cancel_and_replace_are_open_and_reserved(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "statuses.db", config)
    for index, status in enumerate(("PENDING_CANCEL", "PENDING_REPLACE"), start=1):
        client_id = f"CORE-{status}"
        assert repository.reserve_order(
            client_order_id=client_id,
            signal_id=None,
            cycle_id=None,
            symbol="TQQQ",
            side="BUY",
            order_type="LIMIT",
            price=Decimal("100"),
            quantity=index,
            purpose="CORE_REBALANCE_BUY",
        )
        repository.update_order(
            client_id,
            status=status,
            broker_order_id=f"DRY-{status}",
        )

    assert {"PENDING_CANCEL", "PENDING_REPLACE"} <= set(OPEN_ORDER_STATUSES)
    assert {order["status"] for order in repository.open_orders()} == {
        "PENDING_CANCEL",
        "PENDING_REPLACE",
    }
    assert committed_core_buy_quantity(repository, "TQQQ") == 3
    assert reserved_open_buy_amount(config, repository) == (
        Decimal("300") * (Decimal("1") + config.global_.buy_fee)
    )


def test_target_qty_is_fixed_and_expired_gap_signal_is_reactivated(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "fixed-target.db", config)
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    clock = MarketClock()
    signal_day_after_close = datetime(2026, 8, 3, 22, tzinfo=UTC)
    completed = clock.latest_completed_session(
        signal_day_after_close,
        delay_minutes=config.scheduler.signal_delay_minutes,
    )
    service = StubPortfolioService(
        config,
        repository,
        broker,
        OrderManager(repository, broker, _settings(tmp_path)),
        object(),
        clock,
        trading_mode="dry_run",
        target=_target_frame(completed),
    )

    planned = service.run_allocation(signal_day_after_close)
    assert planned is not None and planned.signals == ()
    assert int(repository.get_core_position("QQQ")["target_qty"]) == 0

    broker.set_price("QQQ", Decimal("1000"))
    execution = service.run_allocation(datetime(2026, 8, 4, 12, tzinfo=UTC))
    assert execution is not None and len(execution.signals) == 3
    fixed_qty = int(repository.get_core_position("QQQ")["target_qty"])
    assert fixed_qty == 37
    assert int(repository.get_core_position("QQQ")["target_qty"]) == fixed_qty
    qqq_signal = next(
        signal
        for signal in repository.active_signals("QQQ")
        if signal["action"] == "CORE_REBALANCE_BUY"
    )
    repository.mark_signal(
        int(qqq_signal["signal_id"]),
        status="EXPIRED",
        processed=True,
        reason="TEST_EXPIRED",
    )

    broker.set_price("QQQ", Decimal("750"))
    recovered = service.run_allocation(datetime(2026, 8, 4, 13, tzinfo=UTC))
    assert recovered is not None
    assert recovered.signals == (int(qqq_signal["signal_id"]),)
    assert int(repository.get_core_position("QQQ")["target_qty"]) == fixed_qty
    assert repository.get_signal(int(qqq_signal["signal_id"]))["status"] == "ACTIVE"


def test_existing_v322_target_weight_never_migrates_as_zero_share_sell(
    tmp_path, config
):
    repository = SQLiteRepository(tmp_path / "existing-v322.db", config)
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("45000"),
    )
    generation = date(2026, 8, 3)
    repository.set_core_target(
        "TQQQ",
        active=True,
        target_weight=Decimal("0.10"),
        target_qty=0,
        signal_trade_date=generation,
    )
    for symbol in ("QQQ", "SOXL"):
        repository.set_core_target(
            symbol,
            active=False,
            target_weight=Decimal("0"),
            target_qty=0,
            signal_trade_date=generation,
        )
    seed = "EXISTING-V322-TQQQ"
    assert repository.reserve_order(
        client_order_id=seed,
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=10,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        seed,
        status="FILLED",
        broker_order_id="DRY-EXISTING",
        filled_qty=10,
        average_fill_price=Decimal("100"),
    )
    repository.apply_core_fill(seed)
    broker.holdings["TQQQ"] = {
        "quantity": 10,
        "averagePurchasePrice": Decimal("100"),
    }
    repository.set_system_value(
        "last_v322_allocation_trade_date", generation.isoformat()
    )
    service = PortfolioService(
        config,
        repository,
        broker,
        OrderManager(repository, broker, _settings(tmp_path)),
        object(),
        MarketClock(),
        trading_mode="dry_run",
    )

    result = service.run_allocation(datetime(2026, 8, 4, 12, tzinfo=UTC))

    assert result is not None
    assert int(repository.get_core_position("TQQQ")["target_qty"]) == 49
    assert not any(order["side"] == "SELL" for order in repository.open_orders())
    assert len(repository.active_signals("TQQQ")) == 1


def test_open_risk_reducing_sell_blocks_all_buy_signals(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "sell-barrier.db", config)
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("49000"),
    )
    generation = date(2026, 8, 3)
    repository.set_core_target(
        "QQQ",
        active=True,
        target_weight=Decimal("0.01"),
        target_qty=1,
        signal_trade_date=generation,
    )
    repository.set_core_target(
        "TQQQ",
        active=True,
        target_weight=Decimal("0.01"),
        target_qty=5,
        signal_trade_date=generation,
    )
    repository.set_core_target(
        "SOXL",
        active=False,
        target_weight=Decimal("0"),
        target_qty=0,
        signal_trade_date=generation,
    )
    seed = "CORE-SEED-TQQQ"
    assert repository.reserve_order(
        client_order_id=seed,
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=10,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        seed,
        status="FILLED",
        broker_order_id="DRY-SEED",
        filled_qty=10,
        average_fill_price=Decimal("100"),
    )
    repository.apply_core_fill(seed)
    broker.holdings["TQQQ"] = {
        "quantity": 10,
        "averagePurchasePrice": Decimal("100"),
    }
    manager = OrderManager(repository, broker, _settings(tmp_path))
    pending_sell = manager.submit(
        OrderRequest(
            client_order_id="CORE-PENDING-RISK-SELL",
            symbol="TQQQ",
            side="SELL",
            order_type="LIMIT",
            quantity=5,
            price=Decimal("101"),
            purpose="CORE_REBALANCE_SELL",
        ),
        cycle_id=None,
    )
    assert pending_sell.status == "PENDING"
    repository.set_system_value(
        "last_v322_allocation_trade_date", generation.isoformat()
    )
    service = PortfolioService(
        config,
        repository,
        broker,
        manager,
        object(),
        MarketClock(),
        trading_mode="dry_run",
    )

    result = service.run_allocation(datetime(2026, 8, 4, 12, tzinfo=UTC))
    assert result is not None and result.signals == ()
    assert repository.active_signals() == []


def test_reconciliation_holdings_outage_sets_sticky_portfolio_safe_mode(
    tmp_path, config
):
    class FailingHoldingsBroker(DryRunBroker):
        def get_holdings(self, symbol=None):
            del symbol
            raise TimeoutError("holdings unavailable")

    repository = SQLiteRepository(tmp_path / "holdings-outage.db", config)
    broker = FailingHoldingsBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")}
    )

    with pytest.raises(RuntimeError, match="SAFE_MODE"):
        ReconciliationService(config, repository, broker).run()

    assert repository.get_system_value("v322_portfolio_safe_mode") == "1"
    assert repository.recent_events(1)[0]["event_type"] == (
        "BROKER_HOLDINGS_LOOKUP_FAILED"
    )


def test_reconciliation_rejects_fractional_managed_holding(tmp_path, config):
    class FractionalHoldingBroker(DryRunBroker):
        def get_holdings(self, symbol=None):
            del symbol
            return [{"symbol": "QQQ", "quantity": "0.5"}]

    repository = SQLiteRepository(tmp_path / "fractional.db", config)
    broker = FractionalHoldingBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")}
    )

    issues = ReconciliationService(config, repository, broker).run()

    assert "BROKER_FRACTIONAL_HOLDING:0.5" in issues["QQQ"]
    assert repository.get_system_value("v322_portfolio_safe_mode") == "1"
