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

FIXED_STEP_USD = 5_000.0
COMBINED_BOOSTER_CAP = 0.60

VARIANTS = (
    "fixed50",
    "fixed50_harvest",
    "step5",
    "contrib_profit25",
    "contrib_profit50",
    "fixed50_dd",
    "fixed50_harvest_dd",
    "fixed50_corr60",
    "fortress50",
)

VARIANT_LABELS = {
    "fixed50": "$50k 고정 위험예산 / 월급은 USD Reserve",
    "fixed50_harvest": "$50k 고정 + 월 1회 초과현금 이익잠금",
    "step5": "월급 Reserve가 $5k 쌓일 때만 위험예산 +$5k",
    "contrib_profit25": "월급 100% 편입 + 부스터 확정이익 25%만 복리",
    "contrib_profit50": "월급 100% 편입 + 부스터 확정이익 50% 복리",
    "fixed50_dd": "$50k 고정 + Active DD 감속",
    "fixed50_harvest_dd": "$50k 고정 + 이익잠금 + Active DD 감속",
    "fixed50_corr60": "$50k 고정 + TQQQ/SOXL 부스터 합산 60% 상한",
    "fortress50": "$50k 고정 + 이익잠금 + DD 감속 + 부스터 합산 60%",
}

SCENARIOS = {
    "full": "2011-01-01",
    "recent": "2022-01-01",
}


def uses_harvest(variant: str) -> bool:
    return variant in {"fixed50_harvest", "fixed50_harvest_dd", "fortress50"}


def uses_dd_brake(variant: str) -> bool:
    return variant in {"fixed50_dd", "fixed50_harvest_dd", "fortress50"}


def uses_corr_cap(variant: str) -> bool:
    return variant in {"fixed50_corr60", "fortress50"}


def profit_reinvest_fraction(variant: str) -> float:
    if variant == "contrib_profit25":
        return 0.25
    if variant == "contrib_profit50":
        return 0.50
    return 0.0


def dd_multiplier(drawdown: float) -> float:
    if drawdown <= -0.16:
        return 0.40
    if drawdown <= -0.12:
        return 0.60
    if drawdown <= -0.08:
        return 0.80
    return 1.00


def mark_positions(
    cash: float,
    quantities: dict[str, dict[str, int]],
    prices: dict[str, float],
) -> float:
    return cash + sum(
        quantities[component][symbol] * prices[symbol]
        for component in quantities
        for symbol in prices
    )


def booster_market_value(
    quantities: dict[str, dict[str, int]],
    prices: dict[str, float],
) -> float:
    return sum(
        quantities["booster"][symbol] * prices[symbol]
        for symbol in prices
    )


def harvest_excess_cash(
    active_base: float,
    active_cash: float,
    quantities: dict[str, dict[str, int]],
    prices: dict[str, float],
) -> tuple[float, float]:
    active_equity = mark_positions(active_cash, quantities, prices)
    excess = max(0.0, active_equity - active_base)
    transfer = min(active_cash, excess)
    return active_cash - transfer, transfer


def simulate_variant(
    variant: str,
    config,
    frames,
    start: str,
    end: str,
):
    index = common_index(config, frames, start, end)
    booster_events, source_metrics = generate_booster_events(
        config,
        frames,
        start,
        end,
    )
    month_ends = PortfolioBacktestEngine._month_end_sessions(index)
    first_sessions = first_sessions_by_month(index)
    trends = {
        symbol: PortfolioBacktestEngine._monthly_trend(
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
    reserve_cash = 0.0
    harvested_total = 0.0
    profit_reinvested = 0.0
    fixed_step_transfers = 0

    quantities = {
        "core": {symbol: 0 for symbol in config.enabled_symbols},
        "booster": {symbol: 0 for symbol in config.enabled_symbols},
    }
    pending_core: dict[str, float] | None = None
    core_active_previous = {
        symbol: False for symbol in config.enabled_symbols
    }
    booster_cycle_caps: dict[tuple[str, str], float] = {}
    booster_buy_counts: dict[tuple[str, str], int] = defaultdict(int)
    booster_cycle_cashflow: dict[tuple[str, str], float] = defaultdict(float)

    trades: list[dict] = []
    equity_values: list[float] = []
    twr_values: list[float] = []
    exposures: list[float] = []
    reserve_values: list[float] = []
    active_base_values: list[float] = []
    risk_multipliers: list[float] = []
    combined_booster_exposures: list[float] = []
    contributions: list[float] = []
    flows: list[tuple[date, float]] = [
        (index[0].date(), -INITIAL_CAPITAL)
    ]
    previous_equity: float | None = None
    twr_level = 1.0
    dd_nav_hwm = INITIAL_CAPITAL

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
        is_first_session = timestamp in first_sessions
        is_contribution_month = (
            timestamp.to_period("M") != scenario_start_period
        )

        if is_first_session and uses_harvest(variant):
            active_cash, harvested = harvest_excess_cash(
                active_base,
                active_cash,
                quantities,
                opens,
            )
            reserve_cash += harvested
            harvested_total += harvested

        if is_first_session and is_contribution_month:
            contribution = contribution_usd(
                frames["KRW=X"],
                timestamp,
            )
            contributions.append(contribution)
            flows.append((timestamp.date(), -contribution))
            external_flow = contribution

            if variant in {"contrib_profit25", "contrib_profit50"}:
                active_cash += contribution
                active_base += contribution
            else:
                reserve_cash += contribution

        if variant == "step5":
            while reserve_cash + 1e-9 >= FIXED_STEP_USD:
                reserve_cash -= FIXED_STEP_USD
                active_cash += FIXED_STEP_USD
                active_base += FIXED_STEP_USD
                fixed_step_transfers += 1

        active_mark = mark_positions(
            active_cash,
            quantities,
            opens,
        )
        dd_nav = active_mark + harvested_total
        dd_nav_hwm = max(dd_nav_hwm, dd_nav)
        active_drawdown = (
            dd_nav / dd_nav_hwm - 1.0
            if dd_nav_hwm > 0
            else 0.0
        )
        risk_multiplier = (
            dd_multiplier(active_drawdown)
            if uses_dd_brake(variant)
            else 1.0
        )
        risk_base = active_base * risk_multiplier
        risk_multipliers.append(risk_multiplier)

        if pending_core is not None:
            changes = {}
            for symbol, weight in pending_core.items():
                buy_price = opens[symbol] * (1.0 + SLIPPAGE)
                sell_price = opens[symbol] * (1.0 - SLIPPAGE)
                target_dollars = float(weight) * risk_base
                target_qty = math.floor(
                    target_dollars
                    / (buy_price * (1.0 + buy_fee))
                )
                changes[symbol] = (
                    target_qty - quantities["core"][symbol],
                    buy_price,
                    sell_price,
                )

            for symbol, (difference, _, sell_price) in changes.items():
                if difference >= 0:
                    continue
                quantity = -difference
                fee = quantity * sell_price * sell_fee
                active_cash += quantity * sell_price - fee
                quantities["core"][symbol] -= quantity
                trades.append(
                    {
                        "date": timestamp.date().isoformat(),
                        "component": "core",
                        "symbol": symbol,
                        "side": "SELL",
                        "purpose": "CORE_REBALANCE_SELL",
                        "quantity": quantity,
                        "price": round(sell_price, 6),
                        "fee": round(fee, 6),
                    }
                )

            for symbol, (difference, buy_price, _) in changes.items():
                if difference <= 0:
                    continue
                affordable = math.floor(
                    active_cash
                    / (buy_price * (1.0 + buy_fee))
                )
                quantity = min(difference, affordable)
                if quantity <= 0:
                    continue
                fee = quantity * buy_price * buy_fee
                active_cash -= quantity * buy_price + fee
                quantities["core"][symbol] += quantity
                trades.append(
                    {
                        "date": timestamp.date().isoformat(),
                        "component": "core",
                        "symbol": symbol,
                        "side": "BUY",
                        "purpose": "CORE_REBALANCE_BUY",
                        "quantity": quantity,
                        "price": round(buy_price, 6),
                        "fee": round(fee, 6),
                    }
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
                buy_number = booster_buy_counts[key]
                if buy_number >= len(STAGE_WEIGHTS):
                    continue
                if key not in booster_cycle_caps:
                    booster_cycle_caps[key] = (
                        risk_base * BOOSTER_MAX_WEIGHT
                    )
                budget = (
                    booster_cycle_caps[key]
                    * STAGE_WEIGHTS[buy_number]
                )

                if uses_corr_cap(variant):
                    combined_value = booster_market_value(
                        quantities,
                        opens,
                    )
                    remaining = max(
                        0.0,
                        risk_base * COMBINED_BOOSTER_CAP
                        - combined_value,
                    )
                    budget = min(budget, remaining)

                booster_buy_counts[key] += 1
                quantity = math.floor(
                    budget / (price * (1.0 + buy_fee))
                )
                affordable = math.floor(
                    active_cash / (price * (1.0 + buy_fee))
                )
                quantity = min(quantity, affordable)
                if quantity <= 0:
                    continue

                fee = quantity * price * buy_fee
                gross_cost = quantity * price + fee
                active_cash -= gross_cost
                quantities["booster"][symbol] += quantity
                booster_cycle_cashflow[key] -= gross_cost
            else:
                held = quantities["booster"][symbol]
                if held <= 0:
                    continue
                if purpose == "TP1":
                    quantity = int(
                        (
                            Decimal(held)
                            * Decimal(str(TP1_FRACTION))
                        ).to_integral_value(
                            rounding=ROUND_CEILING
                        )
                    )
                    quantity = max(
                        1,
                        min(held, quantity),
                    )
                else:
                    quantity = held

                fee = quantity * price * sell_fee
                net_sale = quantity * price - fee
                active_cash += net_sale
                quantities["booster"][symbol] -= quantity
                booster_cycle_cashflow[key] += net_sale

                if quantities["booster"][symbol] == 0:
                    cycle_profit = booster_cycle_cashflow.pop(
                        key,
                        0.0,
                    )
                    fraction = profit_reinvest_fraction(variant)
                    if cycle_profit > 0 and fraction > 0:
                        reinvest = cycle_profit * fraction
                        harvest = cycle_profit - reinvest
                        active_base += reinvest
                        profit_reinvested += reinvest
                        cash_harvest = min(active_cash, harvest)
                        active_cash -= cash_harvest
                        reserve_cash += cash_harvest
                        harvested_total += cash_harvest
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
                    "risk_multiplier": round(risk_multiplier, 2),
                }
            )

        if timestamp in month_ends:
            pending_core = {}
            for symbol in config.enabled_symbols:
                active = bool(trends[symbol].loc[timestamp])
                if not active:
                    weight = 0.0
                elif core_active_previous[symbol]:
                    weight = float(
                        config.portfolio.core_target_weight
                    )
                else:
                    weight = float(
                        config.portfolio.core_initial_weight
                    )
                pending_core[symbol] = weight
                core_active_previous[symbol] = active

        liquidation = sum(
            quantities[component][symbol]
            * closes[symbol]
            * (1.0 - sell_fee)
            for component in quantities
            for symbol in config.enabled_symbols
        )
        active_equity_end = active_cash + liquidation
        total_equity = active_equity_end + reserve_cash
        equity_values.append(total_equity)
        reserve_values.append(reserve_cash)
        active_base_values.append(active_base)
        exposures.append(
            liquidation / total_equity
            if total_equity > 0
            else 0.0
        )
        combined_booster_exposures.append(
            booster_market_value(quantities, closes)
            / total_equity
            if total_equity > 0
            else 0.0
        )

        if previous_equity is None:
            daily_return = total_equity / INITIAL_CAPITAL - 1.0
        else:
            denominator = previous_equity + external_flow
            daily_return = (
                total_equity / denominator - 1.0
                if denominator > 0
                else 0.0
            )
        twr_level *= 1.0 + daily_return
        twr_values.append(twr_level)
        previous_equity = total_equity

    equity_curve = pd.Series(equity_values, index=index)
    twr_curve = pd.Series(twr_values, index=index)
    years = max(
        (index[-1] - index[0]).days / 365.2425,
        1.0 / 365.2425,
    )
    twr_total = float(twr_curve.iloc[-1] - 1.0)
    twr_cagr = (
        float(
            twr_curve.iloc[-1] ** (1.0 / years) - 1.0
        )
        if twr_curve.iloc[-1] > 0
        else -1.0
    )
    sharpe, sortino = risk_adjusted_metrics(
        twr_curve,
        config.backtest.annualization_days,
    )
    total_contributed = sum(contributions)
    total_paid_in = INITIAL_CAPITAL + total_contributed
    final_equity = float(equity_curve.iloc[-1])
    investment_profit = final_equity - total_paid_in
    flows.append((index[-1].date(), final_equity))
    money_weighted = xirr(flows)

    metrics = {
        "initial_capital": round(INITIAL_CAPITAL, 2),
        "monthly_contribution_krw": round(
            MONTHLY_CONTRIBUTION_KRW,
            0,
        ),
        "contribution_count": len(contributions),
        "total_contributions_usd": round(
            total_contributed,
            2,
        ),
        "total_paid_in_usd": round(total_paid_in, 2),
        "final_equity_usd": round(final_equity, 2),
        "investment_profit_usd": round(
            investment_profit,
            2,
        ),
        "twr_total_return_pct": round(
            twr_total * 100.0,
            2,
        ),
        "twr_cagr_pct": round(
            twr_cagr * 100.0,
            2,
        ),
        "twr_mdd_pct": round(
            maximum_drawdown(twr_curve) * 100.0,
            2,
        ),
        "twr_sharpe": round(sharpe, 3),
        "twr_sortino": round(sortino, 3),
        "xirr_pct": (
            round(money_weighted * 100.0, 2)
            if money_weighted is not None
            else None
        ),
        "average_exposure_pct": round(
            sum(exposures) / len(exposures) * 100.0,
            2,
        ),
        "max_booster_exposure_pct": round(
            max(combined_booster_exposures) * 100.0,
            2,
        ),
        "final_active_base_usd": round(
            active_base_values[-1],
            2,
        ),
        "final_reserve_usd": round(
            reserve_values[-1],
            2,
        ),
        "harvested_total_usd": round(
            harvested_total,
            2,
        ),
        "profit_reinvested_usd": round(
            profit_reinvested,
            2,
        ),
        "fixed_step_transfers": fixed_step_transfers,
        "average_risk_multiplier": round(
            sum(risk_multipliers) / len(risk_multipliers),
            4,
        ),
        "minimum_risk_multiplier": round(
            min(risk_multipliers),
            2,
        ),
        "trade_fills": len(trades),
        "core_fills": sum(
            trade["component"] == "core"
            for trade in trades
        ),
        "booster_fills": sum(
            trade["component"] == "booster"
            for trade in trades
        ),
    }
    return {
        "start_date": index[0].date().isoformat(),
        "end_date": index[-1].date().isoformat(),
        "metrics": metrics,
        "source_booster_metrics": source_metrics,
    }


def run(variant: str, output: str) -> None:
    config = research_config()
    end = MarketClock().latest_completed_session().isoformat()
    earliest = min(
        date.fromisoformat(value)
        for value in SCENARIOS.values()
    )
    warmup_start = (
        earliest - timedelta(days=400)
    ).isoformat()
    data_source = YFinanceDataSource(
        "data/cache/risk-budget",
    )
    frames = load_frames(
        data_source,
        warmup_start,
        end,
    )

    payload = {
        "research": {
            "variant": variant,
            "label": VARIANT_LABELS[variant],
            "strategy": config.version,
            "config_version": config.config_version,
            "initial_capital_usd": INITIAL_CAPITAL,
            "monthly_contribution_krw": (
                MONTHLY_CONTRIBUTION_KRW
            ),
            "fixed_step_usd": FIXED_STEP_USD,
            "combined_booster_cap": (
                COMBINED_BOOSTER_CAP
            ),
            "slippage": SLIPPAGE,
            "buy_fee": float(config.global_.buy_fee),
            "sell_fee": float(config.global_.sell_fee),
        },
        "scenarios": {},
    }
    for name, start in SCENARIOS.items():
        payload["scenarios"][name] = simulate_variant(
            variant,
            config,
            frames,
            start,
            end,
        )
    Path(output).write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def summarize(path: str) -> None:
    payload = json.loads(
        Path(path).read_text(encoding="utf-8")
    )
    research = payload["research"]
    print(
        f"- Variant: {research['variant']} — "
        f"{research['label']}"
    )
    print(
        f"- Strategy: {research['strategy']} / "
        f"{research['config_version']}"
    )
    print(
        "- Capital: $50,000 initial + "
        "KRW 5,000,000 monthly"
    )
    print(
        "- Costs: buy 0.10% / sell 0.10% / "
        "slippage 0.10%"
    )
    print()
    print(
        "| 구간 | 최종자산 | 투자이익 | CAGR | MDD | "
        "Sharpe | XIRR | 평균노출 | 최대부스터 | Reserve |"
    )
    print(
        "|---|---:|---:|---:|---:|---:|---:|"
        "---:|---:|---:|"
    )
    for scenario, result in payload["scenarios"].items():
        m = result["metrics"]
        print(
            f"| {scenario} | "
            f"${m['final_equity_usd']:,.0f} | "
            f"${m['investment_profit_usd']:,.0f} | "
            f"{m['twr_cagr_pct']:+.2f}% | "
            f"{m['twr_mdd_pct']:.2f}% | "
            f"{m['twr_sharpe']:.3f} | "
            f"{m['xirr_pct']:+.2f}% | "
            f"{m['average_exposure_pct']:.2f}% | "
            f"{m['max_booster_exposure_pct']:.2f}% | "
            f"${m['final_reserve_usd']:,.0f} |"
        )
        print(
            f"  - base=${m['final_active_base_usd']:,.0f}, "
            f"harvest=${m['harvested_total_usd']:,.0f}, "
            f"profit_reinvest=${m['profit_reinvested_usd']:,.0f}, "
            f"steps={m['fixed_step_transfers']}, "
            f"risk={m['average_risk_multiplier']:.3f}/"
            f"{m['minimum_risk_multiplier']:.2f}, "
            f"fills={m['core_fills']}/{m['booster_fills']}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variant",
        choices=VARIANTS,
    )
    parser.add_argument("--output")
    parser.add_argument("--summarize")
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize)
    else:
        run(args.variant, args.output)
