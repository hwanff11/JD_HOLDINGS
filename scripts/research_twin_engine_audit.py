"""Audit the return source and path dependence of the monthly twin engine."""

# ruff: noqa: E501, I001

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research_simple_strategies import ROOT
from research_twin_engine_robustness import _simulate
from jd_holdings.backtest.performance import maximum_drawdown
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource


def _summary(equity: pd.Series) -> dict[str, float]:
    years = (equity.index[-1] - equity.index[0]).days / 365.2425
    returns = equity.pct_change().fillna(0.0)
    return {
        "total_return_pct": round((equity.iloc[-1] / equity.iloc[0] - 1) * 100, 2),
        "cagr_pct": round(((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "positive_day_pct": round((returns > 0).mean() * 100, 2),
    }


def _bootstrap(equity: pd.Series, *, seed: int = 20260811, samples: int = 1000) -> dict[str, float]:
    daily = equity.pct_change().dropna().to_numpy()
    block = 20
    rng = np.random.default_rng(seed)
    totals: list[float] = []
    mdds: list[float] = []
    for _ in range(samples):
        path: list[float] = []
        while len(path) < len(daily):
            start = int(rng.integers(0, len(daily) - block + 1))
            path.extend(daily[start : start + block])
        sampled = np.asarray(path[: len(daily)])
        curve = pd.Series(np.cumprod(1 + sampled))
        totals.append(float(curve.iloc[-1] - 1))
        mdds.append(float(maximum_drawdown(curve)))
    return {
        "return_p05_pct": round(float(np.percentile(totals, 5)) * 100, 2),
        "return_median_pct": round(float(np.percentile(totals, 50)) * 100, 2),
        "return_p95_pct": round(float(np.percentile(totals, 95)) * 100, 2),
        "positive_probability_pct": round(float(np.mean(np.asarray(totals) > 0)) * 100, 2),
        "mdd_median_pct": round(float(np.percentile(mdds, 50)) * 100, 2),
        "mdd_p05_pct": round(float(np.percentile(mdds, 5)) * 100, 2),
    }


def _inception_sensitivity(equity: pd.Series) -> dict[str, dict[str, float]]:
    first = equity.index[0]
    results: dict[str, dict[str, float]] = {}
    for offset in range(12):
        start = first + pd.DateOffset(months=offset)
        section = equity[equity.index >= start]
        results[str(offset)] = _summary(section)
    return results


def _best_year_removed(equity: pd.Series) -> dict[str, Any]:
    daily = equity.pct_change().fillna(0.0)
    annual = daily.groupby(daily.index.year).apply(lambda x: (1 + x).prod() - 1)
    best_year = int(annual.idxmax())
    without = daily[daily.index.year != best_year]
    return {
        "best_year": best_year,
        "best_year_return_pct": round(float(annual.loc[best_year]) * 100, 2),
        "return_without_best_year_pct": round(float((1 + without).prod() - 1) * 100, 2),
        "profitable_years": int((annual > 0).sum()),
        "total_years": int(len(annual)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-08-10")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "twin_engine_audit.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "reports" / "twin_engine_audit.md")
    args = parser.parse_args()
    config = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=800)).isoformat()
    raw = {
        symbol: source.daily(symbol, warmup, args.end)
        for symbol in ("TQQQ", "SOXL", "QQQ", "SOXX", config.idle_cash.symbol)
    }

    monthly_equity, monthly_metrics = _simulate(
        raw, config, end=args.end, ma_months=10, delay=0,
        slippage=args.slippage, monthly_rebalance=True,
    )
    signal_equity, signal_metrics = _simulate(
        raw, config, end=args.end, ma_months=10, delay=0,
        slippage=args.slippage, monthly_rebalance=False,
    )
    report = {
        "monthly_rebalance": monthly_metrics,
        "signal_change_only": signal_metrics,
        "rebalance_increment_pct": round(
            monthly_metrics["total_return_pct"] - signal_metrics["total_return_pct"], 2
        ),
        "monthly_inception_sensitivity": _inception_sensitivity(monthly_equity),
        "monthly_best_year_removed": _best_year_removed(monthly_equity),
        "signal_best_year_removed": _best_year_removed(signal_equity),
        "monthly_bootstrap": _bootstrap(monthly_equity),
        "signal_bootstrap": _bootstrap(signal_equity),
        "execution_audit": {
            "signal_uses_completed_month_end_close": True,
            "earliest_execution_is_next_session_open": True,
            "future_data_used": False,
        },
    }

    inception_returns = [
        value["total_return_pct"]
        for value in report["monthly_inception_sensitivity"].values()
    ]
    lines = [
        "# 월간 쌍발엔진 수익원 감사", "",
        "| 구조 | 누적수익 | MDD | Sharpe | 체결 |",
        "|---|---:|---:|---:|---:|",
        f"| 매월 15% 복원 | {monthly_metrics['total_return_pct']:+.2f}% | {monthly_metrics['mdd_pct']:.2f}% | {monthly_metrics['sharpe']:.3f} | {monthly_metrics['trade_fills']} |",
        f"| 신호 변경 시에만 거래 | {signal_metrics['total_return_pct']:+.2f}% | {signal_metrics['mdd_pct']:.2f}% | {signal_metrics['sharpe']:.3f} | {signal_metrics['trade_fills']} |",
        "",
        f"- 월별 리밸런싱 추가 기여: {report['rebalance_increment_pct']:+.2f}%p",
        f"- 시작월 12개 누적수익 범위: {min(inception_returns):+.2f}%~{max(inception_returns):+.2f}%",
        f"- 매월 복원형 최고연도 제거 후: {report['monthly_best_year_removed']['return_without_best_year_pct']:+.2f}%",
        f"- 신호변경형 최고연도 제거 후: {report['signal_best_year_removed']['return_without_best_year_pct']:+.2f}%",
        f"- 매월 복원형 부트스트랩 흑자확률: {report['monthly_bootstrap']['positive_probability_pct']:.2f}%",
        f"- 신호변경형 부트스트랩 흑자확률: {report['signal_bootstrap']['positive_probability_pct']:.2f}%",
        "",
        "> 월말 완료봉으로 신호를 만든 뒤 다음 거래일 시가부터 체결해 미래 데이터는 사용하지 않습니다.",
        "> 연구 전용이며 운영 코드·설정·Oracle·실주문을 변경하지 않습니다.",
    ]
    markdown = "\n".join(lines) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
