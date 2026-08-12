from __future__ import annotations

import logging
import logging.handlers
import os
from decimal import Decimal
from pathlib import Path

from jd_holdings.application.analysis_service import AnalysisService
from jd_holdings.application.broker import MarketDataDryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.idle_cash_manager import IdleCashManager
from jd_holdings.application.managed_account import managed_cash_balance
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.order_monitor import OrderMonitor
from jd_holdings.application.portfolio_service import PortfolioService
from jd_holdings.application.position_manager import PositionManager
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.tp_manager import TakeProfitManager
from jd_holdings.application.trading_service import TradingService
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.infrastructure.telegram_bot_final import FinalTelegramBotApp
from jd_holdings.infrastructure.toss_client import TossClient
from jd_holdings.settings import load_runtime_settings


def restore_dry_run_holdings(
    repository: SQLiteRepository,
    broker: MarketDataDryRunBroker,
) -> None:
    """Rebuild the in-memory dry-run broker from the persisted JDSS ledger."""
    for symbol in repository.config.enabled_symbols:
        position = repository.get_position(symbol)
        core = repository.get_core_position(symbol)
        core_quantity = int(core["qty"])
        total_quantity = position.quantity + core_quantity
        if total_quantity <= 0:
            continue
        combined_cost = position.current_cost_basis + Decimal(str(core["cost_basis"]))
        broker.holdings[symbol] = {
            "quantity": total_quantity,
            "averagePurchasePrice": combined_cost / Decimal(total_quantity),
        }
    if repository.config.idle_cash.enabled:
        cash_state = repository.get_idle_cash_state()
        if cash_state.managed_quantity > 0:
            broker.holdings[cash_state.symbol] = {
                "quantity": cash_state.managed_quantity,
                "averagePurchasePrice": cash_state.average_price,
            }

    # Realized P/L and fees must survive a process restart. Rebuild cash from all
    # persisted JDSS fills rather than from current position cost bases alone.
    broker.buying_power = max(
        Decimal("0"), managed_cash_balance(repository.config, repository)
    )

    # A zero-fill PENDING/SUBMITTED DRY order can be reconstructed exactly. A
    # PARTIAL_FILLED order cannot be proven from the in-memory broker after a restart,
    # so it is intentionally left unrestored and Reconciliation moves the sleeve to
    # SAFE_MODE instead of guessing the remaining broker state.
    for local in repository.open_orders():
        broker_order_id = str(local.get("broker_order_id") or "")
        if not broker_order_id.startswith("DRY-"):
            continue
        status = str(local["status"])
        if status not in {"PENDING", "SUBMITTED"} or int(local["filled_qty"]) != 0:
            continue
        quantity = int(local["qty"])
        broker.orders[broker_order_id] = {
            "orderId": broker_order_id,
            "clientOrderId": str(local["client_order_id"]),
            "symbol": str(local["symbol"]),
            "side": str(local["side"]),
            "orderType": str(local["order_type"]),
            "timeInForce": "DAY",
            "status": "PENDING",
            "price": str(local["price"]) if local.get("price") is not None else None,
            "quantity": str(quantity),
            "execution": {
                "filledQuantity": "0",
                "averageFilledPrice": None,
                "filledAmount": None,
                "commission": "0",
                "tax": "0",
                "filledAt": None,
                "settlementDate": None,
            },
            "_appliedFilledQuantity": "0",
            "_appliedFilledAmount": "0",
        }
        try:
            broker.sequence = max(broker.sequence, int(broker_order_id.rsplit("-", 1)[-1]))
        except ValueError:
            pass


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
    if config.portfolio.enabled and settings.trading_mode == "live":
        raise RuntimeError("JDSS V3.1은 운영 검증 전이므로 전체 live 모드가 잠겨 있습니다")
    if settings.trading_mode == "live":
        settings.require_live_trading()
        broker = TossClient()
    else:
        broker = MarketDataDryRunBroker(
            data_source,
            buying_power=config.total_strategy_capital,
        )
        restore_dry_run_holdings(repository, broker)
    account_client = (
        TossClient()
        if os.getenv("TOSS_APP_KEY") and os.getenv("TOSS_APP_SECRET")
        else None
    )
    order_manager = OrderManager(repository, broker, settings)
    idle_cash_manager = IdleCashManager(
        config, repository, broker, order_manager, market_clock
    )
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
        idle_cash_manager,
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
    idle_cash_manager.refresh_orders()
    mismatches = reconciliation_service.run()
    if mismatches:
        logging.getLogger(__name__).error("시작 정합성 검사 실패: %s", mismatches)
    analysis_service = AnalysisService(config, repository, data_source, market_clock)
    portfolio_service = PortfolioService(
        config,
        repository,
        broker,
        order_manager,
        data_source,
        market_clock,
        trading_mode=settings.trading_mode,
    )
    FinalTelegramBotApp(
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
        idle_cash_manager,
        portfolio_service,
    ).run()


if __name__ == "__main__":
    main()
