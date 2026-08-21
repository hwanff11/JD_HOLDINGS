from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd

from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS


@dataclass(frozen=True)
class EntryScenario:
    """Cumulative deployment fractions for one-time initial portfolio entry."""

    name: str
    fractions: tuple[float, ...]
    interval_sessions: int

    def __post_init__(self) -> None:
        if not self.fractions:
            raise ValueError("fractions must not be empty")
        if any(not 0 < value <= 1 for value in self.fractions):
            raise ValueError("fractions must be within (0, 1]")
        if any(right <= left for left, right in zip(self.fractions, self.fractions[1:])):
            raise ValueError("fractions must strictly increase")
        if abs(self.fractions[-1] - 1.0) > 1e-12:
            raise ValueError("final fraction must be 1.0")
        if len(self.fractions) > 1 and self.interval_sessions < 1:
            raise ValueError("multi-stage scenario requires interval_sessions >= 1")

    def fraction_for_offset(self, session_offset: int) -> float:
        if session_offset < 0:
            raise ValueError("session_offset must be >= 0")
        if len(self.fractions) == 1:
            return self.fractions[0]
        stage = min(session_offset // self.interval_sessions, len(self.fractions) - 1)
        return self.fractions[stage]


DEFAULT_SCENARIOS: tuple[EntryScenario, ...] = (
    EntryScenario("LUMP_100", (1.0,), 0),
    EntryScenario("AGGR_60_80_100_3D", (0.60, 0.80, 1.0), 3),
    EntryScenario("FAST_50_75_100_1D", (0.50, 0.75, 1.0), 1),
    EntryScenario("CURRENT_50_75_100_3D", (0.50, 0.75, 1.0), 3),
    EntryScenario("SLOW_50_75_100_5D", (0.50, 0.75, 1.0), 5),
    EntryScenario("BAL_40_70_100_3D", (0.40, 0.70, 1.0), 3),
    EntryScenario("EQUAL_33_67_100_3D", (0.33, 0.67, 1.0), 3),
    EntryScenario("FOUR_25_50_75_100_3D", (0.25, 0.50, 0.75, 1.0), 3),
)


def _weights_from_target(row: pd.Series) -> dict[str, float]:
    return {
        symbol: float(row[symbol])
        for symbol in ALLOCATION_SYMBOLS
        if float(row[symbol]) > 1e-12
    }


def _maximum_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def simulate_entry_window(
    frames: dict[str, pd.DataFrame],
    targets: pd.DataFrame,
    *,
    start_position: int,
    scenario: EntryScenario,
    horizons: tuple[int, ...] = (21, 63, 126),
    initial_capital: float = 50000.0,
    hwm_reinvestment_fraction: float = 0.75,
    buy_fee: float = 0.001,
    sell_fee: float = 0.001,
    slippage: float = 0.001,
) -> dict[str, Any]:
    """Simulate a fresh V3.2.2 account with only its initial deployment changed.

    The strategy target path, HWM75 sizing, fees and slippage remain unchanged.
    The deployment fraction is applied to the fully sized target quantity, which
    mirrors the production onboarding contract more closely than scaling weights.
    """

    if not horizons or min(horizons) < 1:
        raise ValueError("horizons must contain positive session counts")
    max_horizon = max(horizons)
    index = targets.index
    if start_position < 1 or start_position + max_horizon > len(index):
        raise ValueError("start_position does not have enough warmup/forward sessions")
    for symbol in ALLOCATION_SYMBOLS:
        if symbol not in frames:
            raise ValueError(f"missing frame: {symbol}")
        if not index.equals(index.intersection(frames[symbol].index)):
            raise ValueError(f"frame index is missing target sessions: {symbol}")

    cash = initial_capital
    high_water = initial_capital
    quantities = {symbol: 0 for symbol in ALLOCATION_SYMBOLS}
    prior_ts = index[start_position - 1]
    pending = _weights_from_target(targets.loc[prior_ts])
    equity_values: list[float] = []
    trade_count = 0

    for offset, timestamp in enumerate(
        index[start_position : start_position + max_horizon]
    ):
        opens = {
            symbol: float(frames[symbol].loc[timestamp, "open"])
            for symbol in ALLOCATION_SYMBOLS
        }
        closes = {
            symbol: float(frames[symbol].loc[timestamp, "close"])
            for symbol in ALLOCATION_SYMBOLS
        }
        open_equity = cash + sum(
            quantities[symbol] * opens[symbol] * (1 - sell_fee)
            for symbol in ALLOCATION_SYMBOLS
        )
        sizing_equity = initial_capital + hwm_reinvestment_fraction * max(
            0.0, high_water - initial_capital
        )
        sizing_equity = max(0.0, min(sizing_equity, open_equity))

        fraction = scenario.fraction_for_offset(offset)
        desired: dict[str, int] = {}
        for symbol in ALLOCATION_SYMBOLS:
            buy_price = opens[symbol] * (1 + slippage)
            full_target = math.floor(
                sizing_equity
                * pending.get(symbol, 0.0)
                / (buy_price * (1 + buy_fee))
            )
            desired[symbol] = math.floor(full_target * fraction)

        for symbol in ALLOCATION_SYMBOLS:
            difference = desired[symbol] - quantities[symbol]
            if difference >= 0:
                continue
            quantity = -difference
            price = opens[symbol] * (1 - slippage)
            fee = quantity * price * sell_fee
            cash += quantity * price - fee
            quantities[symbol] -= quantity
            trade_count += 1

        for symbol in ALLOCATION_SYMBOLS:
            difference = desired[symbol] - quantities[symbol]
            if difference <= 0:
                continue
            price = opens[symbol] * (1 + slippage)
            affordable = math.floor(cash / (price * (1 + buy_fee)))
            quantity = min(difference, affordable)
            if quantity <= 0:
                continue
            fee = quantity * price * buy_fee
            cash -= quantity * price + fee
            quantities[symbol] += quantity
            trade_count += 1

        liquidation = sum(
            quantities[symbol] * closes[symbol] * (1 - sell_fee)
            for symbol in ALLOCATION_SYMBOLS
        )
        equity = cash + liquidation
        equity_values.append(equity)
        high_water = max(high_water, equity)
        pending = _weights_from_target(targets.loc[timestamp])

    output: dict[str, Any] = {
        "start_date": index[start_position].date().isoformat(),
        "prior_leverage": float(targets.loc[prior_ts]["leverage"]),
        "trade_count": trade_count,
    }
    for horizon in horizons:
        segment = equity_values[:horizon]
        output[f"return_{horizon}"] = segment[-1] / initial_capital - 1.0
        output[f"mdd_{horizon}"] = _maximum_drawdown(segment)
    return output


def summarize_scenarios(
    rows: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = (21, 63, 126),
    baseline_name: str = "LUMP_100",
) -> list[dict[str, Any]]:
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    baseline = frame[frame["scenario"] == baseline_name].set_index("start_date")
    summaries: list[dict[str, Any]] = []

    for scenario, group in frame.groupby("scenario", sort=False):
        group = group.set_index("start_date")
        aligned_baseline = baseline.loc[group.index]
        summary: dict[str, Any] = {
            "scenario": scenario,
            "samples": int(len(group)),
            "avg_trades": float(group["trade_count"].mean()),
        }
        for horizon in horizons:
            returns = group[f"return_{horizon}"] * 100
            mdds = group[f"mdd_{horizon}"] * 100
            base_returns = aligned_baseline[f"return_{horizon}"] * 100
            base_mdds = aligned_baseline[f"mdd_{horizon}"] * 100
            advantage = returns - base_returns
            down_mask = base_returns < 0
            up_mask = base_returns >= 0
            summary.update(
                {
                    f"median_return_{horizon}_pct": float(returns.median()),
                    f"p10_return_{horizon}_pct": float(returns.quantile(0.10)),
                    f"median_mdd_{horizon}_pct": float(mdds.median()),
                    f"worst_mdd_{horizon}_pct": float(mdds.min()),
                    f"beat_lump_{horizon}_pct": float((advantage > 0).mean() * 100),
                    f"median_edge_{horizon}_pctpt": float(advantage.median()),
                    f"mdd_improvement_{horizon}_pctpt": float(
                        (mdds - base_mdds).median()
                    ),
                    f"down_market_edge_{horizon}_pctpt": float(
                        advantage[down_mask].mean() if down_mask.any() else 0.0
                    ),
                    f"up_market_cost_{horizon}_pctpt": float(
                        (-advantage[up_mask]).mean() if up_mask.any() else 0.0
                    ),
                }
            )
        summaries.append(summary)
    return summaries


def leverage_breakdown(
    rows: list[dict[str, Any]],
    *,
    scenario_name: str,
    horizon: int = 63,
    baseline_name: str = "LUMP_100",
) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    scenario = frame[frame["scenario"] == scenario_name].set_index("start_date")
    baseline = frame[frame["scenario"] == baseline_name].set_index("start_date")
    if scenario.empty or baseline.empty:
        return []
    joined = scenario.join(
        baseline[[f"return_{horizon}"]].rename(
            columns={f"return_{horizon}": "baseline_return"}
        ),
        how="inner",
    )
    joined["edge"] = joined[f"return_{horizon}"] - joined["baseline_return"]
    result: list[dict[str, Any]] = []
    for leverage, group in joined.groupby("prior_leverage"):
        result.append(
            {
                "leverage": float(leverage),
                "samples": int(len(group)),
                "median_edge_pctpt": float(group["edge"].median() * 100),
                "beat_lump_pct": float((group["edge"] > 0).mean() * 100),
                "down_market_edge_pctpt": float(
                    group.loc[group["baseline_return"] < 0, "edge"].mean() * 100
                    if (group["baseline_return"] < 0).any()
                    else 0.0
                ),
            }
        )
    return result
