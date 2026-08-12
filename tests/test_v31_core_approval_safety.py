from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.portfolio_service import PortfolioService
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.application.trading_service import TradingService
from jd_holdings.bot import restore_dry_run_orders
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.settings import RuntimeSettings


class FrameSource:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    def daily(self, symbol, start, end, *, refresh=False):
        del start, end, refresh
        return self.frames[symbol]


def _monthly_frame(values: list[float]) -> pd.DataFrame:
    index = pd.date_range("2025-08-31", periods=len(values), freq="ME")
    index = index[:-1].append(pd.DatetimeIndex([pd.Timestamp("2026-07-31")]))
    return pd.DataFrame({"close": values}, index=index)


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(),
        database_path=tmp_path / "jdss.db",
        log_path=tmp_path / "jdss.log",
    )


def _build_core_signal(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    broker = DryRunBroker(
        {"TQQQ": Decimal("100"), "SOXL": Decimal("50"), "SGOV": Decimal("100")},
        buying_power=Decimal("20000"),
    )
    order_manager = OrderManager(repository, broker, _settings(tmp_path))
    market_clock = MarketClock()
    portfolio = PortfolioService(
        config,
        repository,
        broker,
        order_manager,
        FrameSource(
            {
                "QQQ": _monthly_frame(list(range(100, 112))),
                "SOXX": _monthly_frame(list(range(112, 100, -1))),
            }
        ),
        market_clock,
        trading_mode="dry_run",
    )
    run = portfolio.run_month_end(datetime(2026, 8, 3, 22, tzinfo=UTC))
    assert run is not None and len(run.signals) == 1
    trading = TradingService(
        config,
        repository,
        broker,
        order_manager,
        PositionManager(config, repository, broker),
        TakeProfitManager(repository, broker, order_manager),
        market_clock,
    )
    return repository, broker, trading, run.signals[0]


def test_core_quote_never_exceeds_signal_time_planned_quantity(tmp_path, config):
    repository, broker, trading, signal_id = _build_core_signal(tmp_path, config)
    del repository
    broker.set_price("TQQQ", Decimal("90"))
    approval_time = datetime(2026, 8, 4, 12, tzinfo=UTC)

    review_id, review_token = trading.create_review_approval(signal_id, now=approval_time)
    quote = trading.consume_review(review_id, review_token, now=approval_time)

    assert quote.quantity == 19


def test_core_quote_reduces_quantity_when_core_was_filled_after_signal(tmp_path, config):
    repository, broker, trading, signal_id = _build_core_signal(tmp_path, config)
    client_id = "CORE-CONCURRENT-FILL"
    assert repository.reserve_order(
        client_order_id=client_id,
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=10,
        purpose="CORE_REBALANCE_BUY",
    )
    repository.update_order(
        client_id,
        status="FILLED",
        broker_order_id="DRY-CORE-CONCURRENT",
        filled_qty=10,
        average_fill_price=Decimal("100"),
    )
    repository.apply_core_fill(client_id)
    broker.holdings["TQQQ"] = {
        "quantity": 10,
        "averagePurchasePrice": Decimal("100"),
    }
    broker.buying_power = Decimal("19000")
    approval_time = datetime(2026, 8, 4, 12, tzinfo=UTC)

    review_id, review_token = trading.create_review_approval(signal_id, now=approval_time)
    quote = trading.consume_review(review_id, review_token, now=approval_time)

    assert quote.quantity == 9


def test_restart_sequence_uses_completed_historical_dry_orders(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    assert repository.reserve_order(
        client_order_id="COMPLETED-HISTORY",
        signal_id=None,
        cycle_id=None,
        symbol="TQQQ",
        side="BUY",
        order_type="LIMIT",
        price=Decimal("100"),
        quantity=1,
        purpose="ENTRY_1",
    )
    repository.update_order(
        "COMPLETED-HISTORY",
        status="FILLED",
        broker_order_id="DRY-00000042",
        filled_qty=1,
        average_fill_price=Decimal("100"),
    )
    broker = DryRunBroker({"TQQQ": Decimal("100")}, buying_power=Decimal("19900"))

    restore_dry_run_orders(repository, broker)

    assert broker.sequence == 42
