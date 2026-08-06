from __future__ import annotations

import html
import logging
from datetime import timedelta

from jd_holdings.backtest.engine import BacktestEngine

from .telegram_bot import TelegramBacktestRequest, TelegramBotApp

LOGGER = logging.getLogger(__name__)


class ValidatedTelegramBotApp(TelegramBotApp):
    """JDSS v1.3.1 Telegram app with validated backtest data dependencies.

    The legacy UI/command surface remains unchanged. Only the backtest execution path
    is overridden so SOXL backtests receive the same SOXX/SMH sector benchmarks used
    by the live analysis path.
    """

    def _run_backtest_and_send(self, request: TelegramBacktestRequest) -> None:
        try:
            start = request.start.isoformat()
            end = request.end.isoformat()
            warmup_start = (request.start - timedelta(days=400)).isoformat()
            spy = self.data_source.daily("SPY", warmup_start, end)
            qqq = self.data_source.daily("QQQ", warmup_start, end)

            guard = self.config.market_regime.get("soxl_sector_guard", {})
            sector_data = {}
            if "SOXL" in request.symbols and guard.get("enabled", False):
                for benchmark in guard.get("benchmark_candidates", ("SOXX", "SMH")):
                    name = str(benchmark).upper()
                    sector_data[name] = self.data_source.daily(name, warmup_start, end)

            engine = BacktestEngine(self.config)
            results = {}
            for symbol in request.symbols:
                target = self.data_source.daily(symbol, warmup_start, end)
                results[symbol] = engine.run(
                    symbol,
                    target,
                    spy,
                    qqq,
                    start=start,
                    end=end,
                    sector_data=sector_data if symbol == "SOXL" else None,
                )
            self._send_long(self._format_backtest_results(results))
        except Exception as exc:
            LOGGER.exception("Telegram 백테스트 실패")
            self._send(f"❌ 백테스트를 끝내지 못했어요.\n{html.escape(str(exc))}")
        finally:
            self._backtest_lock.release()
