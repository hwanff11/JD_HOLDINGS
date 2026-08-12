from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

import pandas as pd

from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from scripts.research_active import (
    BOOSTER_MAX_WEIGHT,
    INITIAL_CAPITAL,
    MONTHLY_CONTRIBUTION_KRW,
    SLIPPAGE,
    STAGE_WEIGHTS,
    TP1_FRACTION,
    common_index,
    contribution_usd,
    first_sessions_by_month,
    generate_booster_events,
    load_frames,
    research_config,
    xirr,
)

SCENARIOS = {
    "full": "2011-01-01",
    "recent": "2022-01-01",
}

VARIANTS = {
    "profit0": (0.00, "V3.1 매매로직 + 월급 편입 + 확정이익 재투자 없음"),
    "profit25": (0.25, "V3.1 매매로직 + 월급 편입 + 확정이익 25% 재투자"),
    "profit30": (0.30, "V3.1 매매로직 + 월급 편입 + 확정이익 30% 재투자"),
}


def split_positive_profit(profit: float, reinvest_fraction: float) -> tuple[float, float]:
    """Return (reinvest, reserve) for a completed positive booster cycle."""
    if profit <= 0:
        return 0.0, 0.0
    reinvest = profit * reinvest_fraction
    return reinvest, profit - reinvest


def _booster_quantity(held: int, purpose: str) -> int:
    if purpose != "TP1":
        return held
    quantity = int(
        (Decimal(held) * Decimal(str(TP1_FRACTION))).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    return max(1, min(held, quantity))


def _mark_positions(
    cash: float,
    quantities: dict[str, dict[str, int]],
    prices: dict[str, float],
) -> float:
    return cash + sum(
        quantities[component][symbol] * prices[symbol]
        for component in quantities
        for symbol in prices
    )


def simulate(
    *,
    reinvest_fraction: float,
    config,
    frames,
    start: str,
    end: str,
) -> dict:
    index = common_index(config, frames, start, end)
    booster_events, source_metrics = generate_booster_events(
        config,
        frames,
        start,
        end,
    )
    production_engine = PortfolioBacktestEngine(config)
    month_ends = production_engine._month_end_sessions(index)
    first_sessions = first_sessions_by_month(index)
    trends = {
        symbol: production_engine._monthly_trend(
            frames[underlying],
            index,
            config.portfolio.trend_months,
        )
        for symbol, underlying in config.portfolio.core_underlyings.items()
    }

    buy_fee = float(config.global_.buy_fee)
    sell_fee = float(config.global_.sell_fee)
    active_base = INITIAL_CAPITAL
    active_cash = INITIAL_CAPITAL
    profit_reserve = 0.0
    profit_reinvested = 0.0
    profit_locked = 0.0

    quantities = {
        "core": {symbol: 0 for symbol in config.enabled_symbols},
        "booster": {symbol: 0 for symbol in config.enabled_symbols},
    }
    pending_core: dict[str, float] | None = None
    core_active_previous = {symbol: False for symbol in config.enabled_symbols}
    booster_cycle_caps: dict[tuple[str, str], float] = {}
    booster_buy_counts: dict[tuple[str, str], int] = defaultdict(int)
    booster_cycle_cashflow: dict[tuple[str, str], float] = defaultdict(float)

    trades: list[dict] = []
    total_equity_values: list[float] = []
    twr_values: list[float] = []
    exposures: list[float] = []
    active_base_values: list[float] = []
    reserve_values: list[float] = []
    contributions: list[float] = []
    flows: list[tuple[date, float]] = [(index[0].date(), -INITIAL_CAPITAL)]
    previous_equity: float | None = None
    twr_level = 1.0
    scenario_start_period = index[0].to_period("M")

    for timestamp in index:
        opens = {
            symbol: float(frames[symbol].loc[timestamp, "open"])
            for symbol in config.enabled_symbols
        }
        closes = {
            symbol: float(frames[symbol].loc[timestamp, "close"])
            for symbol in config.enabled_symbols
        }

        external_flow = 0.0
        if (
            timestamp in first_sessions
            and timestamp.to_period("M") != scenario_start_period
        ):
            contribution = contribution_usd(frames["KRW=X"], timestamp)
            contributions.append(contribution)
            flows.append((timestamp.date(), -contribution))
            external_flow = contribution
            active_cash += contribution
            active_base += contribution

        if pending_core is not None:
            # Use the production portfolio engine's exact sell-first/buy-second,
            # fee, slippage, integer quantity and available-cash implementation.
            # The candidate contract deliberately supplies Active Capital Base as
            # the sizing equity so locked Profit Reserve cannot be re-risked.
            active_cash = production_engine._rebalance_core(
                pending_core,
                quantities["core"],
                opens,
                cash=active_cash,
                equity=active_base,
                timestamp=timestamp,
                buy_fee=buy_fee,
                sell_fee=sell_fee,
                slippage=SLIPPAGE,
                trades=trades,
            )
            pending_core = None

        for event in booster_events.get(timestamp, []):
            symbol = str(event["symbol"])
            side = str(event["side"])
            purpose = str(event.get("purpose", "JDSS"))
            price = float(event["price"])
            cycle_id = str(event.get("cycle_id") or "")
            key = (symbol, cycle_id)

            if side == "BUY":
                stage_index = booster_buy_counts[key]
                if stage_index >= len(STAGE_WEIGHTS):
                    continue
                if key not in booster_cycle_caps:
                    booster_cycle_caps[key] = active_base * BOOSTER_MAX_WEIGHT
                budget = booster_cycle_caps[key] * STAGE_WEIGHTS[stage_index]
                booster_buy_counts[key] += 1
                quantity = math.floor(budget / (price * (1.0 + buy_fee)))
                affordable = math.floor(active_cash / (price * (1.0 + buy_fee)))
                quantity = min(quantity, affordable)
                if quantity <= 0:
                    continue
                fee = quantity * price * buy_fee
                gross = quantity * price + fee
                active_cash -= gross
                quantities["booster"][symbol] += quantity
                booster_cycle_cashflow[key] -= gross
            else:
                held = quantities["booster"][symbol]
                if held <= 0:
                    continue
                quantity = _booster_quantity(held, purpose)
                fee = quantity * price * sell_fee
                net = quantity * price - fee
                active_cash += net
                quantities["booster"][symbol] -= quantity
                booster_cycle_cashflow[key] += net

                if quantities["booster"][symbol] == 0:
                    cycle_profit = booster_cycle_cashflow.pop(key, 0.0)
                    reinvest, lock_target = split_positive_profit(
                        cycle_profit,
                        reinvest_fraction,
                    )
                    if reinvest > 0:
                        active_base += reinvest
                        profit_reinvested += reinvest
                    if lock_target > 0:
                        locked = min(active_cash, lock_target)
                        active_cash -= locked
                        profit_reserve += locked
                        profit_locked += locked
                    booster_cycle_caps.pop(key, None)
                    booster_buy_counts.pop(key, None)

            trades.append(
                {
                    "date": timestamp.date().isoformat(),
                    "component": "booster",
                    "symbol": symbol,
                    "side": side,
                    "purpose": purpose,
                    "cycle_id": cycle_id,
                    "quantity": quantity,
                    "price": round(price, 6),
                    "fee": round(fee, 6),
                }
            )

        if timestamp in month_ends:
            pending_core = {}
            for symbol in config.enabled_symbols:
                active = bool(trends[symbol].loc[timestamp])
                if not active:
                    weight = 0.0
                elif core_active_previous[symbol]:
                    weight = float(config.portfolio.core_target_weight)
                else:
                    weight = float(config.portfolio.core_initial_weight)
                pending_core[symbol] = weight
                core_active_previous[symbol] = active

        liquidation = sum(
            quantities[component][symbol] * closes[symbol] * (1.0 - sell_fee)
            for component in quantities
            for symbol in config.enabled_symbols
        )
        active_equity = active_cash + liquidation
        total_equity = active_equity + profit_reserve
        total_equity_values.append(total_equity)
        active_base_values.append(active_base)
        reserve_values.append(profit_reserve)
        exposures.append(liquidation / total_equity if total_equity > 0 else 0.0)

        if previous_equity is None:
            daily_return = total_equity / INITIAL_CAPITAL - 1.0
        else:
            denominator = previous_equity + external_flow
            daily_return = total_equity / denominator - 1.0 if denominator > 0 else 0.0
        twr_level *= 1.0 + daily_return
        twr_values.append(twr_level)
        previous_equity = total_equity

    equity_curve = pd.Series(total_equity_values, index=index)
    twr_curve = pd.Series(twr_values, index=index)
    years = max((index[-1] - index[0]).days / 365.2425, 1.0 / 365.2425)
    twr_cagr = (
        float(twr_curve.iloc[-1] ** (1.0 / years) - 1.0)
        if twr_curve.iloc[-1] > 0
        else -1.0
    )
    sharpe, sortino = risk_adjusted_metrics(twr_curve, config.backtest.annualization_days)
    total_contributed = sum(contributions)
    total_paid_in = INITIAL_CAPITAL + total_contributed
    final_equity = float(equity_curve.iloc[-1])
    investment_profit = final_equity - total_paid_in
    flows.append((index[-1].date(), final_equity))
    money_weighted = xirr(flows)

    return {
        "start_date": index[0].date().isoformat(),
        "end_date": index[-1].date().isoformat(),
        "metrics": {
            "total_paid_in_usd": round(total_paid_in, 2),
            "final_equity_usd": round(final_equity, 2),
            "investment_profit_usd": round(investment_profit, 2),
            "twr_cagr_pct": round(twr_cagr * 100.0, 2),
            "twr_mdd_pct": round(maximum_drawdown(twr_curve) * 100.0, 2),
            "twr_sharpe": round(sharpe, 3),
            "twr_sortino": round(sortino, 3),
            "xirr_pct": round(money_weighted * 100.0, 2)
            if money_weighted is not None
            else None,
            "average_exposure_pct": round(sum(exposures) / len(exposures) * 100.0, 2),
            "final_active_base_usd": round(active_base_values[-1], 2),
            "final_profit_reserve_usd": round(reserve_values[-1], 2),
            "profit_reinvested_usd": round(profit_reinvested, 2),
            "profit_locked_usd": round(profit_locked, 2),
            "trade_fills": len(trades),
            "core_fills": sum(trade["component"] == "core" for trade in trades),
            "booster_fills": sum(trade["component"] == "booster" for trade in trades),
        },
        "source_booster_metrics": source_metrics,
    }


def run(variant: str, output: str) -> None:
    fraction, label = VARIANTS[variant]
    config = research_config()
    end = MarketClock().latest_completed_session().isoformat()
    earliest = min(date.fromisoformat(value) for value in SCENARIOS.values())
    warmup_start = (earliest - timedelta(days=400)).isoformat()
    frames = load_frames(
        YFinanceDataSource("data/cache/profit-lock-parity"),
        warmup_start,
        end,
    )
    payload = {
        "research": {
            "variant": variant,
            "label": label,
            "strategy": config.version,
            "config_version": config.config_version,
            "initial_capital_usd": INITIAL_CAPITAL,
            "monthly_contribution_krw": MONTHLY_CONTRIBUTION_KRW,
            "profit_reinvest_fraction": fraction,
            "profit_reserve_fraction": 1.0 - fraction,
            "sgov_enabled": False,
            "core_execution": "PortfolioBacktestEngine._rebalance_core",
            "booster_signal_source": "StrategyBacktestEngine V3.1 trades",
            "booster_cycle_cap_rule": "freeze active_base*40% at first BUY",
            "slippage": SLIPPAGE,
            "buy_fee": float(config.global_.buy_fee),
            "sell_fee": float(config.global_.sell_fee),
        },
        "scenarios": {},
    }
    for name, start in SCENARIOS.items():
        payload["scenarios"][name] = simulate(
            reinvest_fraction=fraction,
            config=config,
            frames=frames,
            start=start,
            end=end,
        )
    Path(output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def summarize(path: str) -> None:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    research = payload["research"]
    print(f"- Variant: {research['variant']} — {research['label']}")
    print(f"- Strategy: {research['strategy']} / {research['config_version']}")
    print("- Capital: $50,000 initial + KRW 5,000,000 monthly")
    print("- Profit Reserve: USD only; SGOV OFF")
    print("- Core execution: production PortfolioBacktestEngine._rebalance_core")
    print("- Booster timing: production StrategyBacktestEngine V3.1 event stream")
    print("- Costs: buy 0.10% / sell 0.10% / slippage 0.10%")
    print()
    print(
        "| 구간 | 최종자산 | 투자이익 | CAGR | MDD | Sharpe | XIRR | "
        "평균노출 | Active Base | Profit Reserve |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for scenario, result in payload["scenarios"].items():
        metrics = result["metrics"]
        xirr_value = metrics["xirr_pct"]
        xirr_text = f"{xirr_value:+.2f}%" if xirr_value is not None else "-"
        print(
            f"| {scenario} | ${metrics['final_equity_usd']:,.0f} | "
            f"${metrics['investment_profit_usd']:,.0f} | "
            f"{metrics['twr_cagr_pct']:+.2f}% | {metrics['twr_mdd_pct']:.2f}% | "
            f"{metrics['twr_sharpe']:.3f} | {xirr_text} | "
            f"{metrics['average_exposure_pct']:.2f}% | "
            f"${metrics['final_active_base_usd']:,.0f} | "
            f"${metrics['final_profit_reserve_usd']:,.0f} |"
        )
        print(
            f"  - locked=${metrics['profit_locked_usd']:,.0f}, "
            f"reinvested=${metrics['profit_reinvested_usd']:,.0f}, "
            f"fills(core/booster)={metrics['core_fills']}/{metrics['booster_fills']}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=tuple(VARIANTS))
    parser.add_argument("--output")
    parser.add_argument("--summarize")
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize)
    else:
        run(args.variant, args.output)
