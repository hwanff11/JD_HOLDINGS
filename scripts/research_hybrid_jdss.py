"""Compare the production JDSS baseline with two trend-pullback hybrids.

This is a research-only runner. It monkey-patches the decision hook only while a
hybrid backtest is running and never changes production strategy configuration.
"""

from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import jd_holdings.backtest.engine as engine_module
import pandas as pd
from jd_holdings.backtest.engine import (
    BacktestEngine,
    BacktestResult,
    _Pending,
    _SimulationState,
)
from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import StrategyConfig, load_config
from jd_holdings.core.enums import DecisionType, MarketRegime, PositionState
from jd_holdings.core.indicators import calculate_indicators, snapshot_from_row
from jd_holdings.core.models import (
    IndicatorSnapshot,
    PositionSnapshot,
    ScoreResult,
    TradeDecision,
)
from jd_holdings.core.strategy import (
    calculate_stage_budget,
    evaluate_strategy,
    expected_holding_state,
)
from jd_holdings.core.take_profit import calculate_take_profit
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ("TQQQ", "SOXL")
BENCHMARKS = ("SPY", "QQQ", "SOXX", "SMH")
SEGMENTS = {
    "development_2011_2018": ("2011-01-01", "2018-12-31"),
    "validation_2019_2022": ("2019-01-01", "2022-12-31"),
    "test_2023_present": ("2023-01-01", None),
    "full_history": ("2011-01-01", None),
}


def _snapshot_map(
    symbol: str, frame: pd.DataFrame
) -> dict[pd.Timestamp, IndicatorSnapshot]:
    return {
        timestamp: snapshot_from_row(symbol, timestamp, row)
        for timestamp, row in frame.iterrows()
    }


def _strong_benchmark(frame: pd.DataFrame) -> pd.Series:
    return (frame["close"] > frame["ema60"]) & (frame["ema20"] > frame["ema60"])


def _trend_maps(
    frames: dict[str, pd.DataFrame],
) -> tuple[dict[str, set[date]], dict[str, set[date]]]:
    qqq_strong = _strong_benchmark(frames["QQQ"])
    semiconductor_strong = _strong_benchmark(frames["SOXX"]) & _strong_benchmark(
        frames["SMH"]
    )
    entry_dates: dict[str, set[date]] = {}
    structural_dates: dict[str, set[date]] = {}

    for symbol in SYMBOLS:
        frame = frames[symbol]
        benchmark_strong = qqq_strong if symbol == "TQQQ" else semiconductor_strong
        structural = (
            benchmark_strong.reindex(frame.index).fillna(False)
            & (frame["ema20"] > frame["ema60"])
            & (frame["close"].pct_change(20) > 0)
        )
        rolling_high = frame["close"].rolling(10, min_periods=10).max()
        pullback = frame["close"] <= rolling_high * 0.98
        reversal = (frame["close"] > frame["previous_close"]) | (
            frame["close"] > frame["open"]
        )
        entry = (
            structural
            & (frame["close"] > frame["ema20"])
            & frame["rsi5"].between(35, 50, inclusive="both")
            & pullback
            & reversal
        )
        structural_dates[symbol] = {
            timestamp.date() for timestamp in frame.index[structural]
        }
        entry_dates[symbol] = {timestamp.date() for timestamp in frame.index[entry]}
    return entry_dates, structural_dates


class HybridBacktestEngine(BacktestEngine):
    """Production engine with a research-only trend-pullback decision overlay."""

    def __init__(
        self,
        config: StrategyConfig,
        *,
        entry_dates: dict[str, set[date]],
        structural_dates: dict[str, set[date]],
        trend_tp: tuple[Decimal, Decimal],
    ) -> None:
        super().__init__(config)
        self.entry_dates = entry_dates
        self.structural_dates = structural_dates
        self.trend_cycle_ids: set[str] = set()
        self.trend_tp_config = replace(
            config,
            take_profit=replace(
                config.take_profit,
                tp1_base=trend_tp[0],
                tp2_base=trend_tp[1],
            ),
        )

    @contextmanager
    def _decision_overlay(self):
        original = engine_module.evaluate_strategy
        engine_module.evaluate_strategy = self._evaluate_hybrid
        try:
            yield
        finally:
            engine_module.evaluate_strategy = original

    def run(self, *args: Any, **kwargs: Any) -> BacktestResult:
        self.trend_cycle_ids.clear()
        with self._decision_overlay():
            return super().run(*args, **kwargs)

    def _evaluate_hybrid(
        self,
        snapshot: IndicatorSnapshot,
        score: ScoreResult,
        position: PositionSnapshot,
        config: StrategyConfig,
        *,
        data_ok: bool = True,
        system_ok: bool = True,
        sector_benchmarks: dict[str, IndicatorSnapshot] | None = None,
    ) -> TradeDecision:
        baseline = evaluate_strategy(
            snapshot,
            score,
            position,
            config,
            data_ok=data_ok,
            system_ok=system_ok,
            sector_benchmarks=sector_benchmarks,
        )
        if baseline.allowed or score.regime != MarketRegime.GREEN:
            return baseline
        if (
            not data_ok
            or not system_ok
            or score.reversal_score < config.global_.minimum_reversal_score
        ):
            return baseline

        if position.state == PositionState.EMPTY:
            if snapshot.trade_date not in self.entry_dates[snapshot.symbol]:
                return baseline
            cap = config.global_.capital_per_symbol
            target, budget = calculate_stage_budget(cap, 1, Decimal(0), config)
            return TradeDecision(
                action=DecisionType.FIRST_ENTRY_CANDIDATE,
                allowed=True,
                reason_codes=("RESEARCH_TREND_PULLBACK_ENTRY",),
                target_stage=1,
                cycle_exposure_cap=cap,
                target_cumulative_capital=target,
                planned_budget=budget,
            )

        if position.cycle_id not in self.trend_cycle_ids:
            return baseline
        if snapshot.trade_date not in self.structural_dates[snapshot.symbol]:
            return baseline

        stage_drops = {2: Decimal("0.02"), 3: Decimal("0.04"), 4: Decimal("0.06")}
        for stage in (2, 3, 4):
            if position.state != expected_holding_state(stage - 1):
                continue
            trigger = position.anchor_price * (Decimal(1) - stage_drops[stage])
            if position.anchor_price <= 0 or snapshot.close > trigger:
                return baseline
            target, budget = calculate_stage_budget(
                position.cycle_exposure_cap,
                stage,
                position.staged_entry_capital,
                config,
            )
            if budget <= 0:
                return baseline
            return TradeDecision(
                action=DecisionType.ADD_ENTRY_CANDIDATE,
                allowed=True,
                reason_codes=("RESEARCH_TREND_PULLBACK_ADD",),
                target_stage=stage,
                cycle_exposure_cap=position.cycle_exposure_cap,
                target_cumulative_capital=target,
                planned_budget=budget,
                stage_trigger_price=trigger.quantize(Decimal("0.0001")),
            )
        return baseline

    def _execute_pending(
        self,
        pending: _Pending,
        state: _SimulationState,
        timestamp: pd.Timestamp,
        next_open: Decimal,
        slippage: Decimal,
        trades: list[dict[str, Any]],
    ) -> tuple[bool, str | None]:
        filled, reason = super()._execute_pending(
            pending, state, timestamp, next_open, slippage, trades
        )
        if not filled or not state.cycle_id:
            return filled, reason
        if "RESEARCH_TREND_PULLBACK_ENTRY" in pending.decision.reason_codes:
            self.trend_cycle_ids.add(state.cycle_id)
        if state.cycle_id in self.trend_cycle_ids:
            state.tp_plan = calculate_take_profit(
                state.average_price,
                state.quantity,
                Decimal(str(pending.snapshot.atr_pct)),
                self.trend_tp_config,
            )
        return filled, reason


def _combined_metrics(
    results: dict[str, BacktestResult], annualization_days: int
) -> dict[str, Any]:
    equity = pd.concat(
        [result.equity_curve.rename(symbol) for symbol, result in results.items()],
        axis=1,
        join="inner",
    ).sum(axis=1)
    initial = float(equity.iloc[0])
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    sharpe, sortino = risk_adjusted_metrics(equity, annualization_days)
    return {
        "initial_equity": round(initial, 2),
        "final_equity": round(final, 2),
        "total_return_pct": round((final / initial - 1) * 100, 2),
        "cagr_pct": round(((final / initial) ** (1 / years) - 1) * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "closed_cycles": sum(
            int(result.metrics["closed_cycles"]) for result in results.values()
        ),
        "signals": sum(int(result.metrics["signals"]) for result in results.values()),
        "idle_cash_income": round(
            sum(
                float(result.metrics.get("idle_cash_income", 0))
                for result in results.values()
            ),
            2,
        ),
        "annual_returns_pct": {
            str(year): round((group.iloc[-1] / group.iloc[0] - 1) * 100, 2)
            for year, group in equity.groupby(equity.index.year)
        },
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# JDSS Hybrid 1차 연구 결과",
        "",
        f"- 생성시각: {report['generated_at']}",
        f"- 데이터 종료일: {report['end_date']}",
        f"- 슬리피지: {report['slippage'] * 100:.2f}%",
        "- 비용: 매수 0.1%, 매도 0.1%",
        "- 종목: TQQQ, SOXL (종목당 $10,000, SGOV 유휴자금 포함)",
        "",
        "| 후보 | 구간 | 누적수익률 | CAGR | MDD | Sharpe | 완료 사이클 | SGOV 기여 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for candidate, candidate_data in report["candidates"].items():
        for segment in ("full_history", "test_2023_present"):
            metrics = candidate_data["segments"][segment]["combined"]
            lines.append(
                f"| {candidate} | {segment} | {metrics['total_return_pct']:+.2f}% | "
                f"{metrics['cagr_pct']:+.2f}% | {metrics['mdd_pct']:.2f}% | "
                f"{metrics['sharpe']:.3f} | {metrics['closed_cycles']} | "
                f"${metrics['idle_cash_income']:,.2f} |"
            )
    lines.extend(
        [
            "",
            "## 후보 정의",
            "",
            "- A_BASELINE: 운영 중인 JDSS-2.2.2-SGOV 그대로",
            "- B_HYBRID_4_6: 기존 과매도 반등 + GREEN 추세 눌림목, 추세 사이클 TP 4%/6%",
            "- C_HYBRID_5_9: B와 동일하되 추세 사이클만 TP 5%/9%",
            "",
            "> 연구 전용 결과이며 운영 설정이나 주문 로직을 변경하지 않습니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-08-10")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports" / "hybrid_research.json"
    )
    parser.add_argument(
        "--markdown", type=Path, default=ROOT / "reports" / "hybrid_research.md"
    )
    args = parser.parse_args()

    config = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (
        datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)
    ).isoformat()
    raw = {
        symbol: source.daily(symbol, warmup, args.end)
        for symbol in (*SYMBOLS, *BENCHMARKS, config.idle_cash.symbol)
    }
    frames = {
        symbol: calculate_indicators(frame, config) for symbol, frame in raw.items()
    }
    snapshots = {
        symbol: _snapshot_map(symbol, frame) for symbol, frame in frames.items()
    }
    entry_dates, structural_dates = _trend_maps(frames)

    factories = {
        "A_BASELINE": lambda: BacktestEngine(config),
        "B_HYBRID_4_6": lambda: HybridBacktestEngine(
            config,
            entry_dates=entry_dates,
            structural_dates=structural_dates,
            trend_tp=(Decimal("0.04"), Decimal("0.06")),
        ),
        "C_HYBRID_5_9": lambda: HybridBacktestEngine(
            config,
            entry_dates=entry_dates,
            structural_dates=structural_dates,
            trend_tp=(Decimal("0.05"), Decimal("0.09")),
        ),
    }
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "end_date": args.end,
        "slippage": args.slippage,
        "strategy_version": config.version,
        "candidates": {},
    }

    for candidate, factory in factories.items():
        candidate_data: dict[str, Any] = {"segments": {}}
        for segment, (start, configured_end) in SEGMENTS.items():
            end = configured_end or args.end
            results = {
                symbol: factory().run(
                    symbol,
                    frames[symbol],
                    frames["SPY"],
                    frames["QQQ"],
                    start=start,
                    end=end,
                    slippage=args.slippage,
                    indicators_precomputed=True,
                    snapshots_precomputed=snapshots,
                    sector_data={"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
                    if symbol == "SOXL"
                    else None,
                    idle_cash_data=raw[config.idle_cash.symbol],
                )
                for symbol in SYMBOLS
            }
            candidate_data["segments"][segment] = {
                "combined": _combined_metrics(
                    results, config.backtest.annualization_days
                ),
                "symbols": {
                    symbol: result.metrics for symbol, result in results.items()
                },
                "open_positions": {
                    symbol: result.open_position for symbol, result in results.items()
                },
            }
        report["candidates"][candidate] = candidate_data

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.markdown.write_text(_markdown(report), encoding="utf-8")
    print(_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
