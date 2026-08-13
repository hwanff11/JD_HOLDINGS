from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from jd_holdings import __version__
from jd_holdings.application.allocation_trading_service import AllocationTradingService
from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.core.enums import PositionState
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.settings import RuntimeSettings


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "final-dry-run.db",
        log_path=tmp_path / "final-dry-run.log",
    )


def test_v322_allocation_buy_completes_two_step_dry_run_flow(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "final-dry-run.db", config)
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    manager = OrderManager(repository, broker, _settings(tmp_path))
    clock = MarketClock()
    repository.set_core_target(
        "QQQ",
        active=True,
        target_weight=Decimal("0.50"),
        signal_trade_date=date(2026, 8, 3),
    )
    signal_id, created = repository.create_core_buy_signal(
        symbol="QQQ",
        trade_date=date(2026, 8, 3),
        signal_close=Decimal("500"),
        planned_budget=Decimal("25000"),
        valid_until=clock.next_session_close(date(2026, 8, 3)),
        code_version=__version__,
    )
    assert created
    trading = AllocationTradingService(
        config,
        repository,
        broker,
        manager,
        PositionManager(config, repository, broker),
        TakeProfitManager(repository, broker, manager),
        clock,
    )
    now = datetime(2026, 8, 4, 12, tzinfo=UTC)

    review_id, review_token = trading.create_review_approval(signal_id, now=now)
    quote = trading.consume_review(review_id, review_token, now=now)
    receipt = trading.execute(
        quote.execution_approval_id,
        quote.execution_token,
        now=now,
    )

    assert receipt.status == "FILLED"
    assert repository.get_core_position("QQQ")["qty"] == quote.quantity
    assert repository.get_signal(signal_id)["status"] == "PROCESSED"
    assert ReconciliationService(config, repository, broker).run() == {}


def test_v322_restart_rejects_legacy_direct_booster_state(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "final-dry-run.db", config)
    with repository.transaction() as connection:
        connection.execute(
            """
            UPDATE positions
            SET state = ?, qty = 10, avg_price = '100', current_cost_basis = '1000',
                updated_at = CURRENT_TIMESTAMP
            WHERE symbol = 'TQQQ'
            """,
            (PositionState.HOLDING_1ST.value,),
        )
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("49000"),
    )
    broker.holdings["TQQQ"] = {
        "quantity": 10,
        "averagePurchasePrice": Decimal("100"),
    }

    mismatches = ReconciliationService(config, repository, broker).run()

    assert any(
        item.startswith("V322_DIRECT_BOOSTER_STATE_PRESENT")
        for item in mismatches["TQQQ"]
    )
    assert repository.get_position("TQQQ").state == PositionState.SAFE_MODE
    assert repository.get_system_value("v322_portfolio_safe_mode") == "1"
