from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.core.v322_allocation import (
    ALLOCATION_SYMBOLS,
    V322Policy,
    replay_targets,
    virtual_active_series,
)
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.research.initial_entry import (
    DEFAULT_SCENARIOS,
    leverage_breakdown,
    simulate_entry_window,
    summarize_scenarios,
)

HORIZONS = (21, 63, 126)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JDSS initial-entry scenario research")
    parser.add_argument(
        "--end",
        default="",
        help="YYYY-MM-DD; default latest completed XNYS session",
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=5,
        help="historical launch-date stride in sessions",
    )
    parser.add_argument("--output-json", default="reports/initial-entry-research.json")
    parser.add_argument("--output-md", default="reports/initial-entry-research.md")
    return parser.parse_args()


def _prepare_history(end: date):
    config = load_config()
    policy = V322Policy.from_config(config)
    strategy_start = pd.Timestamp(config.backtest.default_start).date()
    warmup_start = strategy_start - timedelta(days=420)
    source = YFinanceDataSource(Path(".cache") / "initial-entry-research")
    end_text = end.isoformat()

    raw = {
        symbol: source.daily(symbol, warmup_start, end_text, refresh=False)
        for symbol in ("SPY", "QQQ", "TQQQ", "SOXL", policy.rs_benchmark, "SMH")
    }
    sector_data = {
        symbol: raw[symbol]
        for symbol in (policy.rs_benchmark, "SMH")
        if symbol in raw
    }
    engine = StrategyBacktestEngine(config)
    virtual = {
        symbol: engine.run(
            symbol,
            raw[symbol],
            raw["SPY"],
            raw["QQQ"],
            start=strategy_start,
            end=end,
            slippage=float(config.backtest.default_slippage),
            sector_data=sector_data if symbol == "SOXL" else None,
        )
        for symbol in config.enabled_symbols
    }

    common = raw["QQQ"].index
    for symbol in ("TQQQ", "SOXL", policy.rs_benchmark):
        common = common.intersection(raw[symbol].index)
    active = {
        symbol: virtual_active_series(virtual[symbol], common)
        for symbol in config.enabled_symbols
    }
    targets = replay_targets(
        raw["QQQ"].reindex(common),
        raw[policy.rs_benchmark].reindex(common),
        active["TQQQ"],
        active["SOXL"],
        policy,
    )
    frames = {symbol: raw[symbol] for symbol in ALLOCATION_SYMBOLS}
    return config, policy, strategy_start, frames, targets


def _fmt(value: float, digits: int = 2) -> str:
    return f"{value:+.{digits}f}%"


def _render_markdown(payload: dict) -> str:
    scenario_header = (
        "| 시나리오 | 63일 중앙수익 | 63일 P10 | 63일 중앙 MDD | "
        "63일 일시매수 승률 | 하락장 우위 | 상승장 기회비용 | "
        "126일 중앙수익 | 126일 중앙 MDD |"
    )
    leverage_header = (
        "| 시작 레버리지 | 표본 | 63일 일시매수 대비 중앙 차이 | "
        "일시매수 승률 | 일시매수 손실구간 평균 방어 |"
    )
    lines = [
        "# JDSS 최초진입 분할매수 시나리오 연구",
        "",
        f"- 전략: `{payload['strategy_version']}`",
        f"- 데이터 종료일: `{payload['end_date']}`",
        f"- 시작점: `{payload['sample_start']} ~ {payload['sample_end']}`",
        f"- 시작점 간격: 미국 거래일 `{payload['stride']}`일마다",
        f"- 시작점 수: `{payload['samples_per_scenario']}`개 / 시나리오",
        "- 평가기간: 21 / 63 / 126 미국 거래일",
        "- 공통 조건: V3.2.2 목표배분, HWM75, 매수·매도 수수료 0.1%, 슬리피지 0.1%",
        "- 차이는 최초진입 누적 투자비율과 단계 간격뿐이며, 이후 전략 목표는 동일하게 추종",
        "",
        "## 1. 시나리오 비교",
        "",
        scenario_header,
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    row_template = (
        "| {scenario} | {r63} | {p63} | {m63} | {win63:.1f}% | "
        "{down} | {cost} | {r126} | {m126} |"
    )
    for row in payload["summary"]:
        lines.append(
            row_template.format(
                scenario=row["scenario"],
                r63=_fmt(row["median_return_63_pct"]),
                p63=_fmt(row["p10_return_63_pct"]),
                m63=_fmt(row["median_mdd_63_pct"]),
                win63=row["beat_lump_63_pct"],
                down=_fmt(row["down_market_edge_63_pctpt"]),
                cost=_fmt(row["up_market_cost_63_pctpt"]),
                r126=_fmt(row["median_return_126_pct"]),
                m126=_fmt(row["median_mdd_126_pct"]),
            )
        )

    lines.extend(
        [
            "",
            "### 지표 읽는 법",
            "",
            "- `P10`: 시작점 중 하위 10% 수익률. 높을수록 나쁜 진입시점 방어력이 좋습니다.",
            "- `일시매수 승률`: 같은 시작일의 100% 즉시진입보다 수익률이 높았던 비율입니다.",
            "- `하락장 우위`: 100% 즉시진입이 손실이었던 구간에서 평균적으로 얼마나 덜 잃었는지입니다.",
            "- `상승장 기회비용`: 100% 즉시진입이 수익이었던 구간에서 평균적으로 얼마나 덜 벌었는지입니다.",
            "",
            "## 2. 현재안(50 → 75 → 100%, 3거래일)의 시작 레버리지별 비교",
            "",
            leverage_header,
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for row in payload["current_by_leverage"]:
        lines.append(
            f"| {row['leverage']:.2f}x | {row['samples']} | "
            f"{_fmt(row['median_edge_pctpt'])} | {row['beat_lump_pct']:.1f}% | "
            f"{_fmt(row['down_market_edge_pctpt'])} |"
        )

    lines.extend(
        [
            "",
            "## 3. 연구 원칙",
            "",
            "이 결과는 최초 6개월의 진입 리스크만 비교합니다. "
            "장기 전략 자체의 우열을 다시 최적화하지 않습니다.",
            "이번 1차 연구에서 상위 후보가 비슷하면 후보 2~3개만 "
            "거래일 매일 시작점으로 다시 세밀하게 검증해 과최적화를 줄입니다.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    args = _parse_args()
    if args.stride < 1:
        raise ValueError("--stride must be >= 1")
    clock = MarketClock()
    end = pd.Timestamp(args.end).date() if args.end else clock.latest_completed_session()
    config, policy, strategy_start, frames, targets = _prepare_history(end)

    start_position = max(
        1,
        int(targets.index.searchsorted(pd.Timestamp(strategy_start))),
    )
    last_start = len(targets.index) - max(HORIZONS)
    positions = list(range(start_position, last_start + 1, args.stride))
    if not positions:
        raise RuntimeError("분석 가능한 최초진입 시작점이 없습니다")

    rows: list[dict] = []
    for position in positions:
        for scenario in DEFAULT_SCENARIOS:
            result = simulate_entry_window(
                frames,
                targets,
                start_position=position,
                scenario=scenario,
                horizons=HORIZONS,
                initial_capital=float(policy.initial_capital),
                hwm_reinvestment_fraction=float(policy.hwm_reinvestment_fraction),
                buy_fee=float(config.global_.buy_fee),
                sell_fee=float(config.global_.sell_fee),
                slippage=float(config.backtest.default_slippage),
            )
            result["scenario"] = scenario.name
            rows.append(result)

    summary = summarize_scenarios(rows, horizons=HORIZONS)
    current_by_leverage = leverage_breakdown(
        rows,
        scenario_name="CURRENT_50_75_100_3D",
        horizon=63,
    )
    payload = {
        "strategy_version": config.version,
        "config_version": config.config_version,
        "end_date": end.isoformat(),
        "sample_start": targets.index[positions[0]].date().isoformat(),
        "sample_end": targets.index[positions[-1]].date().isoformat(),
        "stride": args.stride,
        "samples_per_scenario": len(positions),
        "scenarios": [
            {
                "name": item.name,
                "fractions": list(item.fractions),
                "interval_sessions": item.interval_sessions,
            }
            for item in DEFAULT_SCENARIOS
        ],
        "summary": summary,
        "current_by_leverage": current_by_leverage,
    }

    json_path = Path(args.output_json)
    md_path = Path(args.output_md)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
