from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd

import jd_holdings.backtest.runner as runner_module
from jd_holdings.backtest.runner import run_production_backtest


class _DataSource:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def daily(self, symbol, start, end=None, *, refresh=False):
        self.calls.append((symbol, refresh))
        index = pd.date_range("2010-01-04", "2025-01-10", freq="B")
        return pd.DataFrame(
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1_000_000.0,
            },
            index=index,
        )


def test_production_runner_replays_overlay_from_strategy_start_and_warns_on_stale_end(
    config, monkeypatch
):
    strategy_calls: list[tuple[str, date, date]] = []
    portfolio_virtual_starts: dict[str, date] = {}

    class FakeStrategyEngine:
        def __init__(self, _config) -> None:
            pass

        def run(self, symbol, _target, _spy, _qqq, *, start, end, **_kwargs):
            start_date = pd.Timestamp(start).date()
            end_date = pd.Timestamp(end).date()
            strategy_calls.append((symbol, start_date, end_date))
            return SimpleNamespace(
                symbol=symbol,
                requested_start=start_date,
                start_date=start_date,
                end_date=date(2025, 1, 8),
            )

    class FakePortfolioEngine:
        def __init__(self, _config) -> None:
            pass

        def run(self, _frames, virtual_results, *, start, end, slippage):
            del start, end, slippage
            portfolio_virtual_starts.update(
                {
                    symbol: result.requested_start
                    for symbol, result in virtual_results.items()
                }
            )
            return SimpleNamespace(end_date=date(2025, 1, 8))

    monkeypatch.setattr(runner_module, "StrategyBacktestEngine", FakeStrategyEngine)
    monkeypatch.setattr(runner_module, "PortfolioBacktestEngine", FakePortfolioEngine)
    data_source = _DataSource()

    run = run_production_backtest(
        config,
        data_source,
        symbols=config.enabled_symbols,
        start=date(2025, 1, 2),
        end=date(2025, 1, 10),
    )

    strategy_start = date.fromisoformat(config.backtest.default_start)
    assert [item[1] for item in strategy_calls[:2]] == [date(2025, 1, 2)] * 2
    assert portfolio_virtual_starts == {
        symbol: strategy_start for symbol in config.enabled_symbols
    }
    assert run.warnings[-1] == (
        "요청 종료일 2025-01-10의 데이터가 아직 없어 "
        "최신 확보일 2025-01-08까지만 계산했습니다"
    )
    assert all(refresh is False for _, refresh in data_source.calls)


def test_production_runner_forwards_explicit_refresh_without_fallback_semantics(
    config, monkeypatch
):
    class FakeStrategyEngine:
        def __init__(self, _config) -> None:
            pass

        def run(self, symbol, _target, _spy, _qqq, *, start, end, **_kwargs):
            return SimpleNamespace(
                symbol=symbol,
                requested_start=pd.Timestamp(start).date(),
                start_date=pd.Timestamp(start).date(),
                end_date=pd.Timestamp(end).date(),
            )

    class FakePortfolioEngine:
        def __init__(self, _config) -> None:
            pass

        def run(self, _frames, _virtual_results, *, start, end, slippage):
            del start, slippage
            return SimpleNamespace(end_date=pd.Timestamp(end).date())

    monkeypatch.setattr(runner_module, "StrategyBacktestEngine", FakeStrategyEngine)
    monkeypatch.setattr(runner_module, "PortfolioBacktestEngine", FakePortfolioEngine)
    data_source = _DataSource()

    run_production_backtest(
        config,
        data_source,
        symbols=config.enabled_symbols,
        start=date(2025, 1, 2),
        end=date(2025, 1, 10),
        refresh=True,
    )

    assert data_source.calls
    assert all(refresh is True for _, refresh in data_source.calls)
