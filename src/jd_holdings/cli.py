from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from jd_holdings.application.analysis_service import AnalysisService
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.backtest.engine import BacktestEngine
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.infrastructure.toss_client import TossClient
from jd_holdings.settings import load_runtime_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JDSS v1.1.2 운영 도구")
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
        spy = data_source.daily("SPY", start, end, refresh=args.refresh)
        qqq = data_source.daily("QQQ", start, end, refresh=args.refresh)
        output: dict[str, object] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "strategy_version": config.version,
            "config_version": config.config_version,
            "results": {},
        }
        engine = BacktestEngine(config)
        for symbol in symbols:
            target = data_source.daily(symbol, start, end, refresh=args.refresh)
            result = engine.run(
                symbol,
                target,
                spy,
                qqq,
                start=start,
                end=end,
                slippage=args.slippage,
            )
            output["results"][symbol] = result.to_dict(include_equity=False)
            metrics = result.metrics
            print(
                f"{symbol}: return={metrics['total_return_pct']:+.2f}% "
                f"CAGR={metrics['cagr_pct']:+.2f}% MDD={metrics['mdd_pct']:.2f}% "
                f"cycles={metrics['closed_cycles']} signals={metrics['signals']}"
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
        prices = client.get_prices(list(config.enabled_symbols))
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
