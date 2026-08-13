from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from jd_holdings.application.analysis_service import AnalysisService
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
from jd_holdings.core.v322_allocation import V322Policy
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.infrastructure.toss_client import TossClient
from jd_holdings.settings import load_runtime_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JDSS 운영 도구")
    parser.add_argument("--config", default="strategy.yaml", help="strategy.yaml 경로")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-config", help="설정 검증")
    subparsers.add_parser("init-db", help="SQLite 스키마 생성")
    analyze = subparsers.add_parser("analyze", help="최신 완결 일봉 JDSS 분석")
    analyze.add_argument("--symbol", choices=["TQQQ", "SOXL"])
    backtest = subparsers.add_parser("backtest", help="yfinance 장기 백테스트")
    backtest.add_argument("--symbol", choices=["TQQQ", "SOXL", "ALL"], default="ALL")
    backtest.add_argument("--start")
    backtest.add_argument("--end")
    backtest.add_argument("--slippage", type=float)
    backtest.add_argument("--refresh", action="store_true")
    backtest.add_argument("--output", type=Path)
    subparsers.add_parser("toss-smoke", help="주문 없이 Toss 인증·시세·장상태만 조회")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    V322Policy.from_config(config)
    if args.command == "validate-config":
        print(f"OK strategy={config.version} config={config.config_version}")
        return 0

    settings = load_runtime_settings()
    repository = SQLiteRepository(settings.database_path, config)
    if args.command == "init-db":
        print(f"OK database={settings.database_path}")
        return 0

    data_source = YFinanceDataSource("data/cache")
    market_clock = MarketClock()
    if args.command == "analyze":
        results = AnalysisService(config, repository, data_source, market_clock).analyze_all()
        for result in results:
            if args.symbol and result.symbol != args.symbol:
                continue
            print(
                json.dumps(
                    {
                        "symbol": result.symbol,
                        "trade_date": result.trade_date.isoformat(),
                        "score": result.score.detail(),
                        "decision": {
                            "action": result.decision.action.value,
                            "allowed": result.decision.allowed,
                            "reason_codes": result.decision.reason_codes,
                            "planned_budget": str(result.decision.planned_budget),
                        },
                        "signal_id": result.signal_id,
                        "signal_created": result.signal_created,
                        "execution_mode": "VIRTUAL_OVERLAY_SIGNAL_ONLY",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 0

    if args.command == "backtest":
        completed = market_clock.latest_completed_session()
        start = args.start or config.backtest.default_start
        end = args.end or completed.isoformat()
        symbols = config.enabled_symbols if args.symbol == "ALL" else (args.symbol,)
        requested_start = datetime.fromisoformat(start).date()
        strategy_start = datetime.fromisoformat(config.backtest.default_start).date()
        data_start = min(requested_start, strategy_start)
        warmup_start = (data_start - timedelta(days=420)).isoformat()

        spy = data_source.daily("SPY", warmup_start, end, refresh=args.refresh)
        qqq = data_source.daily("QQQ", warmup_start, end, refresh=args.refresh)
        idle_cash_data = None
        if config.idle_cash.enabled:
            idle_cash_data = data_source.daily(
                config.idle_cash.symbol, warmup_start, end, refresh=args.refresh
            )

        guard = config.market_regime.get("soxl_sector_guard", {})
        sector_data: dict[str, pd.DataFrame] = {}
        if "SOXL" in config.enabled_symbols and guard.get("enabled", False):
            for benchmark in guard.get("benchmark_candidates", ("SOXX", "SMH")):
                name = str(benchmark).upper()
                try:
                    sector_data[name] = data_source.daily(
                        name, warmup_start, end, refresh=args.refresh
                    )
                except Exception as exc:
                    print(
                        f"warning: {name} sector data unavailable; continuing with "
                        f"available benchmarks ({exc})",
                        file=sys.stderr,
                    )

        output: dict[str, object] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "strategy_version": config.version,
            "config_version": config.config_version,
            "results": {},
        }
        engine = StrategyBacktestEngine(config)
        completed_results: dict[str, object] = {}
        target_frames: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            target = data_source.daily(symbol, warmup_start, end, refresh=args.refresh)
            target_frames[symbol] = target
            result = engine.run(
                symbol,
                target,
                spy,
                qqq,
                start=start,
                end=end,
                slippage=args.slippage,
                sector_data=sector_data if symbol == "SOXL" else None,
                idle_cash_data=idle_cash_data,
            )
            output["results"][symbol] = result.to_dict(include_equity=False)
            completed_results[symbol] = result
            metrics = result.metrics
            print(
                f"{symbol} virtual-JDSS: return={metrics['total_return_pct']:+.2f}% "
                f"CAGR={metrics['cagr_pct']:+.2f}% MDD={metrics['mdd_pct']:.2f}% "
                f"cycles={metrics['closed_cycles']} signals={metrics['signals']}"
            )

        if config.portfolio.enabled and symbols == config.enabled_symbols:
            virtual_results = {}
            for symbol in config.enabled_symbols:
                target = target_frames.get(symbol)
                if target is None:
                    target = data_source.daily(
                        symbol, warmup_start, end, refresh=args.refresh
                    )
                    target_frames[symbol] = target
                virtual_results[symbol] = engine.run(
                    symbol,
                    target,
                    spy,
                    qqq,
                    start=config.backtest.default_start,
                    end=end,
                    slippage=args.slippage,
                    sector_data=sector_data if symbol == "SOXL" else None,
                )
            raw_frames: dict[str, pd.DataFrame] = {
                **target_frames,
                "SPY": spy,
                "QQQ": qqq,
            }
            policy = V322Policy.from_config(config)
            if policy.rs_benchmark not in raw_frames:
                rs_frame = sector_data.get(policy.rs_benchmark)
                if rs_frame is None:
                    rs_frame = data_source.daily(
                        policy.rs_benchmark, warmup_start, end, refresh=args.refresh
                    )
                raw_frames[policy.rs_benchmark] = rs_frame
            portfolio_result = PortfolioBacktestEngine(config).run(
                raw_frames,
                virtual_results,
                start=start,
                end=end,
                slippage=args.slippage,
            )
            portfolio_metrics = portfolio_result.metrics
            serialized = portfolio_result.to_dict(include_equity=False)
            output["v322_portfolio"] = serialized
            output["v3_portfolio"] = serialized
        else:
            portfolio = pd.concat(
                [
                    result.equity_curve.rename(symbol)
                    for symbol, result in completed_results.items()
                ],
                axis=1,
                join="inner",
            ).sum(axis=1)
            years = (portfolio.index[-1] - portfolio.index[0]).days / 365.2425
            portfolio_metrics = {
                "initial_equity": round(float(portfolio.iloc[0]), 2),
                "final_equity": round(float(portfolio.iloc[-1]), 2),
                "total_return_pct": round(
                    float((portfolio.iloc[-1] / portfolio.iloc[0] - 1) * 100), 2
                ),
                "cagr_pct": round(
                    float(((portfolio.iloc[-1] / portfolio.iloc[0]) ** (1 / years) - 1) * 100),
                    2,
                ),
                "mdd_pct": round(
                    float((portfolio / portfolio.cummax() - 1).min() * 100), 2
                ),
                "idle_cash_income": 0.0,
            }
        output["portfolio_metrics"] = portfolio_metrics
        print(
            "PORTFOLIO: "
            f"return={portfolio_metrics['total_return_pct']:+.2f}% "
            f"CAGR={portfolio_metrics['cagr_pct']:+.2f}% "
            f"MDD={portfolio_metrics['mdd_pct']:.2f}% "
            f"Sharpe={portfolio_metrics.get('sharpe', 0):.3f}"
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"saved={args.output}")
        return 0

    if args.command == "toss-smoke":
        client = TossClient()
        smoke_symbols = ["QQQ", *config.enabled_symbols]
        if config.idle_cash.enabled:
            smoke_symbols.append(config.idle_cash.symbol)
        prices = client.get_prices(list(dict.fromkeys(smoke_symbols)))
        calendar = client.get_market_calendar()
        print(
            json.dumps(
                {
                    "authenticated": True,
                    "prices": {key: str(value) for key, value in prices.items()},
                    "market_dates": {
                        key: value.get("date")
                        for key, value in calendar.items()
                        if isinstance(value, dict)
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
