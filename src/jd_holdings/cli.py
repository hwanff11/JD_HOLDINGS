from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jd_holdings.application.analysis_service import AnalysisService
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import load_config
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

        # 지표 초기값(EMA60/ATR/RSI 등)이 요청 시작일 전에 충분히 형성되도록
        # 약 400일의 워밍업 데이터를 함께 조회한다. 성과 집계는 engine.run(start=...)에서
        # 사용자가 요청한 기간부터만 시작한다.
        warmup_start = (
            datetime.fromisoformat(start).date() - timedelta(days=400)
        ).isoformat()
        spy = data_source.daily("SPY", warmup_start, end, refresh=args.refresh)
        qqq = data_source.daily("QQQ", warmup_start, end, refresh=args.refresh)

        guard = config.market_regime.get("soxl_sector_guard", {})
        sector_data: dict[str, object] = {}
        if "SOXL" in symbols and guard.get("enabled", False):
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
        for symbol in symbols:
            target = data_source.daily(symbol, warmup_start, end, refresh=args.refresh)
            result = engine.run(
                symbol,
                target,
                spy,
                qqq,
                start=start,
                end=end,
                slippage=args.slippage,
                sector_data=sector_data if symbol == "SOXL" else None,
            )
            output["results"][symbol] = result.to_dict(include_equity=False)
            metrics = result.metrics
            sector_text = ""
            if symbol == "SOXL" and metrics.get("sector_guard_requested"):
                sector_text = (
                    f" sector_guard={int(metrics['sector_guard_applied'])}"
                    f" blocks={int(metrics['sector_guard_blocks'])}"
                )
            print(
                f"{symbol}: return={metrics['total_return_pct']:+.2f}% "
                f"CAGR={metrics['cagr_pct']:+.2f}% MDD={metrics['mdd_pct']:.2f}% "
                f"cycles={metrics['closed_cycles']} signals={metrics['signals']}"
                f"{sector_text}"
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
