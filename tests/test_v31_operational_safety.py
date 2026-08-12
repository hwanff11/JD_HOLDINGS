from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.managed_account import (
    available_managed_cash,
    managed_cash_balance,
    managed_equity,
)
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.application.trading_service import TradingService
from jd_holdings.bot import restore_dry_run_holdings
from jd_holdings.core.models import OrderRequest
from jd_holdings.core.twin_core import target_quantity
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.settings import RuntimeSettings


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "operational.db",
        log_path=tmp_path / "operational.log",
    )


def test_managed_equity_ignores_personal_cash_and_holdings(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "operational.db", config)
    broker = DryRunBroker(
        {"TQQQ": Decimal("100"), "SOXL": Decimal("50"), "SGOV": Decimal("100")},
        buying_power=Decimal("50000"),
    )
    broker.holdings["SGOV"] = {
        "quantity": 40,
        "averagePurchasePrice": Decimal("99"),
    }
    broker.holdings["TQQQ"] = {
        "quantity": 7,
        "averagePurchasePrice": Decimal("80"),
    }

    assert managed_cash_balance(config, repository) == Decimal("20000")
    assert available_managed_cash(config, repository, broker) == Decimal("20000")
    assert managed_equity(config, repository, broker) == Decimal("20000")


def test_managed_cash_reconstructs_realized_pnl_and_fees(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "operational.db", config)
    for client_id, side, price in (
        ("BUY-1", "BUY", Decimal("100")),
        ("SELL-1", "SELL", Decimal("110")),
    ):
        assert repository.reserve_order(
            client_order_id=client_id,
            signal_id=None,
            cycle_id=None,
            symbol="TQQQ",
            side=side,
            order_type="LIMIT",
            price=price,
            quantity=10,
            purpose="TEST",
        )
        repository.update_order(
            client_id,
            status="FILLED",
            broker_order_id=f"DRY-{client_id}",
            filled_qty=10,
            average_fill_price=price,
        )

    expected = Decimal("20000") - Decimal("1000") * Decimal("1.001")
    expected += Decimal("1100") * Decimal("0.999")
    assert managed_cash_balance(config, repository) == expected

    broker = DryRunBroker(
        {"TQQQ": Decimal("110"), "SOXL": Decimal("50"), "SGOV": Decimal("100")},
        buying_power=Decimal("999999"),
    )
    restore_dry_run_holdings(repository, broker)
    assert broker.get_buying_power("USD") == expected


def test_core_review_rechecks_current_managed_target(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "operational.db", config)
    broker = DryRunBroker(
        {"TQQQ": Decimal("90"), "SOXL": Decimal("50"), "SGOV": Decimal("100")},
        buying_power=Decimal("18098.1"),
    )
    broker.holdings["TQQQ"] = {
        "quantity": 19,
        "averagePurchasePrice": Decimal("100"),
    }
    repository.set_core_target(
        "TQQQ",
        active=True,
        target_weight=Decimal("0.10"),
        signal_trade_date=date(2026, 7, 31),
    )
    assert repository.reserve_order(
        client_order_id="CORE-OLD",
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=19,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        "CORE-OLD",
        status="FILLED",
        broker_order_id="DRY-00000001",
        filled_qty=19,
        average_fill_price=Decimal("100"),
    )
    repository.apply_core_fill("CORE-OLD")

    planned_budget = (
        Decimal("19")
        * Decimal("100")
        * (Decimal("1") + config.global_.buy_limit_buffer)
        * (Decimal("1") + config.global_.buy_fee)
    )
    signal_id, created = repository.create_core_buy_signal(
        symbol="TQQQ",
        trade_date=date(2026, 7, 31),
        signal_close=Decimal("100"),
        planned_budget=planned_budget,
        valid_until=datetime.now(UTC) + timedelta(days=2),
        code_version="test",
    )
    assert created
    order_manager = OrderManager(repository, broker, _settings(tmp_path))
    trading = TradingService(
        config,
        repository,
        broker,
        order_manager,
        PositionManager(config, repository, broker),
        TakeProfitManager(repository, broker, order_manager),
        MarketClock(),
    )
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    approval_id, token = trading.create_review_approval(signal_id, now=now)
    quote = trading.consume_review(approval_id, token, now=now)

    equity = managed_equity(config, repository, broker)
    target = target_quantity(
        equity,
        Decimal("0.10"),
        quote.limit_price,
        config.global_.buy_fee,
    )
    assert repository.get_core_position("TQQQ")["qty"] == 19
    assert quote.quantity == max(0, target - 19)
    assert quote.quantity == 2


def test_reconciliation_fails_safe_for_local_open_order_without_broker_id(
    tmp_path, config
):
    repository = SQLiteRepository(tmp_path / "operational.db", config)
    broker = DryRunBroker(
        {"TQQQ": Decimal("100"), "SOXL": Decimal("50"), "SGOV": Decimal("100")},
        buying_power=Decimal("20000"),
    )
    assert repository.reserve_order(
        client_order_id="STRANDED",
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("99"),
        quantity=1,
        purpose="ENTRY_1",
    )

    result = ReconciliationService(config, repository, broker).run()

    assert "TQQQ" in result
    assert "OPEN_ORDER_WITHOUT_BROKER_ID:CREATED" in result["TQQQ"]
    assert repository.get_position("TQQQ").state.value == "SAFE_MODE"


def test_restore_recreates_only_provable_zero_fill_pending_orders(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "operational.db", config)
    for client_id, status, filled in (
        ("PENDING", "PENDING", 0),
        ("PARTIAL", "PARTIAL_FILLED", 1),
    ):
        assert repository.reserve_order(
            client_order_id=client_id,
            signal_id=None,
            cycle_id=None,
            symbol="TQQQ",
            side="BUY",
            order_type="LIMIT",
            price=Decimal("90"),
            quantity=2,
            purpose="ENTRY_1",
        )
        repository.update_order(
            client_id,
            status=status,
            broker_order_id=f"DRY-0000000{1 if client_id == 'PENDING' else 2}",
            filled_qty=filled,
            average_fill_price=Decimal("90") if filled else None,
        )

    broker = DryRunBroker(
        {"TQQQ": Decimal("100"), "SOXL": Decimal("50"), "SGOV": Decimal("100")},
        buying_power=Decimal("20000"),
    )
    restore_dry_run_holdings(repository, broker)

    assert "DRY-00000001" in broker.orders
    assert "DRY-00000002" not in broker.orders
    result = ReconciliationService(config, repository, broker).run()
    assert "TQQQ" in result
    assert "BROKER_DB_OPEN_ORDER_MISMATCH" in result["TQQQ"]


def test_dry_run_broker_applies_cumulative_partial_fills_by_delta():
    broker = DryRunBroker({"TQQQ": Decimal("100")}, buying_power=Decimal("1000"))
    request = OrderRequest(
        client_order_id="PARTIAL-DELTA",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=5,
        price=Decimal("90"),
        purpose="ENTRY_1",
    )
    receipt = broker.place_order(request)
    assert receipt.status == "PENDING"
    order = broker.orders[receipt.broker_order_id]

    order["status"] = "PARTIAL_FILLED"
    order["execution"]["filledQuantity"] = "2"
    order["execution"]["averageFilledPrice"] = "89"
    order["execution"]["filledAmount"] = "178"
    broker._apply_fill(order)
    assert broker.get_holdings("TQQQ")[0]["quantity"] == "2"
    assert broker.get_buying_power("USD") == Decimal("822")

    order["execution"]["filledQuantity"] = "5"
    order["execution"]["averageFilledPrice"] = "90"
    order["execution"]["filledAmount"] = "450"
    broker._apply_fill(order)
    assert broker.get_holdings("TQQQ")[0]["quantity"] == "5"
    assert broker.get_buying_power("USD") == Decimal("550")
    broker._apply_fill(order)
    assert broker.get_buying_power("USD") == Decimal("550")
