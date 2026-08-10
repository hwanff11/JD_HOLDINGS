#!/usr/bin/env python3
"""Compare JDSS FINAL plus idle-cash yield against SPY and QQQ."""

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
        if len(values) >= 2:
            output[str(year)] = float((values.iloc[-1] / values.iloc[0] - 1.0) * 100.0)
    return output


def _cash_returns(index: pd.DatetimeIndex, irx: pd.DataFrame, sgov: pd.DataFrame) -> pd.Series:
    irx_yield = irx["close"].reindex(index).ffill()
    irx_daily = (irx_yield / 100.0) / 252.0

    sgov_close = sgov["close"].reindex(index).ffill()
    sgov_daily = sgov_close.pct_change().fillna(0.0)
    sgov_start = sgov["close"].dropna().index.min()

    returns = irx_daily.fillna(0.0)
    returns.loc[returns.index >= sgov_start] = sgov_daily.loc[
        returns.index >= sgov_start
    ]
    return returns.clip(lower=-0.01, upper=0.01)


def _cash_balance_series(result, index: pd.DatetimeIndex) -> pd.Series:
    trade_cash: dict[pd.Timestamp, float] = {}
    for trade in result.trades:
        trade_cash[pd.Timestamp(trade["date"])] = float(trade["cash_after"])

    values: list[float] = []
    cash = INITIAL_PER_SYMBOL
    for date in index:
        if date in trade_cash:
            cash = trade_cash[date]
        values.append(max(cash, 0.0))
    return pd.Series(values, index=index, name=f"{result.symbol}_cash")


def _overlay_from_idle_cash(
    base_equity: pd.Series,
    idle_cash: pd.Series,
    cash_returns: pd.Series,
) -> pd.Series:
    accrued_interest = 0.0
    values: list[float] = []
    for i, date in enumerate(base_equity.index):
        if i > 0:
            rate = float(cash_returns.get(date, 0.0))
            accrued_interest += (float(idle_cash.loc[date]) + accrued_interest) * rate
        values.append(float(base_equity.loc[date]) + accrued_interest)
    return pd.Series(values, index=base_equity.index)


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

    plain_by_symbol = {
        symbol: result.equity_curve.reindex(common_index)
        for symbol, result in results.items()
    }
    cash_by_symbol = {
        symbol: _cash_balance_series(result, common_index)
        for symbol, result in results.items()
    }
    overlay_by_symbol = {
        symbol: _overlay_from_idle_cash(
            plain_by_symbol[symbol],
            cash_by_symbol[symbol],
            cash_returns,
        )
        for symbol in SYMBOLS
    }

    jdss_plain = sum(plain_by_symbol.values())
    jdss_cash = sum(overlay_by_symbol.values())
    spy = _buy_hold(frames["SPY"], "2011-01-01", args.end, 20_000.0).reindex(
        common_index
    ).ffill()
    qqq = _buy_hold(frames["QQQ"], "2011-01-01", args.end, 20_000.0).reindex(
        common_index
    ).ffill()

    series = {
        "JDSS_FINAL": jdss_plain,
        "JDSS_FINAL_CASH_YIELD": jdss_cash,
        "SPY_BUY_HOLD": spy,
        "QQQ_BUY_HOLD": qqq,
    }
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "cash_method": (
            "Interest sidecar on actual backtest cash_after balances; ^IRX/252 before "
            "SGOV inception and SGOV adjusted-close daily return thereafter"
        ),
        "metrics": {name: _summary(values) for name, values in series.items()},
        "annual_returns": {
            name: _calendar_returns(values) for name, values in series.items()
        },
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

    args.output.with_suffix(".md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
