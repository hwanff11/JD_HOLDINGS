from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import make_score, make_snapshot

from jd_holdings.application.broker import DryRunBroker, MarketDataDryRunBroker
from jd_holdings.application.database import ApprovalError, SQLiteRepository
from jd_holdings.application.idle_cash_manager import (
    IdleCashManager,
    IdleCashReleasePending,
)
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.application.trading_service import TradingService
from jd_holdings.bot import restore_dry_run_holdings
from jd_holdings.core.execution import max_chase_price
from jd_holdings.core.strategy import evaluate_entry
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
    sweep_order = next(order for order in broker.orders.values() if order["side"] == "BUY")
    assert Decimal(str(sweep_order["price"])) == Decimal("100.01")


def test_sgov_limits_choose_best_price_from_unsorted_orderbook(tmp_path, config):
    class UnsortedOrderbookBroker(DryRunBroker):
        def get_orderbook(self, symbol):
            return {
                "asks": [{"price": "100.03"}, {"price": "100.01"}],
                "bids": [{"price": "99.97"}, {"price": "99.99"}],
            }

    broker = UnsortedOrderbookBroker(
        {"SGOV": Decimal("100")}, buying_power=Decimal("20000")
    )
    _, manager = build_manager(tmp_path, config, broker)

    assert manager._buy_limit(Decimal("100")) == Decimal("100.02")
    assert manager._sell_limit(Decimal("100")) == Decimal("99.98")


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
    assert Decimal(str(sell_order["price"])) == Decimal("99.99")


def test_pending_release_resumes_to_final_approval_without_signal_retry(tmp_path, config):
    broker = PendingReleaseBroker(
        {"SGOV": Decimal("100"), "TQQQ": Decimal("100")},
        buying_power=Decimal("20000"),
    )
    repository, manager = build_manager(tmp_path, config, broker)
    manager.run_once(now=OPEN_SESSION)
    position_manager = PositionManager(config, repository, broker)
    tp_manager = TakeProfitManager(repository, broker, manager.order_manager)
    trading = TradingService(
        config,
        repository,
        broker,
        manager.order_manager,
        position_manager,
        tp_manager,
        idle_cash_manager=manager,
    )
    snapshot = make_snapshot(close=Decimal("100"))
    score = make_score(84)
    decision = evaluate_entry(snapshot, score, repository.get_position("TQQQ"), config)
    signal_id, _ = repository.create_signal(
        symbol="TQQQ",
        trade_date=date(2026, 8, 4),
        score=score,
        atr_pct=Decimal("0.05"),
        decision=decision,
        signal_close=snapshot.close,
        max_chase_price=max_chase_price(snapshot.close, config),
        valid_until=datetime.now(UTC) + timedelta(days=1),
        code_version="test",
        cycle_id=None,
    )
    broker.pending_sells = True
    approval_id, token = trading.create_review_approval(signal_id)
    premarket = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)

    with pytest.raises(IdleCashReleasePending) as pending:
        trading.consume_review(approval_id, token, now=premarket)
    assert pending.value.signal_id == signal_id
    assert repository.get_cash_release_intent(signal_id)["status"] == "WAITING_SGOV_FILL"
    assert trading.active_signals() == []

    broker.pending_sells = False
    broker.fill_open_orders("SGOV")
    manager.refresh_orders()
    quotes = trading.resume_cash_releases(now=premarket)

    assert len(quotes) == 1
    assert quotes[0].execution_approval_id is not None
    assert repository.get_cash_release_intent(signal_id)["status"] == "AWAITING_EXECUTION"
    before_orders = len(broker.orders)
    assert manager.run_once(now=OPEN_SESSION) == []
    assert len(broker.orders) == before_orders


def test_stale_sgov_limit_is_canceled_for_repricing(tmp_path, config):
    broker = PendingReleaseBroker({"SGOV": Decimal("100")}, buying_power=Decimal("20000"))
    repository, manager = build_manager(tmp_path, config, broker)
    manager.run_once(now=OPEN_SESSION)
    broker.pending_sells = True
    with pytest.raises(IdleCashReleasePending):
        manager.ensure_buying_power(Decimal("4000"))
    pending = next(
        order
        for order in repository.open_orders("SGOV")
        if order["purpose"] == "SGOV_ENTRY_RELEASE"
    )
    old = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE orders SET created_at = ? WHERE client_order_id = ?",
            (old, pending["client_order_id"]),
        )

    events = manager.refresh_orders()

    assert any("재가격" in event for event in events)
    assert not repository.open_orders("SGOV")
    with pytest.raises(IdleCashReleasePending):
        manager.ensure_buying_power(Decimal("4000"))
    sell_orders = [order for order in broker.orders.values() if order["side"] == "SELL"]
    assert len(sell_orders) == 2


def test_cancel_really_invalidates_approval_and_cash_intent(tmp_path, config):
    broker = PendingReleaseBroker(
        {"SGOV": Decimal("100"), "TQQQ": Decimal("100")},
        buying_power=Decimal("20000"),
    )
    repository, manager = build_manager(tmp_path, config, broker)
    manager.run_once(now=OPEN_SESSION)
    position_manager = PositionManager(config, repository, broker)
    tp_manager = TakeProfitManager(repository, broker, manager.order_manager)
    trading = TradingService(
        config,
        repository,
        broker,
        manager.order_manager,
        position_manager,
        tp_manager,
        idle_cash_manager=manager,
    )
    snapshot = make_snapshot(close=Decimal("100"))
    score = make_score(84)
    decision = evaluate_entry(snapshot, score, repository.get_position("TQQQ"), config)
    signal_id, _ = repository.create_signal(
        symbol="TQQQ",
        trade_date=date(2026, 8, 4),
        score=score,
        atr_pct=Decimal("0.05"),
        decision=decision,
        signal_close=snapshot.close,
        max_chase_price=max_chase_price(snapshot.close, config),
        valid_until=datetime.now(UTC) + timedelta(days=1),
        code_version="test",
        cycle_id=None,
    )
    approval_id, token = trading.create_review_approval(signal_id)
    trading.cancel_approval(approval_id)
    with pytest.raises(ApprovalError, match="취소"):
        trading.consume_review(approval_id, token)

    broker.pending_sells = True
    approval_id, token = trading.create_review_approval(signal_id)
    with pytest.raises(IdleCashReleasePending):
        trading.consume_review(
            approval_id, token, now=datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
        )
    trading.cancel_cash_release(signal_id)
    assert repository.get_cash_release_intent(signal_id)["status"] == "CANCELED"
    assert not [
        order
        for order in repository.open_orders("SGOV")
        if order["purpose"] == "SGOV_ENTRY_RELEASE"
    ]


def test_final_execution_rechecks_cash_buffer(tmp_path, config):
    broker = PendingReleaseBroker(
        {"SGOV": Decimal("100"), "TQQQ": Decimal("100")},
        buying_power=Decimal("20000"),
    )
    repository, manager = build_manager(tmp_path, config, broker)
    manager.run_once(now=OPEN_SESSION)
    trading = TradingService(
        config,
        repository,
        broker,
        manager.order_manager,
        PositionManager(config, repository, broker),
        TakeProfitManager(repository, broker, manager.order_manager),
        idle_cash_manager=manager,
    )
    snapshot = make_snapshot(close=Decimal("100"))
    score = make_score(84)
    decision = evaluate_entry(snapshot, score, repository.get_position("TQQQ"), config)
    signal_id, _ = repository.create_signal(
        symbol="TQQQ",
        trade_date=date(2026, 8, 4),
        score=score,
        atr_pct=Decimal("0.05"),
        decision=decision,
        signal_close=snapshot.close,
        max_chase_price=max_chase_price(snapshot.close, config),
        valid_until=datetime.now(UTC) + timedelta(days=1),
        code_version="test",
        cycle_id=None,
    )
    approval_id, token = trading.create_review_approval(signal_id)
    premarket = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    broker.pending_sells = True
    with pytest.raises(IdleCashReleasePending):
        trading.consume_review(approval_id, token, now=premarket)
    broker.pending_sells = False
    broker.fill_open_orders("SGOV")
    manager.refresh_orders()
    quote = trading.resume_cash_releases(now=premarket)[0]
    order_total = (
        Decimal(quote.quantity)
        * quote.limit_price
        * (Decimal("1") + config.global_.buy_fee)
    )
    broker.buying_power = order_total

    with pytest.raises(ApprovalError, match="매수가능금액"):
        trading.execute(
            quote.execution_approval_id,
            quote.execution_token,
            now=premarket,
        )
    assert repository.get_cash_release_intent(signal_id)["status"] == "CANCELED"


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
