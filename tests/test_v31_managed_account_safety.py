from __future__ import annotations

from decimal import Decimal

import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.idle_cash_manager import IdleCashManager
from jd_holdings.application.managed_account import (
    available_managed_cash,
    managed_cash_balance,
    managed_equity,
)
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.bot import restore_dry_run_holdings, restore_dry_run_orders
from jd_holdings.core.models import OrderRequest
from jd_holdings.settings import RuntimeSettings


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "managed.db",
        log_path=tmp_path / "managed.log",
    )


def test_managed_equity_and_cash_exclude_personal_account_assets(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "managed.db", config)
    broker = DryRunBroker(
        {"TQQQ": Decimal("100"), "SOXL": Decimal("50"), "SGOV": Decimal("100")},
        buying_power=Decimal("50000"),
    )
    broker.holdings["TQQQ"] = {
        "quantity": 7,
        "averagePurchasePrice": Decimal("80"),
    }
    broker.holdings["SGOV"] = {
        "quantity": 40,
        "averagePurchasePrice": Decimal("99"),
    }

    assert managed_cash_balance(config, repository) == Decimal("20000")
    assert available_managed_cash(config, repository, broker) == Decimal("20000")
    assert managed_equity(config, repository, broker) == Decimal("20000")


def test_central_buy_gate_reserves_managed_cash_not_personal_cash(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "managed.db", config)
    broker = DryRunBroker({"TQQQ": Decimal("100")}, buying_power=Decimal("50000"))
    manager = OrderManager(repository, broker, _settings(tmp_path))

    first = OrderRequest(
        client_order_id="MANAGED-FIRST",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=100,
        price=Decimal("99"),
        purpose="ENTRY_1",
    )
    receipt = manager.submit(first, cycle_id=None)
    assert receipt.status == "PENDING"
    assert broker.get_buying_power("USD") == Decimal("50000")

    second = OrderRequest(
        client_order_id="MANAGED-SECOND",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=110,
        price=Decimal("99"),
        purpose="ENTRY_1",
    )
    with pytest.raises(RuntimeError, match="JDSS 관리현금"):
        manager.submit(second, cycle_id=None)
    assert repository.get_order_by_client_id("MANAGED-SECOND") is None


def test_realized_pnl_and_fees_survive_dry_run_restart(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "managed.db", config)
    for client_id, side, price in (
        ("BUY-ROUNDTRIP", "BUY", Decimal("100")),
        ("SELL-ROUNDTRIP", "SELL", Decimal("110")),
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


def test_reconciliation_fails_safe_for_open_order_without_broker_id(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "managed.db", config)
    broker = DryRunBroker(
        {"TQQQ": Decimal("100"), "SOXL": Decimal("50"), "SGOV": Decimal("100")},
        buying_power=Decimal("20000"),
    )
    assert repository.reserve_order(
        client_order_id="STRANDED-LOCAL",
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

    assert "OPEN_ORDER_WITHOUT_BROKER_ID:CREATED" in result["TQQQ"]
    assert repository.get_position("TQQQ").state.value == "SAFE_MODE"


def test_restart_skips_ambiguous_partial_order_but_keeps_sequence(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "managed.db", config)
    for client_id, status, broker_id, filled in (
        ("UNKNOWN", "UNKNOWN", "DRY-00000009", 0),
        ("PARTIAL", "PARTIAL_FILLED", "DRY-00000010", 1),
        ("PENDING", "PENDING", "DRY-00000011", 0),
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
            broker_order_id=broker_id,
            filled_qty=filled,
            average_fill_price=Decimal("90") if filled else None,
        )

    broker = DryRunBroker(
        {"TQQQ": Decimal("100"), "SOXL": Decimal("50"), "SGOV": Decimal("100")},
        buying_power=Decimal("20000"),
    )
    restore_dry_run_orders(repository, broker)

    assert set(broker.orders) == {"DRY-00000011"}
    assert broker.sequence == 11
    result = ReconciliationService(config, repository, broker).run()
    assert "BROKER_DB_OPEN_ORDER_MISMATCH" in result["TQQQ"]


def test_sgov_partial_restart_sets_idle_cash_safe_mode_instead_of_crashing(
    tmp_path, config
):
    repository = SQLiteRepository(tmp_path / "managed.db", config)
    assert repository.reserve_order(
        client_order_id="SGOV-PARTIAL",
        signal_id=None,
        cycle_id=None,
        symbol="SGOV",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=2,
        purpose="SGOV_SWEEP_BUY",
    )
    repository.update_order(
        "SGOV-PARTIAL",
        status="PARTIAL_FILLED",
        broker_order_id="DRY-00000012",
        filled_qty=1,
        average_fill_price=Decimal("100"),
    )
    broker = DryRunBroker({"SGOV": Decimal("100")}, buying_power=Decimal("19900"))
    restore_dry_run_orders(repository, broker)
    manager = IdleCashManager(
        config,
        repository,
        broker,
        OrderManager(repository, broker, _settings(tmp_path)),
    )

    events = manager.refresh_orders()

    assert events == ["SGOV 열린 주문 확인 불가: SAFE_MODE"]
    assert repository.get_system_value("idle_cash_safe_mode") == "1"


def test_dry_run_cumulative_partial_fill_is_applied_by_delta():
    broker = DryRunBroker({"TQQQ": Decimal("100")}, buying_power=Decimal("1000"))
    receipt = broker.place_order(
        OrderRequest(
            client_order_id="PARTIAL-DELTA",
            symbol="TQQQ",
            side="BUY",
            order_type="LIMIT",
            quantity=5,
            price=Decimal("90"),
            purpose="ENTRY_1",
        )
    )
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
