from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import make_score, make_snapshot

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import ApprovalError, SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.order_monitor import OrderMonitor
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.application.trading_service import TradingService
from jd_holdings.core.enums import PositionState
from jd_holdings.core.execution import max_chase_price
from jd_holdings.core.strategy import evaluate_entry
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
    return monitor.run_once(now=datetime(2026, 8, 10, 22, 0, tzinfo=UTC))


def test_two_step_dry_run_order_flow(tmp_path, config):
    repository, _, trading, _ = build_services(tmp_path, config)
    quote, premarket = create_approved_entry(repository, trading, config)
    assert quote.quantity == 79
    receipt = trading.execute(quote.execution_approval_id, quote.execution_token, now=premarket)
    assert receipt.status == "FILLED"
    position = repository.get_position("TQQQ")
    assert position.state == PositionState.HOLDING_1ST
    assert position.quantity == quote.quantity
    assert repository.active_tp_plan("TQQQ") is not None
    assert len(repository.open_orders("TQQQ")) == 2


def test_signal_command_filters_and_invalidates_db_signal_below_minimum(tmp_path, config):
    repository, _, trading, _ = build_services(tmp_path, config)
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
    with repository.transaction() as connection:
        connection.execute("UPDATE signals SET score = 50 WHERE signal_id = ?", (signal_id,))

    assert trading.active_signals() == []
    invalid = repository.get_signal(signal_id)
    assert invalid["status"] == "INVALID"
    assert invalid["processed"] == 1
    assert invalid["expired_reason"] == "SIGNAL_SCORE_BELOW_MINIMUM"
    with pytest.raises(ApprovalError, match="활성 상태가 아닌 신호"):
        trading.create_review_approval(signal_id)


def test_latest_failed_analysis_invalidates_previous_active_signal(tmp_path, config):
    repository, _, _, _ = build_services(tmp_path, config)
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

    assert repository.invalidate_active_signals(
        "TQQQ", reason="CURRENT_ENTRY_GATES_FAILED"
    ) == 1
    invalid = repository.get_signal(signal_id)
    assert invalid["status"] == "INVALID"
    assert invalid["expired_reason"] == "CURRENT_ENTRY_GATES_FAILED"


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
    tp1["execution"]["filledQuantity"] = "1"
    tp1["execution"]["averageFilledPrice"] = tp1["price"]
    broker._apply_fill(tp1)

    monitor.run_once()
    assert repository.get_position("TQQQ").quantity == initial_quantity - 1
    assert repository.get_order_by_client_id(tp1_local["client_order_id"])["applied"] == 0

    tp1["status"] = "CANCELED"
    events = monitor.run_once()
    recovered = repository.open_orders("TQQQ")
    assert any("자동 복구" in event for event in events)
    assert sum(int(order["qty"]) for order in recovered) == initial_quantity - 1
    assert repository.get_order_by_client_id(tp1_local["client_order_id"])["applied"] == 1


def test_tp1_completion_keeps_tp2_without_time_based_remainder_exit(tmp_path, config):
    repository, broker, trading, monitor = build_services(tmp_path, config)
    quote, premarket = create_approved_entry(repository, trading, config)
    trading.execute(quote.execution_approval_id, quote.execution_token, now=premarket)

    events = fill_tp1_completely(repository, broker, monitor)
    position = repository.get_position("TQQQ")
    assert position.state == PositionState.PARTIAL_TP_1
    assert any("TP2 주문 재확인" in event for event in events)
    assert [order["purpose"] for order in repository.open_orders("TQQQ")] == ["TP2"]

    events = monitor.run_once(now=datetime(2026, 9, 15, 22, 0, tzinfo=UTC))
    assert not any("거래일 경과" in event for event in events)
    assert [order["purpose"] for order in repository.open_orders("TQQQ")] == ["TP2"]


def test_tp2_recovery_keeps_tp2_after_long_elapsed_time(tmp_path, config):
    repository, broker, trading, monitor = build_services(tmp_path, config)
    quote, premarket = create_approved_entry(repository, trading, config)
    trading.execute(quote.execution_approval_id, quote.execution_token, now=premarket)
    fill_tp1_completely(repository, broker, monitor)

    tp2_local = next(
        order for order in repository.open_orders("TQQQ") if order["purpose"] == "TP2"
    )
    broker.orders[tp2_local["broker_order_id"]]["status"] = "CANCELED"
    events = monitor.run_once(now=datetime(2026, 8, 20, 22, 0, tzinfo=UTC))
    assert any("자동 복구" in event for event in events)
    recovered_tp2 = next(
        order for order in repository.open_orders("TQQQ") if order["purpose"] == "TP2"
    )
    assert recovered_tp2["client_order_id"] != tp2_local["client_order_id"]

    events = monitor.run_once(now=datetime(2026, 9, 8, 22, 0, tzinfo=UTC))
    assert not any("20거래일 경과" in event for event in events)
    assert [order["purpose"] for order in repository.open_orders("TQQQ")] == ["TP2"]


def test_reconciliation_accepts_open_tp2_after_restart(tmp_path, config):
    repository, broker, trading, monitor = build_services(tmp_path, config)
    quote, premarket = create_approved_entry(repository, trading, config)
    trading.execute(quote.execution_approval_id, quote.execution_token, now=premarket)
    fill_tp1_completely(repository, broker, monitor)
    monitor.run_once(now=datetime(2026, 9, 15, 22, 0, tzinfo=UTC))

    assert [order["purpose"] for order in repository.open_orders("TQQQ")] == ["TP2"]
    assert repository.get_position("TQQQ").state == PositionState.PARTIAL_TP_1
    issues = ReconciliationService(config, repository, broker).run()
    assert any(
        issue.startswith("V322_DIRECT_BOOSTER_STATE_PRESENT")
        for issue in issues["TQQQ"]
    )
    assert "V322_DIRECT_TP_PLAN_PRESENT" in issues["TQQQ"]


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
