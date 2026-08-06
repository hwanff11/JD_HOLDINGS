from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

from jd_holdings.application.analysis_service import AnalysisService
from jd_holdings.application.broker import MarketDataDryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.order_monitor import OrderMonitor
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.application.trading_service import TradingService
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.infrastructure.telegram_bot_v131 import ValidatedTelegramBotApp
from jd_holdings.infrastructure.toss_client import TossClient
from jd_holdings.settings import load_runtime_settings


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            ),
            logging.StreamHandler(),
        ],
    )


def main() -> None:
    settings = load_runtime_settings()
    config = load_config(settings.config_path)
    configure_logging(settings.log_path)
    repository = SQLiteRepository(settings.database_path, config)
    data_source = YFinanceDataSource(settings.cache_path)
    market_clock = MarketClock()
    if settings.trading_mode == "live":
        settings.require_live_trading()
        broker = TossClient()
    else:
        broker = MarketDataDryRunBroker(
            data_source,
            buying_power=config.global_.capital_per_symbol * len(config.enabled_symbols),
        )
    account_client = (
        TossClient()
        if os.getenv("TOSS_APP_KEY") and os.getenv("TOSS_APP_SECRET")
        else None
    )
    order_manager = OrderManager(repository, broker, settings)
    position_manager = PositionManager(config, repository, broker)
    tp_manager = TakeProfitManager(repository, broker, order_manager)
    trading_service = TradingService(
        config,
        repository,
        broker,
        order_manager,
        position_manager,
        tp_manager,
        market_clock,
    )
    order_monitor = OrderMonitor(
        config,
        repository,
        broker,
        order_manager,
        position_manager,
        tp_manager,
    )
    reconciliation_service = ReconciliationService(config, repository, broker)
    mismatches = reconciliation_service.run()
    if mismatches:
        logging.getLogger(__name__).error("시작 정합성 검사 실패: %s", mismatches)
    analysis_service = AnalysisService(config, repository, data_source, market_clock)
    ValidatedTelegramBotApp(
        config,
        settings,
        repository,
        analysis_service,
        trading_service,
        order_monitor,
        reconciliation_service,
        data_source,
        market_clock,
        account_client,
    ).run()


if __name__ == "__main__":
    main()
