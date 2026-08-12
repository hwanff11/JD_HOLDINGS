from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from conftest import make_score, make_snapshot

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.application.trading_service import TradingService
from jd_holdings.core.enums import PositionState
from jd_holdings.core.execution import max_chase_price
from jd_holdings.core.strategy import evaluate_entry
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.settings import RuntimeSettings


def _services(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "v31-accounting.db", config)
    settings = RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "v31-accounting.db",
        log_path=tmp_path / "v31-accounting.log",
    )
    broker = DryRunBroker({"TQQQ": Decimal("100")}, buying_power=Decimal("20000"))
    order_manager = OrderManager(repository, broker, settings)
    position_manager = PositionManager(config, repository, broker)
    tp_manager = TakeProfitManager(repository, broker, order_manager)
    trading = TradingService(
        config,
        repository,
        broker,
        order_manager,
        position_manager,
        tp_manager,
        MarketClock(),
    )
    return repository, broker, order_manager, position_manager, tp_manager, trading


def _approved_entry(repository, trading, config):
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
        code_version="v31-accounting-test",
        cycle_id=None,
    )
    assert created
    review_id, review_token = trading.create_review_approval(signal_id)
    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    quote = trading.consume_review(review_id, review_token, now=now)
    receipt = trading.execute(quote.execution_approval_id, quote.execution_token, now=now)
    assert receipt.status == "FILLED"
    return quote


def test_booster_ledger_stays_separate_when_core_shares_exist(tmp_path, config):
    repository, broker, order_manager, position_manager, _, trading = _services(tmp_path, config)
    with repository.transaction() as connection:
        connection.execute(
            """
            UPDATE core_positions
            SET qty = 10, avg_price = '90', cost_basis = '900',
                trend_active = 1, target_weight = '0.10'
            WHERE symbol = 'TQQQ'
            """
        )
    broker.holdings["TQQQ"] = {
        "quantity": 10,
        "averagePurchasePrice": Decimal("90"),
    }

    quote = _approved_entry(repository, trading, config)
    position = repository.get_position("TQQQ")
    assert position.quantity == quote.quantity
    assert position.average_price == Decimal("100")
    assert int(repository.get_core_position("TQQQ")["qty"]) == 10
    assert int(broker.holdings["TQQQ"]["quantity"]) == 10 + quote.quantity

    tp1 = next(order for order in repository.open_orders("TQQQ") if order["purpose"] == "TP1")
    broker_order = broker.orders[tp1["broker_order_id"]]
    broker_order["status"] = "FILLED"
    broker_order["execution"]["filledQuantity"] = str(tp1["qty"])
    broker_order["execution"]["averageFilledPrice"] = str(tp1["price"])
    broker_order["execution"]["filledAmount"] = str(
        Decimal(str(tp1["price"])) * Decimal(str(tp1["qty"]))
    )
    broker._apply_fill(broker_order)
    order_manager.refresh_order(tp1["client_order_id"])
    position_manager.apply_sell_fill(tp1["client_order_id"])

    position = repository.get_position("TQQQ")
    assert position.quantity == quote.quantity - int(tp1["qty"])
    assert position.state == PositionState.PARTIAL_TP_1
    assert int(repository.get_core_position("TQQQ")["qty"]) == 10
    assert int(broker.holdings["TQQQ"]["quantity"]) == 10 + position.quantity
    assert ReconciliationService(config, repository, broker).run() == {}


def test_replaced_tp1_preserves_previous_partial_fill_count(tmp_path, config):
    repository, broker, order_manager, position_manager, tp_manager, trading = _services(
        tmp_path, config
    )
    _approved_entry(repository, trading, config)
    plan = repository.active_tp_plan("TQQQ")
    assert plan is not None
    target = int(plan["tp1_target_qty"])
    assert target > 3

    original = next(
        order for order in repository.open_orders("TQQQ") if order["purpose"] == "TP1"
    )
    broker_order = broker.orders[original["broker_order_id"]]
    broker_order["status"] = "PARTIAL_FILLED"
    broker_order["execution"]["filledQuantity"] = "3"
    broker_order["execution"]["averageFilledPrice"] = str(original["price"])
    order_manager.refresh_order(original["client_order_id"])
    position_manager.apply_sell_fill(original["client_order_id"])
    assert int(repository.active_tp_plan("TQQQ")["tp1_filled_qty"]) == 3

    broker_order["status"] = "CANCELED"
    order_manager.refresh_order(original["client_order_id"])
    position_manager.apply_sell_fill(original["client_order_id"])

    settled = tp_manager.cancel_open_tp_orders("TQQQ")
    for client_order_id in settled:
        position_manager.apply_sell_fill(client_order_id)
    tp_manager.place_orders("TQQQ")
    recovered = next(
        order for order in repository.open_orders("TQQQ") if order["purpose"] == "TP1"
    )
    assert int(recovered["qty"]) == target - 3

    recovered_broker = broker.orders[recovered["broker_order_id"]]
    recovered_broker["status"] = "FILLED"
    recovered_broker["execution"]["filledQuantity"] = str(recovered["qty"])
    recovered_broker["execution"]["averageFilledPrice"] = str(recovered["price"])
    order_manager.refresh_order(recovered["client_order_id"])
    position_manager.apply_sell_fill(recovered["client_order_id"])

    plan = repository.active_tp_plan("TQQQ")
    position = repository.get_position("TQQQ")
    assert int(plan["tp1_filled_qty"]) == target
    assert position.tp1_filled_qty == target
    assert position.state == PositionState.PARTIAL_TP_1
