#!/usr/bin/env python3
"""Compare JDSS FINAL with an idle-cash yield overlay against SPY and QQQ."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from backtest_v2_gap_grid import BENCHMARKS, SYMBOLS, _candidate, _run
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent
INITIAL_PER_SYMBOL = 10_000.0


def _mdd_pct(equity: pd.Series) -> float:
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min() * 100.0)


def _cagr_pct(equity: pd.Series) -> float:
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return 0.0
    return float(((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0) * 100.0)


def _summary(equity: pd.Series) -> dict[str, float]:
    return {
        "start_equity": float(equity.iloc[0]),
        "end_equity": float(equity.iloc[-1]),
        "cumulative_return_pct": float((equity.iloc[-1] / equity.iloc[0] - 1.0) * 100.0),
        "cagr_pct": _cagr_pct(equity),
        "mdd_pct": _mdd_pct(equity),
    }


def _calendar_returns(equity: pd.Series) -> dict[str, float]:
    output: dict[str, float] = {}
    for year, values in equity.groupby(equity.index.year):
        if len(values) < 2:
            continue
        output[str(year)] = float((values.iloc[-1] / values.iloc[0] - 1.0) * 100.0)
    return output


def _cash_returns(index: pd.DatetimeIndex, irx: pd.DataFrame, sgov: pd.DataFrame) -> pd.Series:
    irx_yield = irx["close"].reindex(index).ffill()
    irx_daily = (irx_yield / 100.0) / 252.0

    sgov_close = sgov["close"].reindex(index).ffill()
    sgov_daily = sgov_close.pct_change().fillna(0.0)
    sgov_start = sgov["close"].dropna().index.min()

    returns = irx_daily.fillna(0.0)
    returns.loc[returns.index >= sgov_start] = sgov_daily.loc[returns.index >= sgov_start]
    return returns


def _overlay_symbol(result, price_frame, cash_returns, sell_fee: float) -> pd.Series:
    trades_by_date: dict[pd.Timestamp, list[dict]] = {}
    for trade in result.trades:
        date = pd.Timestamp(trade["date"])
        trades_by_date.setdefault(date, []).append(trade)

    cash = INITIAL_PER_SYMBOL
    quantity = 0
    values: list[float] = []
    dates = result.equity_curve.index
    for i, date in enumerate(dates):
        if i > 0:
            cash *= 1.0 + float(cash_returns.get(date, 0.0))

        for trade in trades_by_date.get(date, []):
            price = float(trade["price"])
            qty = int(trade["quantity"])
            fee = float(trade.get("fee", 0.0))
            if trade["side"] == "BUY":
                cash -= price * qty + fee
                quantity += qty
            else:
                cash += price * qty - fee
                quantity -= qty

        close = float(price_frame.loc[date, "close"])
        values.append(cash + quantity * close * (1.0 - sell_fee))
    return pd.Series(values, index=dates, name=result.symbol)


def _buy_hold(frame: pd.DataFrame, start: str, end: str, initial: float) -> pd.Series:
    values = frame.loc[start:end, "close"].dropna()
    return values / values.iloc[0] * initial


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "v2_cash_overlay.json",
    )
    args = parser.parse_args()

    base = load_config(ROOT / "strategy.yaml")
    config = _candidate(base, (0.02, 0.05, 0.07))
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup_start = (
        datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)
    ).isoformat()
    symbols = (*SYMBOLS, *BENCHMARKS, "^IRX", "SGOV")
    frames = {
        symbol: source.daily(symbol, warmup_start, args.end, refresh=True)
        for symbol in symbols
    }

    results = _run(config, frames, "2011-01-01", args.end)
    common_index = results["TQQQ"].equity_curve.index.intersection(
        results["SOXL"].equity_curve.index
    )
    cash_returns = _cash_returns(common_index, frames["^IRX"], frames["SGOV"])

    overlays = {
        symbol: _overlay_symbol(
            result,
            frames[symbol],
            cash_returns,
            float(config.global_.sell_fee),
        ).reindex(common_index)
        for symbol, result in results.items()
    }
    jdss_plain = sum(result.equity_curve.reindex(common_index) for result in results.values())
    jdss_cash = sum(overlays.values())
    spy = _buy_hold(frames["SPY"], "2011-01-01", args.end, 20_000.0).reindex(common_index).ffill()
    qqq = _buy_hold(frames["QQQ"], "2011-01-01", args.end, 20_000.0).reindex(common_index).ffill()

    series = {
        "JDSS_FINAL": jdss_plain,
        "JDSS_FINAL_CASH_YIELD": jdss_cash,
        "SPY_BUY_HOLD": spy,
        "QQQ_BUY_HOLD": qqq,
    }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "cash_method": "^IRX annualized yield / 252 before SGOV inception; SGOV adjusted close daily return thereafter",
        "metrics": {name: _summary(values) for name, values in series.items()},
        "annual_returns": {name: _calendar_returns(values) for name, values in series.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# JDSS FINAL Cash-Yield Overlay",
        "",
        "| Strategy | End Equity | Cum Return | CAGR | MDD |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, metrics in report["metrics"].items():
        lines.append(
            f"| {name} | ${metrics['end_equity']:,.0f} | "
            f"{metrics['cumulative_return_pct']:+.2f}% | "
            f"{metrics['cagr_pct']:+.2f}% | {metrics['mdd_pct']:.2f}% |"
        )
    lines.extend(["", "## Calendar-year returns", ""])
    years = sorted({year for values in report["annual_returns"].values() for year in values})
    lines.extend(
        [
            "| Year | JDSS | JDSS + Cash | SPY | QQQ |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    annual = report["annual_returns"]
    for year in years:
        lines.append(
            f"| {year} | {annual['JDSS_FINAL'].get(year, 0):+.2f}% | "
            f"{annual['JDSS_FINAL_CASH_YIELD'].get(year, 0):+.2f}% | "
            f"{annual['SPY_BUY_HOLD'].get(year, 0):+.2f}% | "
            f"{annual['QQQ_BUY_HOLD'].get(year, 0):+.2f}% |"
        )
    args.output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
