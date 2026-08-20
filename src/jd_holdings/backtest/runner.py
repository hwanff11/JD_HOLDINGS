from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

import pandas as pd

from jd_holdings.config import StrategyConfig
from jd_holdings.core.v322_allocation import V322Policy

from .engine import BacktestResult
from .portfolio_engine import PortfolioBacktestEngine, PortfolioBacktestResult
from .strategy_engine import StrategyBacktestEngine


class DailyDataSource(Protocol):
    def daily(
        self,
        symbol: str,
        start: str | date,
        end: str | date | None = None,
        *,
        refresh: bool = False,
    ) -> pd.DataFrame: ...


@dataclass(frozen=True)
class ProductionBacktestRun:
    """One canonical backtest result shared by CLI and operator interfaces."""

    results: dict[str, BacktestResult]
    portfolio: PortfolioBacktestResult | None
    warnings: tuple[str, ...]


def run_production_backtest(
    config: StrategyConfig,
    data_source: DailyDataSource,
    *,
    symbols: tuple[str, ...],
    start: str | date,
    end: str | date,
    slippage: float | None = None,
    refresh: bool = False,
) -> ProductionBacktestRun:
    """Run the canonical V3.2.2 path with full-history overlay state.

    A requested reporting window must not reset the virtual JDSS overlay.  The
    overlay is therefore replayed from ``backtest.default_start`` whenever the
    shared-account portfolio is requested.  This is the same state warmup used
    by production target calculation.
    """

    if not symbols:
        raise ValueError("백테스트 종목이 비어 있습니다")
    unknown = set(symbols) - set(config.enabled_symbols)
    if unknown:
        raise ValueError("지원하지 않는 JDSS 종목: " + ", ".join(sorted(unknown)))

    requested_start = pd.Timestamp(start).date()
    requested_end = pd.Timestamp(end).date()
    strategy_start = pd.Timestamp(config.backtest.default_start).date()
    if requested_start < strategy_start:
        raise ValueError(
            f"시작일은 {strategy_start.isoformat()} 이후여야 합니다"
        )
    if requested_start >= requested_end:
        raise ValueError("백테스트는 서로 다른 2개 이상의 거래일이 필요합니다")

    warmup_start = (strategy_start - timedelta(days=420)).isoformat()
    end_text = requested_end.isoformat()
    spy = data_source.daily("SPY", warmup_start, end_text, refresh=refresh)
    qqq = data_source.daily("QQQ", warmup_start, end_text, refresh=refresh)

    idle_cash_data = None
    if config.idle_cash.enabled:
        idle_cash_data = data_source.daily(
            config.idle_cash.symbol, warmup_start, end_text, refresh=refresh
        )

    warnings: list[str] = []
    guard = config.market_regime.get("soxl_sector_guard", {})
    sector_data: dict[str, pd.DataFrame] = {}
    if "SOXL" in config.enabled_symbols and guard.get("enabled", False):
        for benchmark in guard.get("benchmark_candidates", ("SOXX", "SMH")):
            name = str(benchmark).upper()
            try:
                sector_data[name] = data_source.daily(
                    name, warmup_start, end_text, refresh=refresh
                )
            except Exception as exc:
                warnings.append(
                    f"{name} 섹터 데이터 조회 실패({type(exc).__name__}); "
                    "사용 가능한 벤치마크로 계속합니다"
                )

    engine = StrategyBacktestEngine(config)
    target_frames: dict[str, pd.DataFrame] = {}

    def target_frame(symbol: str) -> pd.DataFrame:
        frame = target_frames.get(symbol)
        if frame is None:
            frame = data_source.daily(symbol, warmup_start, end_text, refresh=refresh)
            target_frames[symbol] = frame
        return frame

    results: dict[str, BacktestResult] = {}
    for symbol in symbols:
        results[symbol] = engine.run(
            symbol,
            target_frame(symbol),
            spy,
            qqq,
            start=requested_start,
            end=requested_end,
            slippage=slippage,
            sector_data=sector_data if symbol == "SOXL" else None,
            idle_cash_data=idle_cash_data,
        )

    portfolio: PortfolioBacktestResult | None = None
    if config.portfolio.enabled and symbols == config.enabled_symbols:
        virtual_results: dict[str, BacktestResult] = {}
        for symbol in config.enabled_symbols:
            if requested_start == strategy_start and symbol in results:
                virtual_results[symbol] = results[symbol]
                continue
            virtual_results[symbol] = engine.run(
                symbol,
                target_frame(symbol),
                spy,
                qqq,
                start=strategy_start,
                end=requested_end,
                slippage=slippage,
                sector_data=sector_data if symbol == "SOXL" else None,
                idle_cash_data=idle_cash_data,
            )

        raw_frames: dict[str, pd.DataFrame] = {
            **target_frames,
            "SPY": spy,
            "QQQ": qqq,
        }
        policy = V322Policy.from_config(config)
        if policy.rs_benchmark not in raw_frames:
            rs_frame = sector_data.get(policy.rs_benchmark)
            if rs_frame is None:
                rs_frame = data_source.daily(
                    policy.rs_benchmark,
                    warmup_start,
                    end_text,
                    refresh=refresh,
                )
            raw_frames[policy.rs_benchmark] = rs_frame
        portfolio = PortfolioBacktestEngine(config).run(
            raw_frames,
            virtual_results,
            start=requested_start,
            end=requested_end,
            slippage=slippage,
        )

    actual_end = portfolio.end_date if portfolio is not None else min(
        result.end_date for result in results.values()
    )
    if actual_end < requested_end:
        warnings.append(
            f"요청 종료일 {requested_end.isoformat()}의 데이터가 아직 없어 "
            f"최신 확보일 {actual_end.isoformat()}까지만 계산했습니다"
        )

    return ProductionBacktestRun(results, portfolio, tuple(warnings))


def serialize_backtest_run(
    run: ProductionBacktestRun,
    *,
    strategy_version: str,
    config_version: str,
    generated_at: str,
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "generated_at": generated_at,
        "strategy_version": strategy_version,
        "config_version": config_version,
        "results": {
            symbol: result.to_dict(include_equity=False)
            for symbol, result in run.results.items()
        },
        "warnings": list(run.warnings),
    }
    if run.portfolio is not None:
        serialized = run.portfolio.to_dict(include_equity=False)
        output["v322_portfolio"] = serialized
        # Keep the prior machine-readable key during the V3.2.2 transition.
        output["v3_portfolio"] = serialized
        output["portfolio_metrics"] = run.portfolio.metrics
        return output

    portfolio = pd.concat(
        [
            result.equity_curve.rename(symbol)
            for symbol, result in run.results.items()
        ],
        axis=1,
        join="inner",
    ).sum(axis=1)
    years = max((portfolio.index[-1] - portfolio.index[0]).days / 365.2425, 1 / 365.2425)
    output["portfolio_metrics"] = {
        "initial_equity": round(float(portfolio.iloc[0]), 2),
        "final_equity": round(float(portfolio.iloc[-1]), 2),
        "total_return_pct": round(float((portfolio.iloc[-1] / portfolio.iloc[0] - 1) * 100), 2),
        "cagr_pct": round(
            float(((portfolio.iloc[-1] / portfolio.iloc[0]) ** (1 / years) - 1) * 100),
            2,
        ),
        "mdd_pct": round(float((portfolio / portfolio.cummax() - 1).min() * 100), 2),
        "idle_cash_income": 0.0,
    }
    return output
