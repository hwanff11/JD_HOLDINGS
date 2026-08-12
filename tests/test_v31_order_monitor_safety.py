from __future__ import annotations

from decimal import Decimal

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.order_monitor import OrderMonitor
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.settings import RuntimeSettings


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "monitor.db",
        log_path=tmp_path / "monitor.log",
    )


def test_order_monitor_keeps_missing_partial_restart_in_safe_mode(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "monitor.db", config)
    broker = DryRunBroker(
        {"TQQQ": Decimal("100"), "SOXL": Decimal("50"), "SGOV": Decimal("100")},
        buying_power=Decimal("20000"),
    )
    assert repository.reserve_order(
        client_order_id="PARTIAL-LOST-AFTER-RESTART",
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="SELL",
        order_type="LIMIT",
        price=Decimal("110"),
        quantity=2,
        purpose="TP2",
    )
    repository.update_order(
        "PARTIAL-LOST-AFTER-RESTART",
        status="PARTIAL_FILLED",
        broker_order_id="DRY-00000077",
        filled_qty=1,
        average_fill_price=Decimal("110"),
    )
    manager = OrderManager(repository, broker, _settings(tmp_path))
    monitor = OrderMonitor(
        config,
        repository,
        broker,
        manager,
        PositionManager(config, repository, broker),
        TakeProfitManager(repository, broker, manager),
    )

    events = monitor.run_once()

    assert events == ["TQQQ 열린 주문 확인 불가: SAFE_MODE"]
    assert repository.get_position("TQQQ").state.value == "SAFE_MODE"
    assert any(
        event["event_type"] == "BROKER_ORDER_MISSING"
        for event in repository.recent_events()
    )
