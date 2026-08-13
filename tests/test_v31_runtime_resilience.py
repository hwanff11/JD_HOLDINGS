from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.portfolio_service import PortfolioService
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.application.trading_service_final import FinalTradingService
from jd_holdings.core.enums import PositionState
from jd_holdings.core.models import OrderReceipt
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.telegram_bot_operational import OperationalTelegramBotApp
from jd_holdings.settings import RuntimeSettings

APPROVAL_TIME = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


class UnknownBroker(DryRunBroker):
    def place_order(self, request):
        del request
        raise TimeoutError("broker response lost")


class RejectedBroker(DryRunBroker):
    def place_order(self, request):
        self.sequence += 1
        return OrderReceipt(
            client_order_id=request.client_order_id,
            broker_order_id=f"REJECT-{self.sequence}",
            status="REJECTED",
            quantity=request.quantity,
            filled_quantity=0,
            average_fill_price=None,
            raw={"status": "REJECTED"},
        )


class DisjointFrameSource:
    def daily(self, symbol, start, end, *, refresh=False):
        del start, end, refresh
        if symbol == "QQQ":
            return pd.DataFrame(
                {"close": [100, 101]},
                index=pd.to_datetime(["2026-06-30", "2026-07-31"]),
            )
        return pd.DataFrame(
            {"close": [90, 91]},
            index=pd.to_datetime(["2026-06-29", "2026-07-30"]),
        )


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(123,),
        database_path=tmp_path / "runtime.db",
        log_path=tmp_path / "runtime.log",
    )


def _final_trading(tmp_path, config, broker):
    repository = SQLiteRepository(tmp_path / "runtime.db", config)
    order_manager = OrderManager(repository, broker, _settings(tmp_path))
    position_manager = PositionManager(config, repository, broker)
    tp_manager = TakeProfitManager(repository, broker, order_manager)
    trading = FinalTradingService(
        config,
        repository,
        broker,
        order_manager,
        position_manager,
        tp_manager,
        MarketClock(),
    )
    return repository, order_manager, trading


def _create_core_signal(repository, config) -> int:
    repository.set_core_target(
        "TQQQ",
        active=True,
        target_weight=config.portfolio.core_initial_weight,
        signal_trade_date=date(2026, 7, 31),
    )
    signal_id, created = repository.create_core_buy_signal(
        symbol="TQQQ",
        trade_date=date(2026, 7, 31),
        signal_close=Decimal("100"),
        planned_budget=Decimal("2000"),
        valid_until=APPROVAL_TIME + timedelta(hours=2),
        code_version="runtime-resilience",
    )
    assert created
    return signal_id


def test_unknown_core_buy_enters_safe_mode_and_is_not_reopened(tmp_path, config):
    broker = UnknownBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    repository, _order_manager, trading = _final_trading(tmp_path, config, broker)
    signal_id = _create_core_signal(repository, config)
    review_id, review_token = trading.create_review_approval(signal_id, now=APPROVAL_TIME)
    quote = trading.consume_review(review_id, review_token, now=APPROVAL_TIME)

    with pytest.raises(TimeoutError, match="broker response lost"):
        trading.execute(
            quote.execution_approval_id,
            quote.execution_token,
            now=APPROVAL_TIME,
        )

    signal = repository.get_signal(signal_id)
    assert signal["status"] == "UNKNOWN"
    assert signal["processed"] == 1
    assert repository.get_position("TQQQ").state == PositionState.SAFE_MODE
    assert any(
        event["event_type"] == "CORE_ORDER_SUBMISSION_UNKNOWN"
        for event in repository.recent_events(20)
    )


def test_immediate_core_sell_rejection_enters_safe_mode(tmp_path, config):
    broker = RejectedBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    repository = SQLiteRepository(tmp_path / "runtime.db", config)
    order_manager = OrderManager(repository, broker, _settings(tmp_path))
    service = PortfolioService(
        config,
        repository,
        broker,
        order_manager,
        DisjointFrameSource(),
        MarketClock(),
        trading_mode="dry_run",
    )

    with pytest.raises(RuntimeError, match="SAFE_MODE"):
        service._sell_core("TQQQ", 2, Decimal("100"), "2026-07-31")

    assert repository.get_position("TQQQ").state == PositionState.SAFE_MODE
    assert any(
        event["event_type"] == "CORE_SELL_INCOMPLETE"
        for event in repository.recent_events(20)
    )


def test_v322_missing_ohlcv_is_an_explicit_error(tmp_path, config):
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    repository = SQLiteRepository(tmp_path / "runtime.db", config)
    service = PortfolioService(
        config,
        repository,
        broker,
        OrderManager(repository, broker, _settings(tmp_path)),
        DisjointFrameSource(),
        MarketClock(),
        trading_mode="dry_run",
    )

    with pytest.raises(ValueError, match="OHLCV 필수 컬럼 누락"):
        service.run_allocation(datetime(2026, 8, 3, 22, 0, tzinfo=UTC))

    assert repository.get_system_value("last_v322_allocation_trade_date") is None


class _OneCycleStop:
    def __init__(self) -> None:
        self.calls = 0

    def wait(self, _seconds) -> bool:
        self.calls += 1
        return self.calls > 1


class _Clock:
    def latest_completed_session(self, *, delay_minutes):
        del delay_minutes
        return date(2026, 8, 11)


class _FailingPortfolio:
    def run_month_end(self):
        raise RuntimeError("monthly data unavailable")


class _Analysis:
    def __init__(self) -> None:
        self.calls = 0

    def analyze_all(self):
        self.calls += 1
        return []


class _Monitor:
    def __init__(self) -> None:
        self.calls = 0

    def run_once(self):
        self.calls += 1
        return []


class _Reconciliation:
    def __init__(self) -> None:
        self.calls = 0

    def run(self):
        self.calls += 1
        return {}


def test_portfolio_scheduler_failure_does_not_starve_monitoring(
    tmp_path, config, monkeypatch
):
    repository = SQLiteRepository(tmp_path / "runtime.db", config)
    analysis = _Analysis()
    monitor = _Monitor()
    reconciliation = _Reconciliation()
    errors: list[str] = []

    monkeypatch.setattr(
        "jd_holdings.infrastructure.telegram_bot_operational._is_toss_order_maintenance_window",
        lambda _now: False,
    )
    app = object.__new__(OperationalTelegramBotApp)
    app.config = config
    app.repository = repository
    app.market_clock = _Clock()
    app.portfolio_service = _FailingPortfolio()
    app.analysis_service = analysis
    app.order_monitor = monitor
    app.reconciliation_service = reconciliation
    app.idle_cash_manager = None
    app._stop = _OneCycleStop()
    app._last_monitor = -1_000_000_000.0
    app._last_idle_cash_sweep = -1_000_000_000.0
    app._reconciliation_notice_at = {}
    app._send = lambda *args, **kwargs: None
    app.notify_new_signals = lambda results: None
    app._notify_runtime_error = lambda event_type, title, exc: errors.append(event_type)

    app._scheduler_loop()

    assert "PORTFOLIO_SCHEDULER_ERROR" in errors
    assert analysis.calls == 1
    assert monitor.calls == 1
    assert reconciliation.calls == 1
