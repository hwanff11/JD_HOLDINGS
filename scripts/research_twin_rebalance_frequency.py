"""Research-only cadence search for the twin core and JDSS booster."""

# ruff: noqa: E501, I001

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

from research_simple_strategies import ROOT, SYMBOLS, _combined, _idle_return
from research_twin_engine import _metrics, _month_end_sessions
from research_twin_engine_robustness import STRESS, WINDOWS, _slice_metrics, _trend
from research_twin_jdss_hybrid import UNDERLYING, _baseline_results
from jd_holdings.backtest.engine import BacktestResult
from jd_holdings.config import load_config
from jd_holdings.core.indicators import calculate_indicators
from jd_holdings.infrastructure.market_data import YFinanceDataSource


CADENCES = {
    "MONTHLY": {"kind": "monthly"},
    "SEMIMONTHLY": {"kind": "semimonthly"},
    "BIWEEKLY_A": {"kind": "biweekly", "phase": 0},
    "BIWEEKLY_B": {"kind": "biweekly", "phase": 1},
    "WEEKLY": {"kind": "weekly"},
    "BIWEEKLY_BAND_A": {"kind": "band", "phase": 0, "lower": 0.12, "upper": 0.18},
    "BIWEEKLY_BAND_B": {"kind": "band", "phase": 1, "lower": 0.12, "upper": 0.18},
    "SEMIMONTHLY_BAND": {"kind": "semimonthly_band", "lower": 0.12, "upper": 0.18},
}


def _week_end_sessions(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    values = pd.Series(index=index, data=index)
    return values.groupby(index.to_period("W-FRI")).last().tolist()


def _cadence_sessions(index: pd.DatetimeIndex, spec: dict[str, Any]) -> set[pd.Timestamp]:
    if spec["kind"] == "monthly":
        return set()
    if spec["kind"] in {"semimonthly", "semimonthly_band"}:
        midmonth = []
        for _, sessions in pd.Series(index=index, data=index).groupby(index.to_period("M")):
            first_half = sessions[sessions.dt.day <= 15]
            if not first_half.empty:
                midmonth.append(first_half.iloc[-1])
        return set(midmonth)
    week_ends = _week_end_sessions(index)
    if spec["kind"] == "weekly":
        return set(week_ends)
    phase = int(spec["phase"])
    return {timestamp for position, timestamp in enumerate(week_ends) if position % 2 == phase}


def _trade_targets(
    targets: dict[tuple[str, str], float],
    quantities: dict[str, dict[str, int]],
    prices: dict[str, float],
    *,
    timestamp: pd.Timestamp,
    cash: float,
    equity: float,
    buy_fee: float,
    sell_fee: float,
    slippage: float,
    trades: list[dict[str, Any]],
) -> float:
    differences: dict[tuple[str, str], tuple[int, float, float]] = {}
    for (component, symbol), weight in targets.items():
        buy_price = prices[symbol] * (1 + slippage)
        sell_price = prices[symbol] * (1 - slippage)
        target_qty = math.floor(weight * equity / (buy_price * (1 + buy_fee)))
        differences[(component, symbol)] = (target_qty - quantities[component][symbol], buy_price, sell_price)
    for (component, symbol), (difference, _, sell_price) in differences.items():
        if difference >= 0:
            continue
        quantity = -difference
        fee = quantity * sell_price * sell_fee
        cash += quantity * sell_price - fee
        quantities[component][symbol] -= quantity
        trades.append({"date": str(timestamp.date()), "component": component, "symbol": symbol, "side": "SELL", "quantity": quantity, "price": round(sell_price, 6), "fee": round(fee, 6), "target_weight": targets[(component, symbol)]})
    for (component, symbol), (difference, buy_price, _) in differences.items():
        if difference <= 0:
            continue
        affordable = math.floor(cash / (buy_price * (1 + buy_fee)))
        quantity = min(difference, affordable)
        if quantity <= 0:
            continue
        fee = quantity * buy_price * buy_fee
        cash -= quantity * buy_price + fee
        quantities[component][symbol] += quantity
        trades.append({"date": str(timestamp.date()), "component": component, "symbol": symbol, "side": "BUY", "quantity": quantity, "price": round(buy_price, 6), "fee": round(fee, 6), "target_weight": targets[(component, symbol)]})
    return cash


def _simulate(
    raw: dict[str, pd.DataFrame],
    config: Any,
    baseline: dict[str, BacktestResult],
    *,
    start: str,
    end: str,
    cadence: str,
    booster_cap: float,
    slippage: float,
    execution_delay: int = 0,
) -> tuple[pd.Series, dict[str, Any]]:
    spec = CADENCES[cadence]
    index = raw["TQQQ"].index
    for symbol in ("SOXL", "QQQ", "SOXX", "SPY"):
        index = index.intersection(raw[symbol].index)
    index = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    month_ends = _month_end_sessions(index)
    cadence_dates = _cadence_sessions(index, spec)
    signals = {
        symbol: _trend(raw[UNDERLYING[symbol]], raw[symbol].index.intersection(raw[UNDERLYING[symbol]].index), 10).reindex(index).fillna(False)
        for symbol in SYMBOLS
    }
    baseline_events: dict[pd.Timestamp, list[dict[str, Any]]] = defaultdict(list)
    date_positions = {timestamp: position for position, timestamp in enumerate(index)}
    for symbol, result in baseline.items():
        for trade in result.trades:
            event = dict(trade)
            event["symbol"] = symbol
            event_date = pd.Timestamp(event["date"])
            if event_date not in date_positions:
                continue
            due = date_positions[event_date] + execution_delay
            if due < len(index):
                baseline_events[index[due]].append(event)

    initial = float(config.global_.capital_per_symbol) * len(SYMBOLS)
    cash = initial
    quantities = {"core": {symbol: 0 for symbol in SYMBOLS}, "booster": {symbol: 0 for symbol in SYMBOLS}}
    reference_qty = {symbol: 0 for symbol in SYMBOLS}
    active = {symbol: False for symbol in SYMBOLS}
    scheduled_core: dict[int, dict[str, float]] = {}
    trades: list[dict[str, Any]] = []
    equity_values: list[float] = []
    exposure_values: list[float] = []
    idle_income = 0.0
    idle_returns = _idle_return(raw[config.idle_cash.symbol], index)
    buy_fee = float(config.global_.buy_fee)
    sell_fee = float(config.global_.sell_fee)

    for position, timestamp in enumerate(index):
        opens = {symbol: float(raw[symbol].loc[timestamp, "open"]) for symbol in SYMBOLS}
        closes = {symbol: float(raw[symbol].loc[timestamp, "close"]) for symbol in SYMBOLS}
        income = max(0.0, cash - float(config.idle_cash.cash_buffer)) * float(idle_returns.loc[timestamp])
        cash += income
        idle_income += income
        open_equity = cash + sum(quantities[component][symbol] * opens[symbol] for component in quantities for symbol in SYMBOLS)

        regime_changed = False
        core_targets = scheduled_core.pop(position, None)
        if core_targets is not None:
            next_active = {symbol: core_targets[symbol] > 0 for symbol in SYMBOLS}
            regime_changed = next_active != active
            active = next_active
            cash = _trade_targets(
                {("core", symbol): core_targets[symbol] for symbol in SYMBOLS}, quantities, opens,
                timestamp=timestamp, cash=cash, equity=open_equity, buy_fee=buy_fee,
                sell_fee=sell_fee, slippage=slippage, trades=trades,
            )

        changed_symbols: set[str] = set()
        if timestamp in baseline_events:
            for event in baseline_events[timestamp]:
                symbol = event["symbol"]
                signed = int(event["quantity"]) if event["side"] == "BUY" else -int(event["quantity"])
                reference_qty[symbol] = max(0, reference_qty[symbol] + signed)
                changed_symbols.add(symbol)
        if booster_cap > 0 and (regime_changed or changed_symbols):
            open_equity = cash + sum(quantities[component][symbol] * opens[symbol] for component in quantities for symbol in SYMBOLS)
            target_symbols = SYMBOLS if regime_changed else tuple(changed_symbols)
            targets = {}
            for symbol in target_symbols:
                utilization = min(1.0, reference_qty[symbol] * opens[symbol] / float(config.global_.capital_per_symbol))
                effective_cap = booster_cap if active[symbol] else min(0.05, booster_cap)
                targets[("booster", symbol)] = effective_cap * utilization
            cash = _trade_targets(
                targets, quantities, opens, timestamp=timestamp, cash=cash, equity=open_equity,
                buy_fee=buy_fee, sell_fee=sell_fee, slippage=slippage, trades=trades,
            )

        close_equity = cash + sum(quantities[component][symbol] * closes[symbol] for component in quantities for symbol in SYMBOLS)
        if timestamp in month_ends:
            desired = {symbol: 0.15 if bool(signals[symbol].loc[timestamp]) else 0.0 for symbol in SYMBOLS}
            due = position + 1 + execution_delay
            if due < len(index):
                scheduled_core[due] = desired
        elif timestamp in cadence_dates:
            desired = {symbol: 0.15 if active[symbol] else 0.0 for symbol in SYMBOLS}
            if spec["kind"] in {"band", "semimonthly_band"}:
                weights = {symbol: quantities["core"][symbol] * closes[symbol] / close_equity for symbol in SYMBOLS}
                if not any(active[symbol] and (weights[symbol] < spec["lower"] or weights[symbol] > spec["upper"]) for symbol in SYMBOLS):
                    desired = None
            due = position + 1 + execution_delay
            if desired is not None and due < len(index):
                scheduled_core[due] = desired

        liquidation = sum(quantities[component][symbol] * closes[symbol] * (1 - sell_fee) for component in quantities for symbol in SYMBOLS)
        equity = cash + liquidation
        equity_values.append(equity)
        exposure_values.append(liquidation / equity if equity > 0 else 0.0)

    equity = pd.Series(equity_values, index=index)
    metrics = _metrics(equity, exposure_values, trades, idle_income, config.backtest.annualization_days)
    metrics["component_fills"] = {component: sum(trade["component"] == component for trade in trades) for component in quantities}
    return equity, metrics


def _candidate_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: metrics[key] for key in ("total_return_pct", "cagr_pct", "mdd_pct", "sharpe", "trade_fills", "average_exposure_pct", "annual_returns_pct", "component_fills")}


def _paired_bootstrap(
    candidate: pd.Series,
    baseline: pd.Series,
    *,
    iterations: int = 500,
    block: int = 20,
) -> dict[str, float]:
    aligned = pd.concat([candidate.pct_change(), baseline.pct_change()], axis=1).dropna()
    candidate_returns = aligned.iloc[:, 0].to_numpy()
    baseline_returns = aligned.iloc[:, 1].to_numpy()
    sample_size = len(aligned)
    rng = np.random.default_rng(20260811)
    return_wins = 0
    mdd_wins = 0
    for _ in range(iterations):
        sampled: list[int] = []
        while len(sampled) < sample_size:
            start = int(rng.integers(0, max(1, sample_size - block + 1)))
            sampled.extend(range(start, min(start + block, sample_size)))
        positions = np.asarray(sampled[:sample_size])
        candidate_path = np.cumprod(1 + candidate_returns[positions])
        baseline_path = np.cumprod(1 + baseline_returns[positions])
        candidate_mdd = float(np.min(candidate_path / np.maximum.accumulate(candidate_path) - 1))
        baseline_mdd = float(np.min(baseline_path / np.maximum.accumulate(baseline_path) - 1))
        return_wins += int(candidate_path[-1] > baseline_path[-1])
        mdd_wins += int(candidate_mdd > baseline_mdd)
    return {
        "iterations": iterations,
        "block_sessions": block,
        "return_win_pct": round(return_wins / iterations * 100, 2),
        "mdd_win_pct": round(mdd_wins / iterations * 100, 2),
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 쌍발엔진 조정 주기·결합 탐색", "",
        "| 후보 | 전체 누적 | CAGR | MDD | Sharpe | 체결 | 5년 흑자 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, data in report["candidates"].items():
        m = data["full"]
        lines.append(f"| {name} | {m['total_return_pct']:+.2f}% | {m['cagr_pct']:+.2f}% | {m['mdd_pct']:.2f}% | {m['sharpe']:.3f} | {m['trade_fills']} | {data['positive_rolling_5y']}/{len(WINDOWS)} |")
    lines.extend([
        "", f"- MDD -30% 이내 최고 CAGR 후보: {report['selection']['best_under_30pct_mdd']}",
        f"- 격주 위상 A/B 모두 월간보다 높은 후보: {', '.join(report['selection']['phase_robust']) or '없음'}",
        f"- 최종 승격 후보: {report['selection']['promotion_candidate']}",
        f"- 승격 판정: {'통과' if report['selection']['promote'] else '보류'}",
        f"- 5년 순환 수익 우위: {report['selection']['rolling_5y_wins']}/{len(WINDOWS)}",
        f"- 부트스트랩 수익/MDD 우위: {report['bootstrap']['return_win_pct']:.2f}% / {report['bootstrap']['mdd_win_pct']:.2f}%",
        "", "> 월말 추세 신호는 유지하고 비중 복원 주기만 변경했습니다.",
        "> 연구 전용이며 운영 코드·설정·Oracle·실주문을 변경하지 않습니다.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-08-10")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "twin_rebalance_frequency.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "reports" / "twin_rebalance_frequency.md")
    args = parser.parse_args()
    config = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=800)).isoformat()
    symbols = ("TQQQ", "SOXL", "SPY", "QQQ", "SOXX", "SMH", config.idle_cash.symbol)
    raw = {symbol: source.daily(symbol, warmup, args.end) for symbol in symbols}
    frames = {symbol: calculate_indicators(frame, config) for symbol, frame in raw.items()}
    baseline = _baseline_results(frames, raw, config, start="2011-01-01", end=args.end, slippage=args.slippage)

    definitions = {name: (name, 0.0) for name in CADENCES}
    definitions.update({
        "MONTHLY_H05": ("MONTHLY", 0.05), "MONTHLY_H10": ("MONTHLY", 0.10),
        "SEMIMONTHLY_H05": ("SEMIMONTHLY", 0.05),
        "SEMIMONTHLY_BAND_H05": ("SEMIMONTHLY_BAND", 0.05),
        "BIWEEKLY_A_H05": ("BIWEEKLY_A", 0.05), "BIWEEKLY_B_H05": ("BIWEEKLY_B", 0.05),
        "BIWEEKLY_A_H10": ("BIWEEKLY_A", 0.10), "BIWEEKLY_B_H10": ("BIWEEKLY_B", 0.10),
        "BAND_A_H05": ("BIWEEKLY_BAND_A", 0.05), "BAND_B_H05": ("BIWEEKLY_BAND_B", 0.05),
    })
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(), "end_date": args.end,
        "slippage": args.slippage, "jdss_baseline": _combined(baseline, config), "candidates": {},
    }
    equities: dict[str, pd.Series] = {}
    for name, (cadence, cap) in definitions.items():
        equity, metrics = _simulate(raw, config, baseline, start="2011-01-01", end=args.end, cadence=cadence, booster_cap=cap, slippage=args.slippage)
        equities[name] = equity
        rolling = {}
        for start_year, end_year in WINDOWS:
            section = equity[(equity.index >= pd.Timestamp(f"{start_year}-01-01")) & (equity.index <= pd.Timestamp(f"{end_year}-12-31"))]
            rolling[f"{start_year}_{end_year}"] = _slice_metrics(section, config.backtest.annualization_days)
        stress = {}
        for label, (start, end) in STRESS.items():
            section = equity[(equity.index >= pd.Timestamp(start)) & (equity.index <= pd.Timestamp(end))]
            stress[label] = _slice_metrics(section, config.backtest.annualization_days)
        recent = equity[equity.index >= pd.Timestamp("2023-01-01")]
        report["candidates"][name] = {
            "full": _candidate_summary(metrics),
            "recent": _slice_metrics(recent, config.backtest.annualization_days),
            "rolling_5y": rolling, "positive_rolling_5y": sum(m.get("total_return_pct", 0) > 0 for m in rolling.values()),
            "stress": stress,
        }

    eligible = {name: data for name, data in report["candidates"].items() if data["full"]["mdd_pct"] >= -30.0}
    best = max(eligible, key=lambda name: eligible[name]["full"]["cagr_pct"])
    phase_pairs = {
        "BIWEEKLY": (("BIWEEKLY_A", "BIWEEKLY_B"), "MONTHLY"),
        "BIWEEKLY_H05": (("BIWEEKLY_A_H05", "BIWEEKLY_B_H05"), "MONTHLY_H05"),
        "BIWEEKLY_H10": (("BIWEEKLY_A_H10", "BIWEEKLY_B_H10"), "MONTHLY_H10"),
        "BAND_H05": (("BAND_A_H05", "BAND_B_H05"), "MONTHLY_H05"),
    }
    phase_robust = [
        label for label, (pair, baseline_name) in phase_pairs.items()
        if all(
            report["candidates"][name]["full"]["total_return_pct"]
            > report["candidates"][baseline_name]["full"]["total_return_pct"]
            for name in pair
        )
    ]
    delay_results: dict[str, Any] = {}
    for delay in range(4):
        delay_results[str(delay)] = {}
        for label, cadence in (("MONTHLY_H05", "MONTHLY"), ("SEMIMONTHLY_BAND_H05", "SEMIMONTHLY_BAND")):
            _, metrics = _simulate(
                raw, config, baseline, start="2011-01-01", end=args.end,
                cadence=cadence, booster_cap=0.05, slippage=args.slippage,
                execution_delay=delay,
            )
            delay_results[str(delay)][label] = _candidate_summary(metrics)
    candidate_name = "SEMIMONTHLY_BAND_H05"
    baseline_name = "MONTHLY_H05"
    candidate = report["candidates"][candidate_name]
    baseline_candidate = report["candidates"][baseline_name]
    rolling_wins = sum(
        candidate["rolling_5y"][key]["total_return_pct"]
        > baseline_candidate["rolling_5y"][key]["total_return_pct"]
        for key in candidate["rolling_5y"]
    )
    bootstrap = _paired_bootstrap(equities[candidate_name], equities[baseline_name])
    criteria = {
        "full_return_above_monthly_h05": candidate["full"]["total_return_pct"] > baseline_candidate["full"]["total_return_pct"],
        "full_mdd_within_30pct": candidate["full"]["mdd_pct"] >= -30.0,
        "full_sharpe_above_monthly_h05": candidate["full"]["sharpe"] > baseline_candidate["full"]["sharpe"],
        "all_rolling_5y_positive": candidate["positive_rolling_5y"] == len(WINDOWS),
        "rolling_5y_wins_at_least_8": rolling_wins >= 8,
        "all_delays_return_above_monthly_h05": all(
            values[candidate_name]["total_return_pct"] > values[baseline_name]["total_return_pct"]
            for values in delay_results.values()
        ),
        "all_delays_mdd_within_32pct": all(values[candidate_name]["mdd_pct"] >= -32.0 for values in delay_results.values()),
        "bootstrap_return_win_at_least_60pct": bootstrap["return_win_pct"] >= 60.0,
        "bootstrap_mdd_win_at_least_50pct": bootstrap["mdd_win_pct"] >= 50.0,
    }
    report["delays"] = delay_results
    report["bootstrap"] = bootstrap
    report["selection"] = {
        "best_under_30pct_mdd": best, "phase_robust": phase_robust,
        "promotion_candidate": candidate_name, "rolling_5y_wins": rolling_wins,
        "criteria": criteria, "promote": all(criteria.values()),
    }
    markdown = _markdown(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
