from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

import pandas as pd

from jd_holdings.backtest.performance import maximum_drawdown
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from scripts.research_active import (
    BOOSTER_MAX_WEIGHT,
    INITIAL_CAPITAL,
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

START = "2011-01-01"
V32_REINVEST_FRACTION = 0.25


def _booster_sell_quantity(held: int, purpose: str) -> int:
    if purpose != "TP1":
        return held
    quantity = int(
        (Decimal(held) * Decimal(str(TP1_FRACTION))).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    return max(1, min(held, quantity))


def _daily_twr(
    total_equity: float,
    previous_equity: float | None,
    external_flow: float,
) -> float:
    if previous_equity is None:
        return total_equity / INITIAL_CAPITAL - 1.0
    denominator = previous_equity + external_flow
    return total_equity / denominator - 1.0 if denominator > 0 else 0.0


def simulate_jdss(
    *,
    mode: str,
    config,
    frames,
    start: str,
    end: str,
) -> pd.DataFrame:
    if mode not in {"v31", "v32"}:
        raise ValueError(f"지원하지 않는 mode: {mode}")

    index = common_index(config, frames, start, end)
    booster_events, _ = generate_booster_events(config, frames, start, end)
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

    quantities = {
        "core": {symbol: 0 for symbol in config.enabled_symbols},
        "booster": {symbol: 0 for symbol in config.enabled_symbols},
    }
    pending_core: dict[str, float] | None = None
    core_active_previous = {symbol: False for symbol in config.enabled_symbols}
    booster_cycle_caps: dict[tuple[str, str], float] = {}
    booster_buy_counts: dict[tuple[str, str], int] = defaultdict(int)
    booster_cycle_cashflow: dict[tuple[str, str], float] = defaultdict(float)

    previous_equity: float | None = None
    twr_level = 1.0
    scenario_start_period = index[0].to_period("M")
    rows: list[dict] = []

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
            mode == "v32"
            and timestamp in first_sessions
            and timestamp.to_period("M") != scenario_start_period
        ):
            contribution = contribution_usd(frames["KRW=X"], timestamp)
            active_cash += contribution
            active_base += contribution
            external_flow = contribution

        if pending_core is not None:
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
                trades=[],
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
                gross = quantity * price * (1.0 + buy_fee)
                active_cash -= gross
                quantities["booster"][symbol] += quantity
                booster_cycle_cashflow[key] -= gross
                continue

            held = quantities["booster"][symbol]
            if held <= 0:
                continue
            quantity = _booster_sell_quantity(held, purpose)
            net = quantity * price * (1.0 - sell_fee)
            active_cash += net
            quantities["booster"][symbol] -= quantity
            booster_cycle_cashflow[key] += net

            if quantities["booster"][symbol] == 0:
                cycle_profit = booster_cycle_cashflow.pop(key, 0.0)
                if mode == "v32" and cycle_profit > 0:
                    reinvest = cycle_profit * V32_REINVEST_FRACTION
                    lock_target = cycle_profit - reinvest
                    active_base += reinvest
                    locked = min(active_cash, lock_target)
                    active_cash -= locked
                    profit_reserve += locked
                booster_cycle_caps.pop(key, None)
                booster_buy_counts.pop(key, None)

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
        daily_return = _daily_twr(total_equity, previous_equity, external_flow)
        twr_level *= 1.0 + daily_return
        rows.append(
            {
                "equity": total_equity,
                "daily_return": daily_return,
                "twr": twr_level,
                "contribution": external_flow,
                "active_base": active_base,
                "profit_reserve": profit_reserve,
            }
        )
        previous_equity = total_equity

    return pd.DataFrame(rows, index=index)


def simulate_hybrid(
    *,
    v31_curve: pd.DataFrame,
    frames,
    config,
) -> pd.DataFrame:
    index = v31_curve.index
    first_sessions = first_sessions_by_month(index)
    scenario_start_period = index[0].to_period("M")
    buy_fee = float(config.global_.buy_fee)
    sell_fee = float(config.global_.sell_fee)

    qqq_cash = 0.0
    qqq_quantity = 0
    previous_equity: float | None = None
    twr_level = 1.0
    rows: list[dict] = []

    for timestamp in index:
        external_flow = 0.0
        if (
            timestamp in first_sessions
            and timestamp.to_period("M") != scenario_start_period
        ):
            contribution = contribution_usd(frames["KRW=X"], timestamp)
            qqq_cash += contribution
            external_flow = contribution
            buy_price = float(frames["QQQ"].loc[timestamp, "open"]) * (1.0 + SLIPPAGE)
            quantity = math.floor(qqq_cash / (buy_price * (1.0 + buy_fee)))
            if quantity > 0:
                qqq_cash -= quantity * buy_price * (1.0 + buy_fee)
                qqq_quantity += quantity

        close = float(frames["QQQ"].loc[timestamp, "close"])
        qqq_equity = qqq_cash + qqq_quantity * close * (1.0 - sell_fee)
        total_equity = float(v31_curve.loc[timestamp, "equity"]) + qqq_equity
        daily_return = _daily_twr(total_equity, previous_equity, external_flow)
        twr_level *= 1.0 + daily_return
        rows.append(
            {
                "equity": total_equity,
                "daily_return": daily_return,
                "twr": twr_level,
                "contribution": external_flow,
                "qqq_equity": qqq_equity,
            }
        )
        previous_equity = total_equity

    return pd.DataFrame(rows, index=index)


def period_stats(curve: pd.DataFrame, frequency: str) -> list[dict]:
    if frequency == "year":
        keys = curve.index.to_period("Y")
    elif frequency == "half":
        keys = pd.Index(
            [f"{stamp.year}-H{1 if stamp.month <= 6 else 2}" for stamp in curve.index]
        )
    else:
        raise ValueError(f"지원하지 않는 frequency: {frequency}")

    results: list[dict] = []
    previous_equity = INITIAL_CAPITAL
    for key in pd.Index(keys).unique():
        mask = keys == key
        period = curve.loc[mask]
        if period.empty:
            continue
        period_twr = (1.0 + period["daily_return"]).cumprod()
        contributions = float(period["contribution"].sum())
        ending_equity = float(period["equity"].iloc[-1])
        pnl = ending_equity - previous_equity - contributions
        results.append(
            {
                "period": str(key),
                "start": period.index[0].date().isoformat(),
                "end": period.index[-1].date().isoformat(),
                "return_pct": round((float(period_twr.iloc[-1]) - 1.0) * 100.0, 2),
                "mdd_pct": round(maximum_drawdown(period_twr) * 100.0, 2),
                "pnl_usd": round(pnl, 2),
                "contribution_usd": round(contributions, 2),
                "ending_equity_usd": round(ending_equity, 2),
            }
        )
        previous_equity = ending_equity
    return results


def overall_stats(curve: pd.DataFrame) -> dict:
    years = max(
        (curve.index[-1] - curve.index[0]).days / 365.2425,
        1.0 / 365.2425,
    )
    total_paid_in = INITIAL_CAPITAL + float(curve["contribution"].sum())
    final_equity = float(curve["equity"].iloc[-1])
    flows: list[tuple[date, float]] = [(curve.index[0].date(), -INITIAL_CAPITAL)]
    for timestamp, contribution in curve.loc[curve["contribution"] > 0, "contribution"].items():
        flows.append((timestamp.date(), -float(contribution)))
    flows.append((curve.index[-1].date(), final_equity))
    xirr_value = xirr(flows)
    return {
        "total_paid_in_usd": round(total_paid_in, 2),
        "final_equity_usd": round(final_equity, 2),
        "investment_profit_usd": round(final_equity - total_paid_in, 2),
        "twr_cagr_pct": round(
            (float(curve["twr"].iloc[-1]) ** (1.0 / years) - 1.0) * 100.0,
            2,
        ),
        "twr_mdd_pct": round(maximum_drawdown(curve["twr"]) * 100.0, 2),
        "xirr_pct": round(xirr_value * 100.0, 2) if xirr_value is not None else None,
    }


def _merge_periods(left: list[dict], right: list[dict]) -> list[dict]:
    right_map = {row["period"]: row for row in right}
    merged = []
    for row in left:
        other = right_map[row["period"]]
        merged.append(
            {
                "period": row["period"],
                "start": row["start"],
                "end": row["end"],
                "v31_return_pct": row["return_pct"],
                "v31_mdd_pct": row["mdd_pct"],
                "v31_pnl_usd": row["pnl_usd"],
                "v32_return_pct": other["return_pct"],
                "v32_mdd_pct": other["mdd_pct"],
                "v32_pnl_usd": other["pnl_usd"],
                "v32_contribution_usd": other["contribution_usd"],
                "return_delta_pct_point": round(
                    other["return_pct"] - row["return_pct"],
                    2,
                ),
            }
        )
    return merged


def run(output: str) -> None:
    config = research_config()
    end = MarketClock().latest_completed_session().isoformat()
    warmup_start = (date.fromisoformat(START) - timedelta(days=400)).isoformat()
    frames = load_frames(
        YFinanceDataSource("data/cache/v31-v32-compare"),
        warmup_start,
        end,
    )

    v31 = simulate_jdss(mode="v31", config=config, frames=frames, start=START, end=end)
    v32 = simulate_jdss(mode="v32", config=config, frames=frames, start=START, end=end)
    hybrid = simulate_hybrid(v31_curve=v31, frames=frames, config=config)

    payload = {
        "comparison": {
            "start": v31.index[0].date().isoformat(),
            "end": v31.index[-1].date().isoformat(),
            "v31": "$50k fixed JDSS; no further contributions; no sizing compounding",
            "v32": "$50k + KRW 5m monthly + 25% positive booster profit reinvestment",
            "hybrid": "$50k fixed JDSS + KRW 5m monthly QQQ DCA",
            "costs": "buy/sell 0.10%, slippage 0.10%; QQQ DCA uses same buy cost",
        },
        "overall": {
            "v31": overall_stats(v31),
            "v32": overall_stats(v32),
            "hybrid": overall_stats(hybrid),
        },
        "annual": _merge_periods(
            period_stats(v31, "year"),
            period_stats(v32, "year"),
        ),
        "half_year": _merge_periods(
            period_stats(v31, "half"),
            period_stats(v32, "half"),
        ),
        "hybrid_annual": _merge_periods(
            period_stats(v32, "year"),
            period_stats(hybrid, "year"),
        ),
    }
    Path(output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def summarize(path: str) -> None:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    comparison = payload["comparison"]
    overall = payload["overall"]
    print(f"- Period: {comparison['start']} ~ {comparison['end']}")
    print(f"- V3.1: {comparison['v31']}")
    print(f"- V3.2: {comparison['v32']}")
    print(f"- Hybrid: {comparison['hybrid']}")
    print()
    print("## Overall")
    print("| 안 | 납입총액 | 최종자산 | 투자이익 | TWR CAGR | MDD | XIRR |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for key in ("v31", "v32", "hybrid"):
        stats = overall[key]
        xirr_value = stats["xirr_pct"]
        xirr_text = f"{xirr_value:+.2f}%" if xirr_value is not None else "-"
        print(
            f"| {key.upper()} | {_money(stats['total_paid_in_usd'])} | "
            f"{_money(stats['final_equity_usd'])} | "
            f"{_money(stats['investment_profit_usd'])} | "
            f"{stats['twr_cagr_pct']:+.2f}% | {stats['twr_mdd_pct']:.2f}% | "
            f"{xirr_text} |"
        )

    print()
    print("## Annual V3.1 vs V3.2")
    print("| 연도 | V3.1 수익률 | V3.2 수익률 | 차이 | V3.1 MDD | V3.2 MDD | V3.1 손익 | V3.2 손익 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in payload["annual"]:
        print(
            f"| {row['period']} | {row['v31_return_pct']:+.2f}% | "
            f"{row['v32_return_pct']:+.2f}% | "
            f"{row['return_delta_pct_point']:+.2f}%p | "
            f"{row['v31_mdd_pct']:.2f}% | {row['v32_mdd_pct']:.2f}% | "
            f"{_money(row['v31_pnl_usd'])} | {_money(row['v32_pnl_usd'])} |"
        )

    print()
    print("## Half-year V3.1 vs V3.2")
    print("| 반기 | V3.1 수익률 | V3.2 수익률 | 차이 | V3.1 MDD | V3.2 MDD |")
    print("|---|---:|---:|---:|---:|---:|")
    for row in payload["half_year"]:
        print(
            f"| {row['period']} | {row['v31_return_pct']:+.2f}% | "
            f"{row['v32_return_pct']:+.2f}% | "
            f"{row['return_delta_pct_point']:+.2f}%p | "
            f"{row['v31_mdd_pct']:.2f}% | {row['v32_mdd_pct']:.2f}% |"
        )

    print()
    print("## Annual V3.2 vs Hybrid")
    print("| 연도 | V3.2 수익률 | Hybrid 수익률 | Hybrid-V3.2 | V3.2 손익 | Hybrid 손익 |")
    print("|---|---:|---:|---:|---:|---:|")
    for row in payload["hybrid_annual"]:
        print(
            f"| {row['period']} | {row['v31_return_pct']:+.2f}% | "
            f"{row['v32_return_pct']:+.2f}% | "
            f"{row['return_delta_pct_point']:+.2f}%p | "
            f"{_money(row['v31_pnl_usd'])} | {_money(row['v32_pnl_usd'])} |"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="v31-v32-compare.json")
    parser.add_argument("--summarize")
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize)
        return
    run(args.output)


if __name__ == "__main__":
    main()
