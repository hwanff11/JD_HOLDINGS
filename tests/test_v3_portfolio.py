from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.portfolio_service import PortfolioService
from jd_holdings.backtest.engine import BacktestResult
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.core.twin_core import target_quantity
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.settings import RuntimeSettings


def settings(tmp_path, mode: str = "dry_run") -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode=mode,
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(),
        database_path=tmp_path / "jdss.db",
        log_path=tmp_path / "jdss.log",
    )


class StubPortfolioService(PortfolioService):
    def __init__(self, *args, target: pd.DataFrame, marked_equity=Decimal("50000"), **kwargs):
        self._target = target
        self._marked = marked_equity
        super().__init__(*args, **kwargs)

    def _calculate_target(self, completed):
        del completed
        return {}, self._target

    def _completed_marked_equity(self, raw, timestamp):
        del raw, timestamp
        return self._marked


def test_target_quantity_accounts_for_buy_fee():
    assert target_quantity(
        Decimal("50000"), Decimal("0.10"), Decimal("100"), Decimal("0.001")
    ) == 49


def test_v322_allocation_run_is_idempotent_and_creates_approval_signals(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    runtime = settings(tmp_path)
    order_manager = OrderManager(repository, broker, runtime)
    market_clock = MarketClock()
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    completed = market_clock.latest_completed_session(
        now, delay_minutes=config.scheduler.signal_delay_minutes
    )
    timestamp = pd.Timestamp(completed)
    target = pd.DataFrame(
        [
            {
                "trade_date": completed.isoformat(),
                "leverage": 1.5,
                "semiconductor_active": True,
                "jdss_tqqq_active": False,
                "jdss_soxl_active": False,
                "QQQ": 0.75,
                "TQQQ": 0.125,
                "SOXL": 0.125,
            }
        ],
        index=[timestamp],
    )
    service = StubPortfolioService(
        config,
        repository,
        broker,
        order_manager,
        object(),
        market_clock,
        trading_mode="dry_run",
        target=target,
    )

    result = service.run_allocation(now)

    assert result is not None
    assert result.trade_date == completed.isoformat()
    assert len(result.signals) == 3
    assert repository.get_core_position("QQQ")["target_weight"] == "0.75"
    assert repository.get_core_position("TQQQ")["target_weight"] == "0.125"
    assert repository.get_core_position("SOXL")["target_weight"] == "0.125"
    assert repository.open_orders() == []
    assert service.run_allocation(now) is None


def test_v322_refuses_live_mode_before_any_data_or_order_access(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    broker = DryRunBroker(buying_power=Decimal("50000"))
    service = StubPortfolioService(
        config,
        repository,
        broker,
        OrderManager(repository, broker, settings(tmp_path, "live")),
        object(),
        MarketClock(),
        trading_mode="live",
        target=pd.DataFrame(),
    )

    with pytest.raises(RuntimeError, match="live 모드가 잠겨"):
        service.run_allocation(datetime(2026, 8, 1, 12, tzinfo=UTC))


def test_v322_portfolio_backtest_uses_hwm75_and_no_sgov_frame(config):
    index = pd.bdate_range("2010-01-04", "2026-08-05")
    base = pd.Series(range(len(index)), index=index, dtype=float) / 20 + 100
    frames = {
        "TQQQ": pd.DataFrame({"open": base * 1.2, "close": base * 1.2}),
        "SOXL": pd.DataFrame({"open": base * 0.8, "close": base * 0.8}),
        "QQQ": pd.DataFrame({"open": base, "close": base}),
        "SOXX": pd.DataFrame({"open": base * 1.01, "close": base * 1.01}),
    }
    booster_results = {
        symbol: BacktestResult(
            symbol=symbol,
            start_date=pd.Timestamp("2011-01-03").date(),
            end_date=index[-1].date(),
            strategy_version=config.version,
            config_version=config.config_version,
            slippage=0.001,
            metrics={},
            trades=(),
            signals=(),
            skipped_signals=(),
            closed_cycles=(),
            open_position={"quantity": 0},
            equity_curve=pd.Series(1000.0, index=index),
        )
        for symbol in config.enabled_symbols
    }

    result = PortfolioBacktestEngine(config).run(
        frames,
        booster_results,
        start="2011-01-03",
        end=index[-1].date(),
        slippage=0.001,
    )

    assert result.trades
    assert {trade["component"] for trade in result.trades} == {"allocation"}
    assert result.metrics["initial_risk_budget"] == 50000.0
    assert result.metrics["hwm_reinvestment_fraction"] == 0.75
    assert result.metrics["profit_reinvestment"] == "HWM75_CONTROLLED"
    assert result.metrics["maximum_sizing_base"] >= 50000.0
    assert result.metrics["idle_cash_enabled"] is False
    assert result.metrics["idle_cash_income"] == 0.0
