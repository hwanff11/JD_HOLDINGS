from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

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
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.settings import RuntimeSettings


def build_services(tmp_path, config, broker):
    repository = SQLiteRepository(tmp_path / "final-dry-run.db", config)
    settings = RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "final-dry-run.db",
        log_path=tmp_path / "final-dry-run.log",
    )
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
    return repository, trading, monitor


def create_and_approve_first_entry(repository, trading, config):
    snapshot = make_snapshot(close=Decimal("100"))
    score = make_score(84)
    decision = evaluate_entry(snapshot, score, repository.get_position("TQQQ"), config)
    assert decision.allowed

    signal_id, created = repository.create_signal(
        symbol="TQQQ",
        trade_date=date(2026, 8, 4),
        score=score,
        atr_pct=Decimal("0.05"),
        decision=decision,
        signal_close=snapshot.close,
        max_chase_price=max_chase_price(snapshot.close, config),
        valid_until=datetime.now(UTC) + timedelta(days=1),
        code_version="final-dry-run",
        cycle_id=None,
    )
    assert created

    review_id, review_token = trading.create_review_approval(signal_id)
    execution_time = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    quote = trading.consume_review(review_id, review_token, now=execution_time)
    receipt = trading.execute(
        quote.execution_approval_id,
        quote.execution_token,
        now=execution_time,
    )
    assert receipt.status == "FILLED"
    return quote


def fill_order_at_limit(broker, local_order):
    broker_order = broker.orders[local_order["broker_order_id"]]
    broker_order["status"] = "FILLED"
    broker_order["execution"]["filledQuantity"] = str(local_order["qty"])
    broker_order["execution"]["averageFilledPrice"] = str(local_order["price"])
    broker_order["execution"]["filledAmount"] = str(
        Decimal(str(local_order["price"])) * Decimal(str(local_order["qty"]))
    )
    broker._apply_fill(broker_order)


def test_final_production_flow_end_to_end_with_restart(tmp_path, config):
    """Exercise the complete FINAL lifecycle without reaching a real broker."""
    broker = DryRunBroker({"TQQQ": Decimal("100")})
    repository, trading, monitor = build_services(tmp_path, config, broker)

    # 1) Two-step user approval -> first buy fill -> TP1/TP2 creation.
    quote = create_and_approve_first_entry(repository, trading, config)
    position = repository.get_position("TQQQ")
    assert position.state == PositionState.HOLDING_1ST
    assert position.quantity == quote.quantity
    assert {order["purpose"] for order in repository.open_orders("TQQQ")} == {"TP1", "TP2"}

    # 2) Complete TP1. The remaining position must stay under TP2 management.
    tp1 = next(order for order in repository.open_orders("TQQQ") if order["purpose"] == "TP1")
    fill_order_at_limit(broker, tp1)
    monitor.run_once(now=datetime(2026, 8, 10, 22, 0, tzinfo=UTC))
    position = repository.get_position("TQQQ")
    assert position.state == PositionState.PARTIAL_TP_1
    assert position.quantity > 0
    assert [order["purpose"] for order in repository.open_orders("TQQQ")] == ["TP2"]

    # 3) Simulate a TP2 cancellation/recovery before the 20-session deadline.
    old_tp2 = next(order for order in repository.open_orders("TQQQ") if order["purpose"] == "TP2")
    broker.orders[old_tp2["broker_order_id"]]["status"] = "CANCELED"
    events = monitor.run_once(now=datetime(2026, 8, 20, 22, 0, tzinfo=UTC))
    assert any("자동 복구" in event for event in events)
    recovered_tp2 = next(
        order for order in repository.open_orders("TQQQ") if order["purpose"] == "TP2"
    )
    assert recovered_tp2["client_order_id"] != old_tp2["client_order_id"]

    # 4) At 20 completed sessions, switch the remaining shares to avg +2%.
    events = monitor.run_once(now=datetime(2026, 9, 8, 22, 0, tzinfo=UTC))
    assert any("20거래일 경과" in event for event in events)
    remainder = next(
        order for order in repository.open_orders("TQQQ") if order["purpose"] == "REMAINDER_EXIT"
    )
    assert int(remainder["qty"]) == repository.get_position("TQQQ").quantity

    # 5) Recreate all services with the same DB and broker to simulate a process restart.
    restarted_repository, _, restarted_monitor = build_services(tmp_path, config, broker)
    assert ReconciliationService(config, restarted_repository, broker).run() == {}
    assert restarted_repository.get_position("TQQQ").state == PositionState.PARTIAL_TP_1
    assert [order["purpose"] for order in restarted_repository.open_orders("TQQQ")] == [
        "REMAINDER_EXIT"
    ]

    # 6) Fill the remainder exit and verify the cycle returns cleanly to EMPTY.
    restarted_remainder = next(
        order
        for order in restarted_repository.open_orders("TQQQ")
        if order["purpose"] == "REMAINDER_EXIT"
    )
    fill_order_at_limit(broker, restarted_remainder)
    restarted_monitor.run_once(now=datetime(2026, 9, 9, 22, 0, tzinfo=UTC))

    final_position = restarted_repository.get_position("TQQQ")
    assert final_position.state == PositionState.EMPTY
    assert final_position.quantity == 0
    assert restarted_repository.active_tp_plan("TQQQ") is None
    assert restarted_repository.open_orders("TQQQ") == []
    assert ReconciliationService(config, restarted_repository, broker).run() == {}
