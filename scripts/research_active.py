from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

import pandas as pd

from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource

INITIAL_CAPITAL = 50_000.0
MONTHLY_CONTRIBUTION_KRW = 5_000_000.0
BOOSTER_MAX_WEIGHT = 0.40
STEP_FRACTION = 0.10
STAGE_WEIGHTS = (0.40, 0.30, 0.20)
TP1_FRACTION = 0.30
SLIPPAGE = 0.001

VARIANTS = (
    "monthly_contrib",
    "monthly_equity",
    "quarterly_profit",
    "step10_quarterly",
    "step10_reserve_sgov",
)

SCENARIOS = {
    "full": "2011-01-01",
    "recent": "2022-01-01",
}

VARIANT_LABELS = {
    "monthly_contrib": "월급만 매월 기준금액에 반영 / 수익 미복리 / USD",
    "monthly_equity": "월급+손익을 매월 전액 재기준 / USD",
    "quarterly_profit": "월급 매월 반영 + 손익 분기 재기준 / USD",
    "step10_quarterly": "월급 USD Reserve + 10% 계단식 증액 + 분기 손익 재기준",
    "step10_reserve_sgov": "10% 계단식 증액 + 남은 Reserve만 SGOV",
}


def research_config():
    config = load_config("strategy.yaml")
    return replace(
        config,
        global_=replace(
            config.global_,
            capital_per_symbol=Decimal("20000"),
        ),
        portfolio=replace(
            config.portfolio,
            total_capital=Decimal("50000"),
            booster_max_weight=Decimal("0.40"),
        ),
        # SGOV is handled explicitly by the capital-management simulator below.
        # Turning it off here prevents source booster event generation from
        # silently receiving idle-cash income.
        idle_cash=replace(config.idle_cash, enabled=False),
    )


def load_frames(data_source: YFinanceDataSource, warmup_start: str, end: str):
    symbols = ("TQQQ", "SOXL", "SPY", "QQQ", "SOXX", "SMH", "SGOV", "KRW=X")
    frames = {}
    for symbol in symbols:
        frames[symbol] = data_source.daily(symbol, warmup_start, end)
    return frames


def generate_booster_events(config, frames, start: str, end: str):
    sector_data = {"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
    events: dict[pd.Timestamp, list[dict]] = defaultdict(list)
    source_metrics = {}
    for symbol in config.enabled_symbols:
        result = StrategyBacktestEngine(config).run(
            symbol,
            frames[symbol],
            frames["SPY"],
            frames["QQQ"],
            start=start,
            end=end,
            slippage=SLIPPAGE,
            sector_data=sector_data if symbol == "SOXL" else None,
            idle_cash_data=None,
        )
        source_metrics[symbol] = result.metrics
        for trade in result.trades:
            event = dict(trade)
            event["symbol"] = symbol
            events[pd.Timestamp(event["date"])].append(event)
    return events, source_metrics


def common_index(config, frames, start: str, end: str) -> pd.DatetimeIndex:
    required = {
        *config.enabled_symbols,
        *config.portfolio.core_underlyings.values(),
    }
    index: pd.DatetimeIndex | None = None
    for symbol in sorted(required):
        frame_index = frames[symbol].index
        index = frame_index if index is None else index.intersection(frame_index)
    if index is None:
        raise ValueError("공통 거래일이 없습니다")
    index = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    if len(index) < 2:
        raise ValueError("연구 기간이 너무 짧습니다")
    return index


def first_sessions_by_month(index: pd.DatetimeIndex) -> set[pd.Timestamp]:
    values = pd.Series(index=index, data=index)
    return set(values.groupby(index.to_period("M")).first().tolist())


def contribution_usd(fx_frame: pd.DataFrame, timestamp: pd.Timestamp) -> float:
    previous = fx_frame.loc[fx_frame.index < timestamp, "close"].dropna()
    if previous.empty:
        raise ValueError(f"{timestamp.date()} 이전 USD/KRW 환율이 없습니다")
    usdkrw = float(previous.iloc[-1])
    if usdkrw <= 0:
        raise ValueError(f"비정상 USD/KRW 환율: {usdkrw}")
    return MONTHLY_CONTRIBUTION_KRW / usdkrw


def xirr(flows: list[tuple[date, float]]) -> float | None:
    if not flows or not any(amount < 0 for _, amount in flows) or not any(
        amount > 0 for _, amount in flows
    ):
        return None
    origin = flows[0][0]

    def npv(rate: float) -> float:
        base = 1.0 + rate
        if base <= 0:
            return math.inf
        return sum(
            amount / (base ** ((when - origin).days / 365.2425))
            for when, amount in flows
        )

    low = -0.9999
    high = 1.0
    low_value = npv(low)
    high_value = npv(high)
    while low_value * high_value > 0 and high < 1_000_000:
        high *= 2
        high_value = npv(high)
    if low_value * high_value > 0:
        return None
    for _ in range(200):
        mid = (low + high) / 2
        mid_value = npv(mid)
        if abs(mid_value) < 1e-8:
            return mid
        if low_value * mid_value <= 0:
            high = mid
        else:
            low = mid
            low_value = mid_value
    return (low + high) / 2


def _active_equity(
    cash: float,
    quantities: dict[str, dict[str, int]],
    prices: dict[str, float],
) -> float:
    return cash + sum(
        quantities[component][symbol] * prices[symbol]
        for component in quantities
        for symbol in prices
    )


def _sell_sgov_for_net(
    target_net: float,
    reserve_sgov: float,
    sell_fee: float,
) -> tuple[float, float, float]:
    if target_net <= 0 or reserve_sgov <= 0:
        return 0.0, reserve_sgov, 0.0
    gross_needed = target_net / (1.0 - sell_fee)
    gross = min(reserve_sgov, gross_needed)
    fee = gross * sell_fee
    net = gross - fee
    return net, reserve_sgov - gross, fee


def simulate_variant(
    variant: str,
    config,
    frames,
    start: str,
    end: str,
):
    index = common_index(config, frames, start, end)
    booster_events, source_metrics = generate_booster_events(config, frames, start, end)
    month_ends = PortfolioBacktestEngine._month_end_sessions(index)
    first_sessions = first_sessions_by_month(index)
    trends = {
        symbol: PortfolioBacktestEngine._monthly_trend(
            frames[underlying], index, config.portfolio.trend_months
        )
        for symbol, underlying in config.portfolio.core_underlyings.items()
    }

    sgov_raw = frames["SGOV"]["close"].reindex(index)
    sgov_available = sgov_raw.notna()
    sgov_close = sgov_raw.ffill()
    sgov_returns = sgov_close.pct_change(fill_method=None).fillna(0.0)

    buy_fee = float(config.global_.buy_fee)
    sell_fee = float(config.global_.sell_fee)
    active_base = INITIAL_CAPITAL
    active_cash = INITIAL_CAPITAL
    reserve_cash = 0.0
    reserve_sgov = 0.0
    sgov_income = 0.0
    sgov_fees = 0.0
    step_transfers = 0

    quantities = {
        "core": {symbol: 0 for symbol in config.enabled_symbols},
        "booster": {symbol: 0 for symbol in config.enabled_symbols},
    }
    pending_core: dict[str, float] | None = None
    core_active_previous = {symbol: False for symbol in config.enabled_symbols}
    booster_cycle_caps: dict[tuple[str, str], float] = {}
    booster_buy_counts: dict[tuple[str, str], int] = defaultdict(int)

    trades: list[dict] = []
    equity_values: list[float] = []
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
        # Existing SGOV reserve receives adjusted-price total return first.
        if variant == "step10_reserve_sgov" and reserve_sgov > 0:
            daily_return = float(sgov_returns.loc[timestamp])
            daily_income = reserve_sgov * daily_return
            reserve_sgov += daily_income
            sgov_income += daily_income

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
        is_contribution_month = timestamp.to_period("M") != scenario_start_period
        contribution = 0.0
        if is_first_session and is_contribution_month:
            contribution = contribution_usd(frames["KRW=X"], timestamp)
            contributions.append(contribution)
            flows.append((timestamp.date(), -contribution))
            external_flow = contribution

            # Profit/loss re-basing happens before this month's new money arrives.
            if variant in {"quarterly_profit", "step10_quarterly", "step10_reserve_sgov"}:
                if timestamp.month in {1, 4, 7, 10}:
                    active_base = max(
                        0.0,
                        _active_equity(active_cash, quantities, opens),
                    )

            if variant == "monthly_contrib":
                active_cash += contribution
                active_base += contribution
            elif variant == "monthly_equity":
                active_cash += contribution
                active_base = max(
                    0.0,
                    _active_equity(active_cash, quantities, opens),
                )
            elif variant == "quarterly_profit":
                active_cash += contribution
                active_base += contribution
            else:
                reserve_cash += contribution

        # Geometric 10% capital steps: reserve cash is consumed first. Existing
        # booster cycles remain frozen because their caps are stored separately.
        if variant in {"step10_quarterly", "step10_reserve_sgov"}:
            while active_base > 0:
                step = active_base * STEP_FRACTION
                reserve_net = reserve_cash
                if variant == "step10_reserve_sgov":
                    reserve_net += reserve_sgov * (1.0 - sell_fee)
                else:
                    reserve_net += reserve_sgov
                if reserve_net + 1e-9 < step:
                    break

                from_cash = min(step, reserve_cash)
                reserve_cash -= from_cash
                transferred = from_cash
                remaining = step - transferred
                if remaining > 1e-9 and variant == "step10_reserve_sgov":
                    net, reserve_sgov, fee = _sell_sgov_for_net(
                        remaining,
                        reserve_sgov,
                        sell_fee,
                    )
                    transferred += net
                    sgov_fees += fee
                elif remaining > 1e-9:
                    take = min(remaining, reserve_sgov)
                    reserve_sgov -= take
                    transferred += take

                if transferred + 1e-6 < step:
                    # Numerical guard; return any partial cash transfer to reserve.
                    reserve_cash += transferred
                    break
                active_cash += step
                active_base += step
                step_transfers += 1

        # Only the residual reserve is parked. This deliberately avoids the
        # old all-idle-cash SGOV sweep and avoids buying SGOV just to sell it
        # immediately for a capital step.
        if (
            variant == "step10_reserve_sgov"
            and reserve_cash > 0
            and bool(sgov_available.loc[timestamp])
        ):
            fee = reserve_cash * buy_fee
            reserve_sgov += reserve_cash - fee
            sgov_fees += fee
            reserve_cash = 0.0

        if pending_core is not None:
            changes = {}
            for symbol, weight in pending_core.items():
                buy_price = opens[symbol] * (1.0 + SLIPPAGE)
                sell_price = opens[symbol] * (1.0 - SLIPPAGE)
                target_dollars = float(weight) * active_base
                target_qty = math.floor(target_dollars / (buy_price * (1.0 + buy_fee)))
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
                affordable = math.floor(active_cash / (buy_price * (1.0 + buy_fee)))
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
                    booster_cycle_caps[key] = active_base * BOOSTER_MAX_WEIGHT
                budget = booster_cycle_caps[key] * STAGE_WEIGHTS[buy_number]
                booster_buy_counts[key] += 1
                quantity = math.floor(budget / (price * (1.0 + buy_fee)))
                affordable = math.floor(active_cash / (price * (1.0 + buy_fee)))
                quantity = min(quantity, affordable)
                if quantity <= 0:
                    continue
                fee = quantity * price * buy_fee
                active_cash -= quantity * price + fee
                quantities["booster"][symbol] += quantity
            else:
                held = quantities["booster"][symbol]
                if held <= 0:
                    continue
                if purpose == "TP1":
                    quantity = int(
                        (Decimal(held) * Decimal(str(TP1_FRACTION))).to_integral_value(
                            rounding=ROUND_CEILING
                        )
                    )
                    quantity = max(1, min(held, quantity))
                else:
                    quantity = held
                fee = quantity * price * sell_fee
                active_cash += quantity * price - fee
                quantities["booster"][symbol] -= quantity
                if quantities["booster"][symbol] == 0:
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
                    "cycle_capital": round(booster_cycle_caps.get(key, 0.0), 2),
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
        active_equity_end = active_cash + liquidation
        reserve_value = reserve_cash + reserve_sgov
        total_equity = active_equity_end + reserve_value
        equity_values.append(total_equity)
        active_base_values.append(active_base)
        reserve_values.append(reserve_value)
        exposures.append(liquidation / total_equity if total_equity > 0 else 0.0)

        if previous_equity is None:
            daily_return = total_equity / INITIAL_CAPITAL - 1.0
        else:
            denominator = previous_equity + external_flow
            daily_return = total_equity / denominator - 1.0 if denominator > 0 else 0.0
        twr_level *= 1.0 + daily_return
        twr_values.append(twr_level)
        previous_equity = total_equity

    equity_curve = pd.Series(equity_values, index=index)
    twr_curve = pd.Series(twr_values, index=index)
    years = max((index[-1] - index[0]).days / 365.2425, 1.0 / 365.2425)
    twr_total = float(twr_curve.iloc[-1] - 1.0)
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

    metrics = {
        "initial_capital": round(INITIAL_CAPITAL, 2),
        "monthly_contribution_krw": round(MONTHLY_CONTRIBUTION_KRW, 0),
        "contribution_count": len(contributions),
        "total_contributions_usd": round(total_contributed, 2),
        "average_contribution_usd": round(sum(contributions) / len(contributions), 2)
        if contributions
        else 0.0,
        "minimum_contribution_usd": round(min(contributions), 2) if contributions else 0.0,
        "maximum_contribution_usd": round(max(contributions), 2) if contributions else 0.0,
        "total_paid_in_usd": round(total_paid_in, 2),
        "final_equity_usd": round(final_equity, 2),
        "investment_profit_usd": round(investment_profit, 2),
        "profit_on_paid_in_pct": round(investment_profit / total_paid_in * 100.0, 2),
        "twr_total_return_pct": round(twr_total * 100.0, 2),
        "twr_cagr_pct": round(twr_cagr * 100.0, 2),
        "twr_mdd_pct": round(maximum_drawdown(twr_curve) * 100.0, 2),
        "twr_sharpe": round(sharpe, 3),
        "twr_sortino": round(sortino, 3),
        "xirr_pct": round(money_weighted * 100.0, 2) if money_weighted is not None else None,
        "average_exposure_pct": round(sum(exposures) / len(exposures) * 100.0, 2),
        "final_active_base_usd": round(active_base_values[-1], 2),
        "final_reserve_usd": round(reserve_values[-1], 2),
        "capital_step_transfers": step_transfers,
        "sgov_reserve_income_usd": round(sgov_income, 2),
        "sgov_reserve_fees_usd": round(sgov_fees, 2),
        "trade_fills": len(trades),
        "core_fills": sum(trade["component"] == "core" for trade in trades),
        "booster_fills": sum(trade["component"] == "booster" for trade in trades),
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
    earliest = min(date.fromisoformat(value) for value in SCENARIOS.values())
    warmup_start = (earliest - timedelta(days=400)).isoformat()
    data_source = YFinanceDataSource("data/cache/capital-growth")
    frames = load_frames(data_source, warmup_start, end)

    payload = {
        "research": {
            "variant": variant,
            "label": VARIANT_LABELS[variant],
            "strategy": config.version,
            "config_version": config.config_version,
            "initial_capital_usd": INITIAL_CAPITAL,
            "monthly_contribution_krw": MONTHLY_CONTRIBUTION_KRW,
            "fx_rule": "first US session, prior available USDKRW close",
            "slippage": SLIPPAGE,
            "buy_fee": float(config.global_.buy_fee),
            "sell_fee": float(config.global_.sell_fee),
            "booster_cycle_cap_rule": "freeze active_base*40% at first fill",
            "sgov_rule": "only residual reserve for step10_reserve_sgov",
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
    Path(output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def summarize(path: str) -> None:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    research = payload["research"]
    print(f"- Variant: {research['variant']} — {research['label']}")
    print(f"- Strategy: {research['strategy']} / {research['config_version']}")
    print("- Capital: $50,000 initial + KRW 5,000,000 monthly")
    print("- FX: prior available USD/KRW close before each month's first US session")
    print("- Costs: buy 0.10% / sell 0.10% / slippage 0.10%")
    print("- Booster: V3.1 H40-S3 timing; each cycle freezes its first-fill capital base")
    print()
    print("| 구간 | 최종자산 | 총납입 | 투자이익 | TWR CAGR | MDD | Sharpe | XIRR | 평균노출 | Reserve | SGOV 순효과 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for scenario, result in payload["scenarios"].items():
        m = result["metrics"]
        sgov_net = m["sgov_reserve_income_usd"] - m["sgov_reserve_fees_usd"]
        print(
            f"| {scenario} | ${m['final_equity_usd']:,.0f} | ${m['total_paid_in_usd']:,.0f} | "
            f"${m['investment_profit_usd']:,.0f} | {m['twr_cagr_pct']:+.2f}% | "
            f"{m['twr_mdd_pct']:.2f}% | {m['twr_sharpe']:.3f} | "
            f"{m['xirr_pct']:+.2f}% | {m['average_exposure_pct']:.2f}% | "
            f"${m['final_reserve_usd']:,.0f} | ${sgov_net:,.0f} |"
        )
        print(
            f"  - {scenario}: contributions={m['contribution_count']}, "
            f"avg=${m['average_contribution_usd']:,.2f}, "
            f"base=${m['final_active_base_usd']:,.2f}, steps={m['capital_step_transfers']}, "
            f"fills(core/booster)={m['core_fills']}/{m['booster_fills']}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=VARIANTS)
    parser.add_argument("--output")
    parser.add_argument("--summarize")
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize)
    else:
        run(args.variant, args.output)
