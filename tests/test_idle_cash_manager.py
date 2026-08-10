from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from jd_holdings.application.broker import DryRunBroker, MarketDataDryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.idle_cash_manager import (
    IdleCashManager,
    IdleCashReleasePending,
)
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.bot import restore_dry_run_holdings
from jd_holdings.settings import RuntimeSettings


def build_manager(tmp_path, config, broker):
    repository = SQLiteRepository(tmp_path / "idle-cash.db", config)
    settings = RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "idle-cash.db",
        log_path=tmp_path / "idle-cash.log",
    )
    order_manager = OrderManager(repository, broker, settings)
    return repository, IdleCashManager(config, repository, broker, order_manager)


OPEN_SESSION = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)


class PendingReleaseBroker(DryRunBroker):
    pending_sells = False

    def place_order(self, request):
        if not self.pending_sells or request.side != "SELL" or request.price is None:
            return super().place_order(request)
        original = self.prices[request.symbol]
        self.prices[request.symbol] = request.price - Decimal("1")
        try:
            return super().place_order(request)
        finally:
            self.prices[request.symbol] = original


def test_sweep_deposits_only_allocated_idle_cash(tmp_path, config):
    broker = DryRunBroker({"SGOV": Decimal("100")}, buying_power=Decimal("50000"))
    repository, manager = build_manager(tmp_path, config, broker)

    events = manager.run_once(now=OPEN_SESSION)

    state = repository.get_idle_cash_state()
    assert events and "예치" in events[0]
    assert state.managed_quantity == 197
    assert broker.get_buying_power("USD") == Decimal("30300")
    assert broker.get_holdings("SGOV")[0]["quantity"] == "197"


def test_sweep_does_not_submit_during_closed_session(tmp_path, config):
    broker = DryRunBroker({"SGOV": Decimal("100")}, buying_power=Decimal("20000"))
    repository, manager = build_manager(tmp_path, config, broker)

    events = manager.run_once(now=datetime(2026, 8, 9, 15, 0, tzinfo=UTC))

    assert events == []
    assert repository.get_idle_cash_state().managed_quantity == 0
    assert broker.orders == {}


def test_entry_liquidity_sells_managed_sgov_first(tmp_path, config):
    broker = DryRunBroker({"SGOV": Decimal("100")}, buying_power=Decimal("20000"))
    repository, manager = build_manager(tmp_path, config, broker)
    manager.run_once(now=OPEN_SESSION)
    before = repository.get_idle_cash_state().managed_quantity

    manager.ensure_buying_power(Decimal("4000"))

    after = repository.get_idle_cash_state().managed_quantity
    assert after < before
    assert broker.get_buying_power("USD") >= Decimal("4250")
    sell_order = next(order for order in broker.orders.values() if order["side"] == "SELL")
    local = repository.get_order_by_client_id(sell_order["clientOrderId"])
    assert local is not None and local["purpose"] == "SGOV_ENTRY_RELEASE"


def test_pending_release_blocks_entry_and_canceled_order_can_retry(tmp_path, config):
    broker = PendingReleaseBroker({"SGOV": Decimal("100")}, buying_power=Decimal("20000"))
    repository, manager = build_manager(tmp_path, config, broker)
    manager.run_once(now=OPEN_SESSION)
    broker.pending_sells = True

    with pytest.raises(IdleCashReleasePending, match="체결"):
        manager.ensure_buying_power(Decimal("4000"))

    pending = next(order for order in repository.open_orders("SGOV") if order["side"] == "SELL")
    broker.cancel_order(pending["broker_order_id"])
    manager.refresh_orders()
    broker.pending_sells = False
    manager.ensure_buying_power(Decimal("4000"))
    sell_ids = {
        order["clientOrderId"]
        for order in broker.orders.values()
        if order["side"] == "SELL"
    }
    assert len(sell_ids) == 2


def test_existing_personal_sgov_is_not_adopted_or_reconciled_as_jdss(tmp_path, config):
    broker = DryRunBroker({"SGOV": Decimal("100")}, buying_power=Decimal("20000"))
    broker.holdings["SGOV"] = {
        "quantity": 10,
        "averagePurchasePrice": Decimal("99"),
    }
    repository, manager = build_manager(tmp_path, config, broker)

    assert ReconciliationService(config, repository, broker).run() == {}
    assert repository.get_idle_cash_state().managed_quantity == 0
    manager.run_once(now=OPEN_SESSION)
    snapshot = manager.snapshot()
    assert snapshot.broker_quantity - snapshot.state.managed_quantity == 10


def test_partial_sgov_fills_are_applied_by_cumulative_delta(tmp_path, config):
    broker = DryRunBroker({"SGOV": Decimal("100")}, buying_power=Decimal("20000"))
    repository, _ = build_manager(tmp_path, config, broker)
    client_id = "JDSS-SGOV-TEST-PARTIAL"
    assert repository.reserve_order(
        client_order_id=client_id,
        signal_id=None,
        cycle_id=None,
        symbol="SGOV",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=5,
        purpose="SGOV_SWEEP_BUY",
    )
    repository.update_order(
        client_id,
        status="PARTIAL_FILLED",
        broker_order_id="SGOV-PARTIAL-1",
        filled_qty=2,
        average_fill_price=Decimal("100"),
    )
    first = repository.apply_idle_cash_fill(client_id)
    assert first.managed_quantity == 2
    assert first.average_price == Decimal("100")

    repository.update_order(
        client_id,
        status="FILLED",
        filled_qty=5,
        average_fill_price=Decimal("99"),
    )
    final = repository.apply_idle_cash_fill(client_id)
    assert final.managed_quantity == 5
    assert final.average_price == Decimal("99")
    assert repository.apply_idle_cash_fill(client_id) == final


def test_dry_run_restart_restores_managed_sgov_ledger(tmp_path, config):
    broker = DryRunBroker({"SGOV": Decimal("100")}, buying_power=Decimal("20000"))
    repository, manager = build_manager(tmp_path, config, broker)
    manager.run_once(now=OPEN_SESSION)
    state = repository.get_idle_cash_state()

    restarted = MarketDataDryRunBroker(data_source=object())
    restore_dry_run_holdings(repository, restarted)

    assert restarted.holdings["SGOV"]["quantity"] == state.managed_quantity
    assert restarted.buying_power == Decimal("20000") - (
        state.average_price * state.managed_quantity
    )
