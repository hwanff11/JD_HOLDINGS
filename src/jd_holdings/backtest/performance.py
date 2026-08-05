from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def maximum_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_high = equity.cummax()
    drawdown = equity / running_high - 1.0
    return float(drawdown.min())


def maximum_underwater_days(equity: pd.Series) -> int:
    if equity.empty:
        return 0
    running_high = equity.cummax()
    underwater = equity < running_high
    longest = current = 0
    for value in underwater:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def risk_adjusted_metrics(equity: pd.Series, annualization_days: int) -> tuple[float, float]:
    returns = equity.pct_change().dropna()
    if returns.empty:
        return 0.0, 0.0
    std = float(returns.std(ddof=1))
    sharpe = float(returns.mean()) / std * math.sqrt(annualization_days) if std > 0 else 0.0
    downside = returns[returns < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = (
        float(returns.mean()) / downside_std * math.sqrt(annualization_days)
        if downside_std > 0
        else 0.0
    )
    return sharpe, sortino


def summarize_performance(
    equity: pd.Series,
    closed_cycles: list[dict[str, Any]],
    *,
    signal_count: int,
    executed_entries: int,
    capital_utilization: list[float],
    tp1_hits: int,
    tp2_hits: int,
    rebuy_cycles: int,
    rebuy_profitable_cycles: int,
    annualization_days: int,
) -> dict[str, float | int]:
    initial = float(equity.iloc[0]) if not equity.empty else 0.0
    final = float(equity.iloc[-1]) if not equity.empty else initial
    total_return = final / initial - 1.0 if initial > 0 else 0.0
    elapsed_days = max(1, (equity.index[-1] - equity.index[0]).days) if len(equity) > 1 else 1
    years = elapsed_days / 365.2425
    cagr = (final / initial) ** (1 / years) - 1.0 if initial > 0 and final > 0 else -1.0
    pnls = [float(cycle["pnl"]) for cycle in closed_cycles]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (
        gross_profit / gross_loss if gross_loss > 0 else (math.inf if gross_profit else 0.0)
    )
    sharpe, sortino = risk_adjusted_metrics(equity, annualization_days)
    holding_days = [int(cycle["holding_days"]) for cycle in closed_cycles]
    maes = [float(cycle["mae"]) for cycle in closed_cycles]
    mfes = [float(cycle["mfe"]) for cycle in closed_cycles]
    periods_years = max(elapsed_days / 365.2425, 1 / 365.2425)
    return {
        "initial_equity": round(initial, 2),
        "final_equity": round(final, 2),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "closed_cycles": len(closed_cycles),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
        "profit_factor": round(profit_factor, 3) if math.isfinite(profit_factor) else math.inf,
        "expectancy_usd": round(float(np.mean(pnls)), 2) if pnls else 0.0,
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "average_holding_days": round(float(np.mean(holding_days)), 2) if holding_days else 0.0,
        "median_holding_days": round(float(np.median(holding_days)), 2) if holding_days else 0.0,
        "maximum_holding_days": max(holding_days, default=0),
        "average_mae_pct": round(float(np.mean(maes)) * 100, 2) if maes else 0.0,
        "worst_mae_pct": round(min(maes, default=0.0) * 100, 2),
        "average_mfe_pct": round(float(np.mean(mfes)) * 100, 2) if mfes else 0.0,
        "tp1_hits": tp1_hits,
        "tp2_hits": tp2_hits,
        "tp1_reach_rate_pct": round(tp1_hits / executed_entries * 100, 2)
        if executed_entries
        else 0.0,
        "tp2_reach_rate_pct": round(tp2_hits / executed_entries * 100, 2)
        if executed_entries
        else 0.0,
        "rebuy_cycles": rebuy_cycles,
        "rebuy_success_rate_pct": round(rebuy_profitable_cycles / rebuy_cycles * 100, 2)
        if rebuy_cycles
        else 0.0,
        "signals": signal_count,
        "signals_per_year": round(signal_count / periods_years, 2),
        "executed_entries": executed_entries,
        "average_capital_utilization_pct": round(float(np.mean(capital_utilization)) * 100, 2)
        if capital_utilization
        else 0.0,
        "maximum_underwater_trading_days": maximum_underwater_days(equity),
        "lockup_over_20_days_pct": round(
            sum(day > 20 for day in holding_days) / len(holding_days) * 100, 2
        )
        if holding_days
        else 0.0,
        "lockup_over_40_days_pct": round(
            sum(day > 40 for day in holding_days) / len(holding_days) * 100, 2
        )
        if holding_days
        else 0.0,
        "lockup_over_60_days_pct": round(
            sum(day > 60 for day in holding_days) / len(holding_days) * 100, 2
        )
        if holding_days
        else 0.0,
    }
