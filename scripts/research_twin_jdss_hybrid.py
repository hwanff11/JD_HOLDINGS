"""Research-only unified account combining twin-engine core and JDSS booster."""

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

from research_simple_strategies import ROOT, SYMBOLS, _combined, _idle_return
from research_twin_engine import _metrics, _month_end_sessions, _simulate as simulate_twin
from research_twin_engine_robustness import _trend
from jd_holdings.backtest.engine import BacktestEngine, BacktestResult
from jd_holdings.config import load_config
from jd_holdings.core.indicators import calculate_indicators
from jd_holdings.infrastructure.market_data import YFinanceDataSource

UNDERLYING = {"TQQQ": "QQQ", "SOXL": "SOXX"}
SEGMENTS = {
    "development_2011_2018": ("2011-01-01", "2018-12-31"),
    "validation_2019_2022": ("2019-01-01", "2022-12-31"),
    "test_2023_present": ("2023-01-01", None),
    "full_history": ("2011-01-01", None),
}


def _baseline_results(
    frames: dict[str, pd.DataFrame],
    raw: dict[str, pd.DataFrame],
    config: Any,
    *,
    start: str,
    end: str,
    slippage: float,
) -> dict[str, BacktestResult]:
    return {
        symbol: BacktestEngine(config).run(
            symbol,
            frames[symbol],
            frames["SPY"],
            frames["QQQ"],
            start=start,
            end=end,
            slippage=slippage,
            indicators_precomputed=True,
            sector_data={"SOXX": frames["SOXX"], "SMH": frames["SMH"]}
            if symbol == "SOXL"
            else None,
            idle_cash_data=raw[config.idle_cash.symbol],
        )
        for symbol in SYMBOLS
    }


def _trade_component_targets(
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
        differences[(component, symbol)] = (
            target_qty - quantities[component][symbol],
            buy_price,
            sell_price,
        )

    for (component, symbol), (difference, _, sell_price) in differences.items():
        if difference >= 0:
            continue
        quantity = -difference
        fee = quantity * sell_price * sell_fee
        cash += quantity * sell_price - fee
        quantities[component][symbol] -= quantity
        trades.append({
            "date": str(timestamp.date()), "component": component, "symbol": symbol,
            "side": "SELL", "quantity": quantity, "price": round(sell_price, 6),
            "fee": round(fee, 6), "target_weight": targets[(component, symbol)],
        })

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
        trades.append({
            "date": str(timestamp.date()), "component": component, "symbol": symbol,
            "side": "BUY", "quantity": quantity, "price": round(buy_price, 6),
            "fee": round(fee, 6), "target_weight": targets[(component, symbol)],
        })
    return cash


def _simulate_hybrid(
    raw: dict[str, pd.DataFrame],
    frames: dict[str, pd.DataFrame],
    config: Any,
    baseline: dict[str, BacktestResult],
    *,
    start: str,
    end: str,
    booster_cap: float,
    slippage: float,
) -> dict[str, Any]:
    index = raw["TQQQ"].index
    for symbol in ("SOXL", "QQQ", "SOXX", "SPY"):
        index = index.intersection(raw[symbol].index)
    index = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    month_ends = _month_end_sessions(index)
    signals = {
        symbol: _trend(raw[UNDERLYING[symbol]], raw[symbol].index.intersection(raw[UNDERLYING[symbol]].index), 10).reindex(index).fillna(False)
        for symbol in SYMBOLS
    }
    baseline_events: dict[pd.Timestamp, list[dict[str, Any]]] = defaultdict(list)
    for symbol, result in baseline.items():
        for trade in result.trades:
            item = dict(trade)
            item["symbol"] = symbol
            baseline_events[pd.Timestamp(item["date"])].append(item)

    initial = float(config.global_.capital_per_symbol) * len(SYMBOLS)
    cash = initial
    quantities = {
        "core": {symbol: 0 for symbol in SYMBOLS},
        "booster": {symbol: 0 for symbol in SYMBOLS},
    }
    reference_qty = {symbol: 0 for symbol in SYMBOLS}
    core_active = {symbol: False for symbol in SYMBOLS}
    pending_core: dict[str, float] | None = None
    trades: list[dict[str, Any]] = []
    equity_values: list[float] = []
    exposure_values: list[float] = []
    idle_income = 0.0
    idle_returns = _idle_return(raw[config.idle_cash.symbol], index)
    buy_fee = float(config.global_.buy_fee)
    sell_fee = float(config.global_.sell_fee)

    for timestamp in index:
        opens = {symbol: float(raw[symbol].loc[timestamp, "open"]) for symbol in SYMBOLS}
        closes = {symbol: float(raw[symbol].loc[timestamp, "close"]) for symbol in SYMBOLS}
        income = max(0.0, cash - float(config.idle_cash.cash_buffer)) * float(idle_returns.loc[timestamp])
        cash += income
        idle_income += income
        open_equity = cash + sum(
            quantities[component][symbol] * opens[symbol]
            for component in quantities
            for symbol in SYMBOLS
        )

        recalculate = False
        core_targets = {
            ("core", symbol): 0.15 if core_active[symbol] else 0.0
            for symbol in SYMBOLS
        }
        if pending_core is not None:
            core_active = {symbol: pending_core[symbol] > 0 for symbol in SYMBOLS}
            core_targets = {("core", symbol): pending_core[symbol] for symbol in SYMBOLS}
            pending_core = None
            recalculate = True

        if timestamp in baseline_events:
            for event in baseline_events[timestamp]:
                symbol = event["symbol"]
                signed = int(event["quantity"]) if event["side"] == "BUY" else -int(event["quantity"])
                reference_qty[symbol] = max(0, reference_qty[symbol] + signed)
            recalculate = True

        if recalculate:
            booster_targets: dict[tuple[str, str], float] = {}
            for symbol in SYMBOLS:
                utilization = min(
                    1.0,
                    reference_qty[symbol] * opens[symbol]
                    / float(config.global_.capital_per_symbol),
                )
                effective_cap = booster_cap if core_active[symbol] else min(0.05, booster_cap)
                booster_targets[("booster", symbol)] = effective_cap * utilization
            targets = {**core_targets, **booster_targets}
            cash = _trade_component_targets(
                targets, quantities, opens, timestamp=timestamp, cash=cash,
                equity=open_equity, buy_fee=buy_fee, sell_fee=sell_fee,
                slippage=slippage, trades=trades,
            )

        if timestamp in month_ends:
            pending_core = {
                symbol: 0.15 if bool(signals[symbol].loc[timestamp]) else 0.0
                for symbol in SYMBOLS
            }

        liquidation = sum(
            quantities[component][symbol] * closes[symbol] * (1 - sell_fee)
            for component in quantities
            for symbol in SYMBOLS
        )
        equity = cash + liquidation
        equity_values.append(equity)
        exposure_values.append(liquidation / equity if equity > 0 else 0.0)

    equity = pd.Series(equity_values, index=index)
    metrics = _metrics(
        equity, exposure_values, trades, idle_income, config.backtest.annualization_days
    )
    metrics["component_fills"] = {
        component: sum(1 for trade in trades if trade["component"] == component)
        for component in quantities
    }
    metrics["ending_component_values"] = {
        component: round(
            sum(quantities[component][symbol] * float(raw[symbol].loc[index[-1], "close"]) for symbol in SYMBOLS),
            2,
        )
        for component in quantities
    }
    return metrics


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 쌍발 코어 + JDSS 부스터 결합 연구", "",
        "| 후보 | 구간 | 누적수익 | CAGR | MDD | Sharpe | 평균노출 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for name, candidate in report["candidates"].items():
        for segment, metrics in candidate.items():
            lines.append(
                f"| {name} | {segment} | {metrics['total_return_pct']:+.2f}% | "
                f"{metrics['cagr_pct']:+.2f}% | {metrics['mdd_pct']:.2f}% | "
                f"{metrics['sharpe']:.3f} | {metrics['average_exposure_pct']:.2f}% |"
            )
    lines.extend([
        "",
        "- HYBRID_05: 상승추세 종목 최대 +5% JDSS 부스터",
        "- HYBRID_10: 상승추세 종목 최대 +10% JDSS 부스터",
        "- HYBRID_15: 상승추세 종목 최대 +15% JDSS 부스터",
        "- 월간 추세가 꺼진 종목은 부스터 최대 5%",
        "",
        "> 단일 $20,000 계좌와 공유 현금으로 계산한 연구 전용 결과입니다.",
        "> 운영 코드·설정·Oracle·실주문을 변경하지 않습니다.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-08-10")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "twin_jdss_hybrid.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "reports" / "twin_jdss_hybrid.md")
    args = parser.parse_args()
    config = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=800)).isoformat()
    symbols = ("TQQQ", "SOXL", "SPY", "QQQ", "SOXX", "SMH", config.idle_cash.symbol)
    raw = {symbol: source.daily(symbol, warmup, args.end) for symbol in symbols}
    frames = {symbol: calculate_indicators(frame, config) for symbol, frame in raw.items()}

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "end_date": args.end,
        "slippage": args.slippage,
        "candidates": {"JDSS_BASELINE": {}, "TWIN_15": {}, "HYBRID_05": {}, "HYBRID_10": {}, "HYBRID_15": {}},
    }
    for segment, (start, configured_end) in SEGMENTS.items():
        end = configured_end or args.end
        baseline = _baseline_results(frames, raw, config, start=start, end=end, slippage=args.slippage)
        report["candidates"]["JDSS_BASELINE"][segment] = _combined(baseline, config)
        report["candidates"]["TWIN_15"][segment] = simulate_twin(
            "V_TWIN_ENGINE_15", raw, config, start=start, end=end, slippage=args.slippage
        )["metrics"]
        for cap, name in ((0.05, "HYBRID_05"), (0.10, "HYBRID_10"), (0.15, "HYBRID_15")):
            report["candidates"][name][segment] = _simulate_hybrid(
                raw, frames, config, baseline, start=start, end=end,
                booster_cap=cap, slippage=args.slippage,
            )

    markdown = _markdown(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
