from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import make_score, make_snapshot

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.order_monitor import OrderMonitor
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.application.trading_service import TradingService
from jd_holdings.core.enums import PositionState
from jd_holdings.core.execution import max_chase_price
from jd_holdings.core.strategy import evaluate_entry
from jd_holdings.core.take_profit import ceil_to_tick
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.settings import RuntimeSettings


def build_services(tmp_path, config, broker=None):
    repository = SQLiteRepository(tmp_path / "flow.db", config)
    settings = RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "flow.db",
        log_path=tmp_path / "flow.log",
    )
    broker = broker or DryRunBroker({"TQQQ": Decimal("100")})
    order_manager = OrderManager(repository, broker, settings)
    position_manager = PositionManager(config, repository, broker)
    tp_manager = TakeProfitManager(repository, broker, order_manager)
    market_clock = MarketClock()
    trading = TradingService(
        config,
        repository,
        broker,
        order_manager,
        position_manager,
        tp_manager,
        market_clock,
    )
    monitor = OrderMonitor(
        config,
        repository,
        broker,
        order_manager,
        position_manager,
        tp_manager,
        market_clock,
    )
    return repository, broker, trading, monitor


def create_approved_entry(repository, trading, config):
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
    review_id, review_token = trading.create_review_approval(signal_id)
    premarket = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    quote = trading.consume_review(review_id, review_token, now=premarket)
    return quote, premarket


def fill_tp1_completely(repository, broker, monitor):
    tp1_local = next(
        order for order in repository.open_orders("TQQQ") if order["purpose"] == "TP1"
    )
    tp1 = broker.orders[tp1_local["broker_order_id"]]
    target = int(tp1_local["qty"])
    tp1["status"] = "FILLED"
    tp1["execution"]["filledQuantity"] = str(target)
    tp1["execution"]["averageFilledPrice"] = tp1["price"]
    broker._apply_fill(tp1)
    events = monitor.run_once(now=datetime(2026, 8, 10, 22, 0, tzinfo=UTC))
    return events


def test_two_step_dry_run_order_flow(tmp_path, config):
    repository, broker, trading, _ = build_services(tmp_path, config)
    quote, premarket = create_approved_entry(repository, trading, config)
    assert quote.quantity == 39
    receipt = trading.execute(quote.execution_approval_id, quote.execution_token, now=premarket)
    assert receipt.status == "FILLED"
    position = repository.get_position("TQQQ")
    assert position.state == PositionState.HOLDING_1ST
    assert position.quantity == quote.quantity
    assert repository.active_tp_plan("TQQQ") is not None
    assert len(repository.open_orders("TQQQ")) == 2


def test_partial_tp_is_applied_cumulatively_and_recovered(tmp_path, config):
    repository, broker, trading, monitor = build_services(tmp_path, config)
    quote, premarket = create_approved_entry(repository, trading, config)
    initial_quantity = quote.quantity
    trading.execute(quote.execution_approval_id, quote.execution_token, now=premarket)
    tp1_local = next(
        order for order in repository.open_orders("TQQQ") if order["purpose"] == "TP1"
    )
    tp1 = broker.orders[tp1_local["broker_order_id"]]
    tp1["status"] = "PARTIAL_FILLED"
    tp1["execution"]["filledQuantity"] = "2"
    tp1["execution"]["averageFilledPrice"] = tp1["price"]
    broker._apply_fill(tp1)

    monitor.run_once()
    assert repository.get_position("TQQQ").quantity == initial_quantity - 2
    assert repository.get_order_by_client_id(tp1_local["client_order_id"])["applied"] == 0

    tp1["status"] = "CANCELED"
    events = monitor.run_once()
    recovered = repository.open_orders("TQQQ")
    assert any("자동 복구" in event for event in events)
    assert sum(int(order["qty"]) for order in recovered) == initial_quantity - 2
    assert repository.get_order_by_client_id(tp1_local["client_order_id"])["applied"] == 1


def test_tp1_completion_resets_tp2_clock_and_switches_after_20_sessions(tmp_path, config):
    repository, broker, trading, monitor = build_services(tmp_path, config)
    quote, premarket = create_approved_entry(repository, trading, config)
    trading.execute(quote.execution_approval_id, quote.execution_token, now=premarket)

    events = fill_tp1_completely(repository, broker, monitor)
    position = repository.get_position("TQQQ")
    assert position.state == PositionState.PARTIAL_TP_1
    assert any("TP2 기준시점 재설정" in event for event in events)
    open_after_tp1 = repository.open_orders("TQQQ")
    assert [order["purpose"] for order in open_after_tp1] == ["TP2"]

    monitor.run_once(now=datetime(2026, 8, 20, 22, 0, tzinfo=UTC))
    assert [order["purpose"] for order in repository.open_orders("TQQQ")] == ["TP2"]

    events = monitor.run_once(now=datetime(2026, 9, 15, 22, 0, tzinfo=UTC))
    remainder = next(
        order
        for order in repository.open_orders("TQQQ")
        if order["purpose"] == "REMAINDER_EXIT"
    )
    expected_price = ceil_to_tick(
        position.average_price
        * (Decimal("1") + config.take_profit.remainder_exit.target_from_avg)
    )
    assert Decimal(str(remainder["price"])) == expected_price
    assert int(remainder["qty"]) == position.quantity
    assert any("잔량 +2% 회수주문 전환" in event for event in events)


class FailingBroker(DryRunBroker):
    def place_order(self, request):
        raise TimeoutError("simulated lost response")


def test_unknown_submission_blocks_position_and_reconciliation_enters_safe_mode(tmp_path, config):
    broker = FailingBroker({"TQQQ": Decimal("100")})
    repository, broker, trading, _ = build_services(tmp_path, config, broker)
    quote, premarket = create_approved_entry(repository, trading, config)
    with pytest.raises(TimeoutError):
        trading.execute(quote.execution_approval_id, quote.execution_token, now=premarket)
    assert repository.get_position("TQQQ").state == PositionState.WAITING_1ST_FILL
    issues = ReconciliationService(config, repository, broker).run()
    assert "UNKNOWN_ORDER_WITHOUT_BROKER_ID" in issues["TQQQ"]
    assert repository.get_position("TQQQ").state == PositionState.SAFE_MODE
