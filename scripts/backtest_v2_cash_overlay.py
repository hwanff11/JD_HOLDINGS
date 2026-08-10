#!/usr/bin/env python3
"""Compare JDSS FINAL plus SGOV-era idle-cash yield against SPY and QQQ."""

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
    return float((equity / equity.cummax() - 1).min() * 100)


def _cagr_pct(equity: pd.Series) -> float:
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return 0.0
    return float(((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100)


def _summary(equity: pd.Series) -> dict[str, float]:
    return {
        "start_equity": float(equity.iloc[0]),
        "end_equity": float(equity.iloc[-1]),
        "cumulative_return_pct": float(
            (equity.iloc[-1] / equity.iloc[0] - 1) * 100
        ),
        "cagr_pct": _cagr_pct(equity),
        "mdd_pct": _mdd_pct(equity),
    }


def _calendar_returns(equity: pd.Series) -> dict[str, float]:
    output = {}
    for year, values in equity.groupby(equity.index.year):
        if len(values) >= 2:
            output[str(year)] = float((values.iloc[-1] / values.iloc[0] - 1) * 100)
    return output


def _sgov_returns(
    index: pd.DatetimeIndex,
    sgov: pd.DataFrame,
) -> tuple[pd.Series, pd.Timestamp]:
    raw = sgov["close"].dropna()
    start = raw.index.min()
    close = raw.reindex(index).ffill()
    returns = close.pct_change().fillna(0.0)
    returns.loc[returns.index < start] = 0.0
    return returns, start


def _cash_balance_series(result, index: pd.DatetimeIndex) -> pd.Series:
    trade_cash = {
        pd.Timestamp(trade["date"]): float(trade["cash_after"])
        for trade in result.trades
    }
    cash = INITIAL_PER_SYMBOL
    values = []
    for trade_date in index:
        if trade_date in trade_cash:
            cash = trade_cash[trade_date]
        values.append(max(cash, 0.0))
    return pd.Series(values, index=index)


def _overlay(
    base_equity: pd.Series,
    idle_cash: pd.Series,
    cash_returns: pd.Series,
) -> pd.Series:
    interest = 0.0
    values = []
    for index, trade_date in enumerate(base_equity.index):
        if index > 0:
            interest += (
                float(idle_cash.loc[trade_date]) + interest
            ) * float(cash_returns.get(trade_date, 0.0))
        values.append(float(base_equity.loc[trade_date]) + interest)
    return pd.Series(values, index=base_equity.index)


def _buy_hold(
    frame: pd.DataFrame,
    start: str,
    end: str,
    initial: float,
) -> pd.Series:
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
    warmup = (
        datetime.fromisoformat("2011-01-01").date() - timedelta(days=400)
    ).isoformat()
    frames = {
        symbol: source.daily(symbol, warmup, args.end, refresh=True)
        for symbol in (*SYMBOLS, *BENCHMARKS, "SGOV")
    }
    results = _run(config, frames, "2011-01-01", args.end)
    common_index = results["TQQQ"].equity_curve.index.intersection(
        results["SOXL"].equity_curve.index
    )
    cash_returns, sgov_start = _sgov_returns(common_index, frames["SGOV"])
    plain = {
        symbol: result.equity_curve.reindex(common_index)
        for symbol, result in results.items()
    }
    cash = {
        symbol: _cash_balance_series(result, common_index)
        for symbol, result in results.items()
    }
    cash_overlay = {
        symbol: _overlay(plain[symbol], cash[symbol], cash_returns)
        for symbol in SYMBOLS
    }
    series = {
        "JDSS_FINAL": sum(plain.values()),
        "JDSS_FINAL_SGOV_CASH": sum(cash_overlay.values()),
        "SPY_BUY_HOLD": _buy_hold(
            frames["SPY"], "2011-01-01", args.end, 20_000
        ).reindex(common_index).ffill(),
        "QQQ_BUY_HOLD": _buy_hold(
            frames["QQQ"], "2011-01-01", args.end, 20_000
        ).reindex(common_index).ffill(),
    }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "cash_method": (
            "0% idle-cash return before SGOV inception; SGOV adjusted-close daily "
            "return on actual idle cash thereafter"
        ),
        "sgov_start": sgov_start.isoformat(),
        "metrics": {name: _summary(values) for name, values in series.items()},
        "annual_returns": {
            name: _calendar_returns(values) for name, values in series.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# JDSS FINAL SGOV Cash Overlay",
        "",
        f"SGOV overlay starts: {sgov_start.date()}",
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
    lines.extend(
        [
            "",
            "## Calendar-year returns",
            "",
            "| Year | JDSS | JDSS + SGOV cash | SPY | QQQ |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    annual = report["annual_returns"]
    years = sorted({year for returns in annual.values() for year in returns})
    for year in years:
        lines.append(
            f"| {year} | {annual['JDSS_FINAL'].get(year, 0):+.2f}% | "
            f"{annual['JDSS_FINAL_SGOV_CASH'].get(year, 0):+.2f}% | "
            f"{annual['SPY_BUY_HOLD'].get(year, 0):+.2f}% | "
            f"{annual['QQQ_BUY_HOLD'].get(year, 0):+.2f}% |"
        )
    args.output.with_suffix(".md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
