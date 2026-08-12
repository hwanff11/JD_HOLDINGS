from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.portfolio_service import PortfolioService
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.application.trading_service import TradingService
from jd_holdings.backtest.engine import BacktestResult
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.core.twin_core import monthly_trend_signal, target_quantity
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.settings import RuntimeSettings


class FrameSource:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    def daily(self, symbol, start, end, *, refresh=False):
        del start, end, refresh
        return self.frames[symbol]


def monthly_frame(values: list[float]) -> pd.DataFrame:
    index = pd.date_range("2025-08-31", periods=len(values), freq="ME")
    index = index[:-1].append(pd.DatetimeIndex([pd.Timestamp("2026-07-31")]))
    return pd.DataFrame({"close": values}, index=index)


def settings(tmp_path, mode: str = "dry_run") -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode=mode,
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(),
        database_path=tmp_path / "jdss.db",
        log_path=tmp_path / "jdss.log",
    )


def test_monthly_trend_uses_completed_month_and_strict_cross():
    frame = monthly_frame([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111])
    signal = monthly_trend_signal("TQQQ", "QQQ", frame, months=6)

    assert signal.trade_date.isoformat() == "2026-07-31"
    assert signal.active is True
    assert signal.close == Decimal("111")
    assert signal.moving_average == Decimal("108.5")


def test_target_quantity_accounts_for_buy_fee():
    assert target_quantity(
        Decimal("50000"), Decimal("0.10"), Decimal("100"), Decimal("0.001")
    ) == 49


def test_month_end_run_recovers_after_restart_and_creates_only_active_buy(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    broker = DryRunBroker(
        {"TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    source = FrameSource(
        {
            "QQQ": monthly_frame(list(range(100, 112))),
            "SOXX": monthly_frame(list(range(112, 100, -1))),
        }
    )
    service = PortfolioService(
        config,
        repository,
        broker,
        OrderManager(repository, broker, settings(tmp_path)),
        source,
        MarketClock(),
        trading_mode="dry_run",
    )

    result = service.run_month_end(datetime(2026, 8, 3, 22, tzinfo=UTC))
    assert result is not None
    assert result.trade_date == "2026-07-31"
    assert len(result.signals) == 1
    assert repository.get_core_position("TQQQ")["trend_active"] == 1
    assert repository.get_core_position("TQQQ")["target_weight"] == "0.1"
    assert repository.get_core_position("SOXL")["trend_active"] == 0
    assert service.run_month_end(datetime(2026, 8, 4, 22, tzinfo=UTC)) is None


def test_v3_core_refuses_live_mode(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    broker = DryRunBroker(buying_power=Decimal("50000"))
    service = PortfolioService(
        config,
        repository,
        broker,
        OrderManager(repository, broker, settings(tmp_path, "live")),
        FrameSource({}),
        MarketClock(),
        trading_mode="live",
    )

    with pytest.raises(RuntimeError, match="live 모드가 잠겨"):
        service.run_month_end(datetime(2026, 8, 3, 22, tzinfo=UTC))


def test_core_buy_keeps_two_step_approval_and_separate_ledger(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    broker = DryRunBroker(
        {"TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    runtime = settings(tmp_path)
    order_manager = OrderManager(repository, broker, runtime)
    market_clock = MarketClock()
    service = PortfolioService(
        config,
        repository,
        broker,
        order_manager,
        FrameSource(
            {
                "QQQ": monthly_frame(list(range(100, 112))),
                "SOXX": monthly_frame(list(range(112, 100, -1))),
            }
        ),
        market_clock,
        trading_mode="dry_run",
    )
    run = service.run_month_end(datetime(2026, 8, 3, 22, tzinfo=UTC))
    signal_id = run.signals[0]
    position_manager = PositionManager(config, repository, broker)
    trading = TradingService(
        config,
        repository,
        broker,
        order_manager,
        position_manager,
        TakeProfitManager(repository, broker, order_manager),
        market_clock,
    )

    approval_time = datetime(2026, 8, 4, 12, tzinfo=UTC)
    review_id, review_token = trading.create_review_approval(signal_id, now=approval_time)
    quote = trading.consume_review(review_id, review_token, now=approval_time)
    assert quote.quantity == 49
    receipt = trading.execute(
        quote.execution_approval_id, quote.execution_token, now=approval_time
    )

    assert receipt.status == "FILLED"
    assert repository.get_core_position("TQQQ")["qty"] == 49
    assert repository.get_position("TQQQ").quantity == 0


def test_portfolio_backtest_uses_fixed_principal_and_needs_no_sgov_frame(config):
    index = pd.bdate_range("2025-07-01", "2026-08-05")
    underlying = pd.Series(range(len(index)), index=index, dtype=float) + 100
    leveraged = pd.Series(100.0, index=index)
    frames = {
        "TQQQ": pd.DataFrame({"open": leveraged, "close": leveraged}),
        "SOXL": pd.DataFrame({"open": leveraged, "close": leveraged}),
        "QQQ": pd.DataFrame({"open": underlying, "close": underlying}),
        "SOXX": pd.DataFrame({"open": underlying, "close": underlying}),
    }
    booster_results = {
        symbol: BacktestResult(
            symbol=symbol,
            start_date=index[0].date(),
            end_date=index[-1].date(),
            strategy_version=config.version,
            config_version=config.config_version,
            slippage=0.001,
            metrics={},
            trades=(),
            signals=(),
            skipped_signals=(),
            closed_cycles=(),
            open_position={},
            equity_curve=pd.Series(1000.0, index=index),
        )
        for symbol in config.enabled_symbols
    }

    result = PortfolioBacktestEngine(config).run(
        frames,
        booster_results,
        start=index[0].date(),
        end=index[-1].date(),
        slippage=0.001,
    )

    core_buys = [
        trade
        for trade in result.trades
        if trade["component"] == "core" and trade["side"] == "BUY"
    ]
    assert core_buys
    first_buy = pd.Timestamp(core_buys[0]["date"])
    preceding_session = index[index.get_loc(first_buy) - 1]
    assert preceding_session in PortfolioBacktestEngine._month_end_sessions(index)
    assert result.metrics["component_fills"]["core"] == len(core_buys)
    assert result.metrics["component_fills"]["booster"] == 0
    assert result.metrics["capital_ceiling"] == 50000.0
    assert result.metrics["maximum_invested_cost"] <= 50000.0
    assert result.metrics["profit_reinvestment"] is False
    assert result.metrics["idle_cash_enabled"] is False
    assert result.metrics["idle_cash_income"] == 0.0
