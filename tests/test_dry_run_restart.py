from __future__ import annotations

import json
from decimal import Decimal

from jd_holdings.application.broker import DryRunBroker, MarketDataDryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.bot import restore_dry_run_holdings, restore_dry_run_orders
from jd_holdings.core.enums import PositionState
from jd_holdings.core.models import OrderRequest


class MutablePriceSource:
    def __init__(self, price: Decimal) -> None:
        self.price = price

    def current_price(self, symbol: str):
        del symbol
        return None, float(self.price)


def test_market_data_dry_run_fills_pending_sell_when_price_crosses_limit():
    source = MutablePriceSource(Decimal("100"))
    broker = MarketDataDryRunBroker(source, buying_power=Decimal("1000"))
    broker.holdings["TQQQ"] = {
        "quantity": 10,
        "averagePurchasePrice": Decimal("90"),
    }
    request = OrderRequest(
        client_order_id="TP-CROSS",
        symbol="TQQQ",
        side="SELL",
        order_type="LIMIT",
        quantity=3,
        price=Decimal("110"),
    )

    receipt = broker.place_order(request)
    assert receipt.status == "PENDING"
    source.price = Decimal("111")

    order = broker.get_order(receipt.broker_order_id)
    assert order["status"] == "FILLED"
    assert order["execution"]["filledQuantity"] == "3"
    assert int(broker.holdings["TQQQ"]["quantity"]) == 7
    assert broker.buying_power == Decimal("1333")


def test_dry_run_can_cancel_partial_order():
    broker = DryRunBroker({"TQQQ": Decimal("100")})
    request = OrderRequest(
        client_order_id="PARTIAL-CANCEL",
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        quantity=10,
        price=Decimal("90"),
    )
    receipt = broker.place_order(request)
    broker.orders[receipt.broker_order_id]["status"] = "PARTIAL_FILLED"

    broker.cancel_order(receipt.broker_order_id)

    assert broker.orders[receipt.broker_order_id]["status"] == "CANCELED"


def test_cold_restart_restores_holdings_and_open_tp_order_for_reconciliation(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "cold-restart.db", config)
    cycle_id = "JDSS-TQQQ-COLD"
    tp_plan_id = repository.create_tp_plan(
        cycle_id=cycle_id,
        symbol="TQQQ",
        source_event="FIRST_ENTRY_CANDIDATE",
        average_price=Decimal("100"),
        atr_pct=Decimal("0.04"),
        tp1_price=Decimal("104"),
        tp1_target_qty=3,
        tp2_price=Decimal("110"),
        tp2_target_qty=7,
    )
    with repository.transaction() as connection:
        connection.execute(
            """
            UPDATE positions
            SET state = ?, cycle_id = ?, qty = 10, avg_price = '100',
                current_cost_basis = '1000', cycle_exposure_cap = '3200',
                staged_entry_capital = '1000', cash_remaining = '7000',
                entry_count = 1, anchor_price = '100', last_entry_price = '100',
                tp_plan_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE symbol = 'TQQQ'
            """,
            (PositionState.HOLDING_1ST.value, cycle_id, tp_plan_id),
        )
    assert repository.reserve_order(
        client_order_id="JDSS-TQQQ-TP1-r0",
        signal_id=None,
        cycle_id=cycle_id,
        symbol="TQQQ",
        side="SELL",
        order_type="LIMIT",
        price=Decimal("104"),
        quantity=3,
        purpose="TP1",
    )
    raw = {
        "orderId": "DRY-00000007",
        "clientOrderId": "JDSS-TQQQ-TP1-r0",
        "symbol": "TQQQ",
        "side": "SELL",
        "orderType": "LIMIT",
        "timeInForce": "DAY",
        "status": "PENDING",
        "price": "104",
        "quantity": "3",
        "execution": {
            "filledQuantity": "0",
            "averageFilledPrice": None,
            "filledAmount": None,
            "commission": "0",
            "tax": "0",
            "filledAt": None,
            "settlementDate": None,
        },
    }
    repository.update_order(
        "JDSS-TQQQ-TP1-r0",
        status="PENDING",
        broker_order_id="DRY-00000007",
        filled_qty=0,
        raw=raw,
    )
    assert repository.reserve_order(
        client_order_id="JDSS-TQQQ-TP2-r0",
        signal_id=None,
        cycle_id=cycle_id,
        symbol="TQQQ",
        side="SELL",
        order_type="LIMIT",
        price=Decimal("110"),
        quantity=7,
        purpose="TP2",
    )
    raw2 = dict(raw)
    raw2.update(
        {
            "orderId": "DRY-00000008",
            "clientOrderId": "JDSS-TQQQ-TP2-r0",
            "price": "110",
            "quantity": "7",
            "execution": dict(raw["execution"]),
        }
    )
    repository.update_order(
        "JDSS-TQQQ-TP2-r0",
        status="PENDING",
        broker_order_id="DRY-00000008",
        filled_qty=0,
        raw=raw2,
    )

    source = MutablePriceSource(Decimal("100"))
    broker = MarketDataDryRunBroker(source, buying_power=config.total_strategy_capital)
    restore_dry_run_holdings(repository, broker)
    restore_dry_run_orders(repository, broker)

    assert int(broker.holdings["TQQQ"]["quantity"]) == 10
    assert broker.sequence == 8
    restored_ids = {order["orderId"] for order in broker.list_orders(status="OPEN", symbol="TQQQ")}
    assert restored_ids == {"DRY-00000007", "DRY-00000008"}
    assert ReconciliationService(config, repository, broker).run() == {}


def test_cold_restart_does_not_convert_unknown_order_into_known_broker_order(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "unknown-restart.db", config)
    assert repository.reserve_order(
        client_order_id="UNKNOWN-ORDER",
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=1,
        purpose="FIRST_ENTRY_CANDIDATE",
    )
    repository.update_order(
        "UNKNOWN-ORDER",
        status="UNKNOWN",
        broker_order_id="DRY-00000003",
        raw=json.loads("{}"),
    )
    broker = MarketDataDryRunBroker(MutablePriceSource(Decimal("100")))

    restore_dry_run_orders(repository, broker)

    assert broker.orders == {}
