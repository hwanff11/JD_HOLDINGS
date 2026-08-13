#!/usr/bin/env python3
"""Attribute weak rolling windows of the RS6M monthly-entry / daily-exit candidate."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parents[1]
RS_SCRIPT = ROOT / "scripts" / "research_v321_relative_strength_booster.py"
MONTHLY_SCRIPT = ROOT / "scripts" / "research_v321_rs6m_monthly_lock.py"
ONEWAY_SCRIPT = ROOT / "scripts" / "research_v321_rs6m_oneway_exit.py"
START = "2011-01-03"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def month_returns(series: pd.Series) -> pd.Series:
    month_end = series.groupby(series.index.to_period("M")).last()
    return month_end.pct_change() * 100.0


def close_month_returns(frame: pd.DataFrame) -> pd.Series:
    close = frame["close"].astype(float)
    return close.groupby(close.index.to_period("M")).last().pct_change() * 100.0


def window_return(series: pd.Series, start: str, end: str) -> float:
    subset = series[(series.index >= pd.Timestamp(start)) & (series.index <= pd.Timestamp(end))]
    if len(subset) < 2:
        return 0.0
    return (float(subset.iloc[-1] / subset.iloc[0]) - 1.0) * 100.0


def window_drawdown(series: pd.Series, start: str, end: str) -> dict[str, object]:
    subset = series[(series.index >= pd.Timestamp(start)) & (series.index <= pd.Timestamp(end))]
    if subset.empty:
        return {}
    peak = subset.cummax()
    dd = subset / peak - 1.0
    trough_date = dd.idxmin()
    peak_before = subset.loc[:trough_date].idxmax()
    return {
        "mdd_pct": round(float(dd.min()) * 100.0, 2),
        "peak_date": peak_before.date().isoformat(),
        "trough_date": trough_date.date().isoformat(),
    }


def monthly_rows(
    base_curve: pd.Series,
    candidate_curve: pd.Series,
    qqq_curve: pd.Series,
    tqqq_frame: pd.DataFrame,
    soxl_frame: pd.DataFrame,
    soxx_frame: pd.DataFrame,
    qqq_features: pd.DataFrame,
    soxl_state: pd.Series,
    start: str,
    end: str,
) -> list[dict[str, object]]:
    base_r = month_returns(base_curve)
    cand_r = month_returns(candidate_curve)
    qqq_r = month_returns(qqq_curve)
    tqqq_r = close_month_returns(tqqq_frame)
    soxl_r = close_month_returns(soxl_frame)
    soxx_r = close_month_returns(soxx_frame)
    active_pct = soxl_state.groupby(soxl_state.index.to_period("M")).mean() * 100.0

    months = cand_r.index[
        (cand_r.index >= pd.Period(start[:7], freq="M"))
        & (cand_r.index <= pd.Period(end[:7], freq="M"))
    ]
    rows: list[dict[str, object]] = []
    for month in months:
        c = float(cand_r.get(month, 0.0))
        b = float(base_r.get(month, 0.0))
        q = float(qqq_r.get(month, 0.0))
        c_log = math.log1p(c / 100.0)
        b_log = math.log1p(b / 100.0)
        q_log = math.log1p(q / 100.0)

        month_dates = qqq_features.index[qqq_features.index.to_period("M") == month]
        if len(month_dates):
            last = month_dates[-1]
            q126 = float(qqq_features.loc[last, "ret126"]) * 100.0
            s126_raw = soxx_frame.loc[last].get("ret126")
            s126 = float(s126_raw) * 100.0 if pd.notna(s126_raw) else None
        else:
            q126 = None
            s126 = None

        rows.append(
            {
                "month": str(month),
                "candidate_pct": round(c, 2),
                "baseline_pct": round(b, 2),
                "qqq_pct": round(q, 2),
                "candidate_minus_baseline_pp": round(c - b, 2),
                "candidate_minus_qqq_pp": round(c - q, 2),
                "log_excess_vs_baseline_pp": round((c_log - b_log) * 100.0, 3),
                "log_excess_vs_qqq_pp": round((c_log - q_log) * 100.0, 3),
                "soxl_active_days_pct": round(float(active_pct.get(month, 0.0)), 2),
                "tqqq_month_pct": round(float(tqqq_r.get(month, 0.0)), 2),
                "soxl_month_pct": round(float(soxl_r.get(month, 0.0)), 2),
                "soxl_minus_tqqq_pp": round(
                    float(soxl_r.get(month, 0.0) - tqqq_r.get(month, 0.0)), 2
                ),
                "soxx_month_pct": round(float(soxx_r.get(month, 0.0)), 2),
                "month_end_qqq_ret126_pct": round(q126, 2) if q126 is not None else None,
                "month_end_soxx_ret126_pct": round(s126, 2) if s126 is not None else None,
            }
        )
    return rows


def summarize_rows(rows: list[dict[str, object]]) -> dict[str, object]:
    if not rows:
        return {}
    negative = sorted(rows, key=lambda row: float(row["log_excess_vs_baseline_pp"]))
    active = [row for row in rows if float(row["soxl_active_days_pct"]) > 0.0]
    inactive = [row for row in rows if float(row["soxl_active_days_pct"]) == 0.0]
    return {
        "total_log_excess_vs_baseline_pp": round(
            sum(float(row["log_excess_vs_baseline_pp"]) for row in rows), 3
        ),
        "total_log_excess_vs_qqq_pp": round(
            sum(float(row["log_excess_vs_qqq_pp"]) for row in rows), 3
        ),
        "soxl_active_months": len(active),
        "soxl_inactive_months": len(inactive),
        "active_month_log_excess_vs_baseline_pp": round(
            sum(float(row["log_excess_vs_baseline_pp"]) for row in active), 3
        ),
        "inactive_month_log_excess_vs_baseline_pp": round(
            sum(float(row["log_excess_vs_baseline_pp"]) for row in inactive), 3
        ),
        "worst_6_months_vs_baseline": negative[:6],
        "best_6_months_vs_baseline": list(reversed(negative[-6:])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rs = load_module("rs_booster_attr", RS_SCRIPT)
    monthly = load_module("rs_monthly_attr", MONTHLY_SCRIPT)
    oneway = load_module("rs_oneway_attr", ONEWAY_SCRIPT)
    base = rs.load_base()
    config = load_config(ROOT / "strategy.yaml")
    end = args.end or datetime.now(UTC).date().isoformat()
    warmup = (
        datetime.fromisoformat("2011-01-01").date() - timedelta(days=420)
    ).isoformat()
    source = YFinanceDataSource(ROOT / "data" / "cache")
    raw = {
        symbol: source.daily(symbol, warmup, end)
        for symbol in ("SPY", "QQQ", "TQQQ", "SOXL", "SOXX", "SMH")
    }
    frames = {
        "QQQ": base.features(raw["QQQ"], raw["SPY"]),
        "TQQQ": raw["TQQQ"],
        "SOXL": raw["SOXL"],
        "SOXX": rs.momentum_features(raw["SOXX"]),
    }
    fee = float(config.global_.buy_fee)
    slippage = float(config.backtest.default_slippage)
    active = base.build_active(config, raw, end, slippage)
    qqq = base.benchmark(raw["QQQ"], START, end, fee, slippage)

    base_policy = rs.BoosterPolicy("BASE_TQQQ", "base")
    base_curve, _, _ = rs.simulate(base, base_policy, frames, active, end, fee, slippage)
    candidate_curve, candidate_metrics, candidate_state = oneway.simulate(
        rs, monthly, base, frames, active, end, fee, slippage
    )

    windows = {
        "worst_1y": ("2018-08-07", "2019-08-07"),
        "worst_3y": ("2018-05-08", "2021-05-10"),
        "diagnostic_2018_2021": ("2018-01-01", "2021-12-31"),
    }
    results: dict[str, object] = {}
    for label, (window_start, window_end) in windows.items():
        rows = monthly_rows(
            base_curve,
            candidate_curve,
            qqq,
            frames["TQQQ"],
            frames["SOXL"],
            frames["SOXX"],
            frames["QQQ"],
            candidate_state,
            window_start,
            window_end,
        )
        results[label] = {
            "start": window_start,
            "end": window_end,
            "returns_pct": {
                "candidate": round(window_return(candidate_curve, window_start, window_end), 2),
                "baseline": round(window_return(base_curve, window_start, window_end), 2),
                "qqq": round(window_return(qqq, window_start, window_end), 2),
            },
            "drawdown": {
                "candidate": window_drawdown(candidate_curve, window_start, window_end),
                "baseline": window_drawdown(base_curve, window_start, window_end),
                "qqq": window_drawdown(qqq, window_start, window_end),
            },
            "summary": summarize_rows(rows),
            "months": rows,
        }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "RS6M_ONEWAY_ATTRIBUTION_NO_PRODUCTION_CHANGE",
        "candidate_metrics": candidate_metrics,
        "windows": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for label, result in results.items():
        print(label.upper(), result["returns_pct"], result["drawdown"])
        print("SUMMARY", result["summary"])
        print("MONTHS", result["months"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
