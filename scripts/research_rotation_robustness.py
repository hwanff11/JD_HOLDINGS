"""Robustness checks for the risk-matched relative-strength rotation candidate."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_simple_strategies import (
    DOWNLOAD_SYMBOLS,
    ROOT,
    SYMBOLS,
    SimResult,
    _combined,
    _research_indicators,
    _simulate_rotation,
)
from jd_holdings.backtest.engine import BacktestEngine, BacktestResult
from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import StrategyConfig, load_config
from jd_holdings.core.indicators import calculate_indicators
from jd_holdings.infrastructure.market_data import YFinanceDataSource

STRESS_PERIODS = {
    "2011_US_DOWNGRADE": ("2011-07-01", "2012-01-31"),
    "2015_CHINA_GROWTH": ("2015-07-01", "2016-03-31"),
    "2018_Q4_SELL_OFF": ("2018-09-01", "2019-03-31"),
    "2020_COVID": ("2020-02-01", "2020-06-30"),
    "2022_RATE_SHOCK": ("2022-01-01", "2023-01-31"),
}


def _baseline_results(
    config: StrategyConfig,
    production_frames: dict[str, pd.DataFrame],
    raw: dict[str, pd.DataFrame],
    *,
    start: str,
    end: str,
    slippage: float,
) -> dict[str, BacktestResult]:
    return {
        symbol: BacktestEngine(config).run(
            symbol,
            production_frames[symbol],
            production_frames["SPY"],
            production_frames["QQQ"],
            start=start,
            end=end,
            slippage=slippage,
            indicators_precomputed=True,
            sector_data={
                "SOXX": production_frames["SOXX"],
                "SMH": production_frames["SMH"],
            }
            if symbol == "SOXL"
            else None,
            idle_cash_data=raw[config.idle_cash.symbol],
        )
        for symbol in SYMBOLS
    }


def _rotation_results(
    config: StrategyConfig,
    research_frames: dict[str, pd.DataFrame],
    raw: dict[str, pd.DataFrame],
    *,
    start: str,
    end: str,
    slippage: float,
    delay: int,
) -> dict[str, SimResult]:
    return {
        "portfolio": _simulate_rotation(
            "N_ROTATION_TREND_CAP40",
            research_frames,
            raw[config.idle_cash.symbol],
            config,
            start=start,
            end=end,
            slippage=slippage,
            execution_delay_sessions=delay,
        )
    }


def _equity(results: dict[str, BacktestResult | SimResult]) -> pd.Series:
    return pd.concat(
        [
            result.equity_curve.rename(name)
            if isinstance(result, BacktestResult)
            else result.equity.rename(name)
            for name, result in results.items()
        ],
        axis=1,
        join="inner",
    ).sum(axis=1)


def _equity_metrics(equity: pd.Series, annualization_days: int) -> dict[str, float]:
    initial = float(equity.iloc[0])
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    sharpe, sortino = risk_adjusted_metrics(equity, annualization_days)
    return {
        "total_return_pct": round((final / initial - 1) * 100, 2),
        "cagr_pct": round(((final / initial) ** (1 / years) - 1) * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
    }


def _rolling_windows(end: date) -> list[tuple[str, str]]:
    windows: list[tuple[str, str]] = []
    for year in range(2011, end.year - 3):
        start = date(year, 1, 1)
        window_end = min(date(year + 4, 12, 31), end)
        if (window_end - start).days >= 365 * 4:
            windows.append((start.isoformat(), window_end.isoformat()))
    return windows


def _paired_block_bootstrap(
    baseline_equity: pd.Series,
    candidate_equity: pd.Series,
    *,
    iterations: int = 500,
    block_size: int = 20,
) -> dict[str, Any]:
    returns = pd.concat(
        [
            baseline_equity.pct_change(fill_method=None).rename("baseline"),
            candidate_equity.pct_change(fill_method=None).rename("candidate"),
        ],
        axis=1,
        join="inner",
    ).dropna()
    values = returns.to_numpy()
    length = len(values)
    if length < block_size:
        raise ValueError("bootstrap data is shorter than block size")
    rng = np.random.default_rng(20260811)
    baseline_total: list[float] = []
    candidate_total: list[float] = []
    baseline_mdd: list[float] = []
    candidate_mdd: list[float] = []

    for _ in range(iterations):
        sample_parts: list[np.ndarray] = []
        while sum(len(part) for part in sample_parts) < length:
            start = int(rng.integers(0, length - block_size + 1))
            sample_parts.append(values[start : start + block_size])
        sample = np.concatenate(sample_parts, axis=0)[:length]
        wealth = np.cumprod(1 + sample, axis=0)
        drawdown = wealth / np.maximum.accumulate(wealth, axis=0) - 1
        baseline_total.append(float((wealth[-1, 0] - 1) * 100))
        candidate_total.append(float((wealth[-1, 1] - 1) * 100))
        baseline_mdd.append(float(drawdown[:, 0].min() * 100))
        candidate_mdd.append(float(drawdown[:, 1].min() * 100))

    baseline_total_array = np.asarray(baseline_total)
    candidate_total_array = np.asarray(candidate_total)
    baseline_mdd_array = np.asarray(baseline_mdd)
    candidate_mdd_array = np.asarray(candidate_mdd)
    return {
        "iterations": iterations,
        "block_size_sessions": block_size,
        "candidate_beats_return_pct": round(
            float(np.mean(candidate_total_array > baseline_total_array) * 100), 2
        ),
        "candidate_has_lower_mdd_pct": round(
            float(np.mean(candidate_mdd_array > baseline_mdd_array) * 100), 2
        ),
        "baseline": {
            "median_total_return_pct": round(float(np.median(baseline_total_array)), 2),
            "p05_total_return_pct": round(float(np.percentile(baseline_total_array, 5)), 2),
            "median_mdd_pct": round(float(np.median(baseline_mdd_array)), 2),
            "p05_mdd_pct": round(float(np.percentile(baseline_mdd_array, 5)), 2),
        },
        "candidate": {
            "median_total_return_pct": round(float(np.median(candidate_total_array)), 2),
            "p05_total_return_pct": round(float(np.percentile(candidate_total_array, 5)), 2),
            "median_mdd_pct": round(float(np.median(candidate_mdd_array)), 2),
            "p05_mdd_pct": round(float(np.percentile(candidate_mdd_array, 5)), 2),
        },
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 상대강도 40% 후보 견고성 검증",
        "",
        f"- 데이터 종료일: {report['end_date']}",
        f"- 슬리피지: {report['slippage'] * 100:.2f}%",
        "",
        "## 승인 지연",
        "",
        "| 실행 지연 | 전체 누적 | CAGR | MDD | Sharpe | 최근 누적 | 최근 MDD |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for delay, values in report["execution_delay"].items():
        full = values["full_history"]
        recent = values["test_2023_present"]
        lines.append(
            f"| {delay}거래일 | {full['total_return_pct']:+.2f}% | "
            f"{full['cagr_pct']:.2f}% | {full['mdd_pct']:.2f}% | {full['sharpe']:.3f} | "
            f"{recent['total_return_pct']:+.2f}% | {recent['mdd_pct']:.2f}% |"
        )

    summary = report["rolling_summary"]
    lines.extend(
        [
            "",
            "## 5년 순환구간",
            "",
            f"- 구간 수: {summary['windows']}",
            f"- 후보 수익률 우위: {summary['return_win_rate_pct']:.2f}%",
            f"- 후보 Sharpe 우위: {summary['sharpe_win_rate_pct']:.2f}%",
            f"- 후보 MDD 우위: {summary['mdd_win_rate_pct']:.2f}%",
            "",
            "| 시작 | 종료 | 현행 누적 | 후보 누적 | 현행 MDD | 후보 MDD |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for window in report["rolling_windows"]:
        baseline = window["baseline"]
        candidate = window["candidate"]
        lines.append(
            f"| {window['start']} | {window['end']} | "
            f"{baseline['total_return_pct']:+.2f}% | {candidate['total_return_pct']:+.2f}% | "
            f"{baseline['mdd_pct']:.2f}% | {candidate['mdd_pct']:.2f}% |"
        )

    lines.extend(
        [
            "",
            "## 급락기",
            "",
            "| 구간 | 현행 수익 | 후보 수익 | 현행 MDD | 후보 MDD |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for name, values in report["stress_periods"].items():
        baseline = values["baseline"]
        candidate = values["candidate"]
        lines.append(
            f"| {name} | {baseline['total_return_pct']:+.2f}% | "
            f"{candidate['total_return_pct']:+.2f}% | {baseline['mdd_pct']:.2f}% | "
            f"{candidate['mdd_pct']:.2f}% |"
        )

    bootstrap = report["paired_block_bootstrap"]
    lines.extend(
        [
            "",
            "## 20거래일 블록 부트스트랩",
            "",
            f"- 반복: {bootstrap['iterations']}",
            f"- 후보 누적수익 우위 확률: {bootstrap['candidate_beats_return_pct']:.2f}%",
            f"- 후보 MDD 우위 확률: {bootstrap['candidate_has_lower_mdd_pct']:.2f}%",
            "",
            "> 부트스트랩은 관측된 일간 수익률 경로의 진단이며 미래 성과 보장이 아닙니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-08-10")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "rotation_robustness.json",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=ROOT / "reports" / "rotation_robustness.md",
    )
    args = parser.parse_args()

    config = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=500)).isoformat()
    raw = {symbol: source.daily(symbol, warmup, args.end) for symbol in DOWNLOAD_SYMBOLS}
    research_frames = {symbol: _research_indicators(frame) for symbol, frame in raw.items()}
    production_frames = {
        symbol: calculate_indicators(frame, config) for symbol, frame in raw.items()
    }

    baseline_full_results = _baseline_results(
        config,
        production_frames,
        raw,
        start="2011-01-01",
        end=args.end,
        slippage=args.slippage,
    )
    candidate_full_results = _rotation_results(
        config,
        research_frames,
        raw,
        start="2011-01-01",
        end=args.end,
        slippage=args.slippage,
        delay=1,
    )
    baseline_full_equity = _equity(baseline_full_results)
    candidate_full_equity = _equity(candidate_full_results)

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "end_date": args.end,
        "slippage": args.slippage,
        "execution_delay": {},
        "rolling_windows": [],
        "stress_periods": {},
    }
    for delay in (1, 2, 3):
        report["execution_delay"][str(delay)] = {}
        for segment, start in (
            ("full_history", "2011-01-01"),
            ("test_2023_present", "2023-01-01"),
        ):
            results = _rotation_results(
                config,
                research_frames,
                raw,
                start=start,
                end=args.end,
                slippage=args.slippage,
                delay=delay,
            )
            report["execution_delay"][str(delay)][segment] = _combined(results, config)

    for start, end in _rolling_windows(date.fromisoformat(args.end)):
        baseline = _combined(
            _baseline_results(
                config,
                production_frames,
                raw,
                start=start,
                end=end,
                slippage=args.slippage,
            ),
            config,
        )
        candidate = _combined(
            _rotation_results(
                config,
                research_frames,
                raw,
                start=start,
                end=end,
                slippage=args.slippage,
                delay=1,
            ),
            config,
        )
        report["rolling_windows"].append(
            {"start": start, "end": end, "baseline": baseline, "candidate": candidate}
        )

    windows = report["rolling_windows"]
    report["rolling_summary"] = {
        "windows": len(windows),
        "return_win_rate_pct": round(
            sum(
                window["candidate"]["total_return_pct"]
                > window["baseline"]["total_return_pct"]
                for window in windows
            )
            / len(windows)
            * 100,
            2,
        ),
        "sharpe_win_rate_pct": round(
            sum(
                window["candidate"]["sharpe"] > window["baseline"]["sharpe"]
                for window in windows
            )
            / len(windows)
            * 100,
            2,
        ),
        "mdd_win_rate_pct": round(
            sum(
                window["candidate"]["mdd_pct"] > window["baseline"]["mdd_pct"]
                for window in windows
            )
            / len(windows)
            * 100,
            2,
        ),
    }

    for name, (start, end) in STRESS_PERIODS.items():
        baseline_slice = baseline_full_equity.loc[start:end]
        candidate_slice = candidate_full_equity.loc[start:end]
        report["stress_periods"][name] = {
            "start": start,
            "end": end,
            "baseline": _equity_metrics(
                baseline_slice, config.backtest.annualization_days
            ),
            "candidate": _equity_metrics(
                candidate_slice, config.backtest.annualization_days
            ),
        }

    report["paired_block_bootstrap"] = _paired_block_bootstrap(
        baseline_full_equity,
        candidate_full_equity,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(_markdown(report), encoding="utf-8")
    print(_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
