from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd

from jd_holdings import __version__
from jd_holdings.config import StrategyConfig
from jd_holdings.core.enums import PositionState
from jd_holdings.core.models import OrderRequest
from jd_holdings.core.twin_core import monthly_trend_signal, target_quantity
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource

from .broker import Broker
from .database import SQLiteRepository
from .managed_account import managed_equity
from .order_manager import OrderManager, build_client_order_id

TERMINAL_STATUSES = {"FILLED", "CANCELED", "REJECTED", "REPLACED"}


@dataclass(frozen=True)
class PortfolioRunResult:
    trade_date: str
    signals: tuple[int, ...]
    events: tuple[str, ...]


class PortfolioService:
    """V3 monthly twin-core controller; live submission stays hard-disabled."""

    def __init__(
        self,
        config: StrategyConfig,
        repository: SQLiteRepository,
        broker: Broker,
        order_manager: OrderManager,
        data_source: YFinanceDataSource,
        market_clock: MarketClock,
        *,
        trading_mode: str,
    ) -> None:
        self.config = config
        self.repository = repository
        self.broker = broker
        self.order_manager = order_manager
        self.data_source = data_source
        self.market_clock = market_clock
        self.trading_mode = trading_mode

    def run_month_end(self, now: datetime | None = None) -> PortfolioRunResult | None:
        if not self.config.portfolio.enabled:
            return None
        if self.trading_mode == "live" or self.config.portfolio.live_enabled:
            raise RuntimeError("JDSS V3 코어는 live 모드가 잠겨 있습니다")
        current = now or datetime.now(UTC)
        completed = self.market_clock.latest_completed_session(
            current, delay_minutes=self.config.scheduler.signal_delay_minutes
        )
        signal_session = self.market_clock.month_end_session_on_or_before(completed)
        marker = self.repository.get_system_value("last_v3_core_signal_trade_date")
        if marker == signal_session.isoformat():
            return None

        start = signal_session - timedelta(days=500)
        frames: dict[str, pd.DataFrame] = {}
        for underlying in self.config.portfolio.core_underlyings.values():
            frames[underlying] = self.data_source.daily(
                underlying, start, signal_session, refresh=False
            )
        common_index: pd.DatetimeIndex | None = None
        for frame in frames.values():
            common_index = (
                frame.index if common_index is None else common_index.intersection(frame.index)
            )
        if common_index is None or common_index.empty:
            raise ValueError("월간 코어 기초자산의 공통 거래일 데이터가 없습니다")
        timestamp = next(
            (value for value in common_index if value.date() == signal_session), None
        )
        if timestamp is None:
            raise ValueError(
                "월간 코어 기초자산 데이터에 확정 월말 거래일이 없습니다: "
                f"{signal_session.isoformat()}"
            )

        equity = self.portfolio_equity()
        signal_ids: list[int] = []
        events: list[str] = []
        for symbol, underlying in self.config.portfolio.core_underlyings.items():
            trend = monthly_trend_signal(
                symbol,
                underlying,
                frames[underlying].loc[:timestamp],
                months=self.config.portfolio.trend_months,
            )
            core = self.repository.get_core_position(symbol)
            was_active = bool(core["trend_active"])
            if not trend.active:
                weight = Decimal("0")
            elif was_active:
                weight = self.config.portfolio.core_target_weight
            else:
                weight = self.config.portfolio.core_initial_weight
            self.repository.set_core_target(
                symbol,
                active=trend.active,
                target_weight=weight,
                signal_trade_date=trend.trade_date,
            )
            price = self.broker.get_price(symbol)
            target = target_quantity(equity, weight, price, self.config.global_.buy_fee)
            difference = target - int(core["qty"])
            tolerance_value = equity * self.config.portfolio.rebalance_tolerance_weight
            if abs(Decimal(difference) * price) < tolerance_value:
                difference = 0
            if difference < 0:
                receipt = self._sell_core(
                    symbol, -difference, price, trend.trade_date.isoformat()
                )
                events.append(
                    f"{symbol} 코어 위험축소 {-difference}주 주문 "
                    f"({receipt.status}, {receipt.filled_quantity}/{receipt.quantity}주 체결)"
                )
            elif difference > 0:
                signal_id, created = self.repository.create_core_buy_signal(
                    symbol=symbol,
                    trade_date=trend.trade_date,
                    signal_close=price,
                    planned_budget=(
                        Decimal(difference)
                        * price
                        * (Decimal("1") + self.config.global_.buy_limit_buffer)
                        * (Decimal("1") + self.config.global_.buy_fee)
                    ),
                    valid_until=self.market_clock.next_session_close(completed),
                    code_version=__version__,
                )
                if created:
                    signal_ids.append(signal_id)
                    events.append(f"{symbol} 코어 {difference}주 매수 승인 대기")
            state = "ON" if trend.active else "OFF"
            events.append(
                f"{symbol}/{underlying} 월간추세 {state} "
                f"({trend.close:.2f} / MA{self.config.portfolio.trend_months} "
                f"{trend.moving_average:.2f}, 목표 {weight * 100:.0f}%)"
            )
        self.repository.set_system_value(
            "last_v3_core_signal_trade_date", signal_session.isoformat()
        )
        return PortfolioRunResult(
            trade_date=signal_session.isoformat(),
            signals=tuple(signal_ids),
            events=tuple(events),
        )

    def portfolio_equity(self) -> Decimal:
        """JDSS-managed equity only; unrelated personal assets are excluded."""
        return managed_equity(self.config, self.repository, self.broker)

    def snapshot(self) -> dict[str, object]:
        equity = self.portfolio_equity()
        rows = []
        for core in self.repository.core_positions():
            symbol = str(core["symbol"])
            price = self.broker.get_price(symbol)
            market_value = price * int(core["qty"])
            booster = self.repository.get_position(symbol)
            rows.append(
                {
                    "symbol": symbol,
                    "underlying": core["underlying"],
                    "trend_active": bool(core["trend_active"]),
                    "target_weight": Decimal(str(core["target_weight"])),
                    "core_quantity": int(core["qty"]),
                    "core_market_value": market_value,
                    "core_weight": market_value / equity if equity else Decimal("0"),
                    "booster_quantity": booster.quantity,
                    "booster_cost_basis": booster.current_cost_basis,
                    "signal_trade_date": core["signal_trade_date"],
                }
            )
        return {"equity": equity, "rows": rows}

    def _sell_core(self, symbol: str, quantity: int, price: Decimal, context: str):
        if not self.config.portfolio.risk_reducing_sells_automatic:
            raise RuntimeError("코어 자동 위험축소가 비활성화되어 있습니다")
        client_order_id = build_client_order_id(
            symbol=symbol,
            purpose="CORE_REBALANCE_SELL",
            signal_id=None,
            unique_context=context,
        )
        try:
            receipt = self.order_manager.submit(
                OrderRequest(
                    client_order_id=client_order_id,
                    symbol=symbol,
                    side="SELL",
                    order_type="LIMIT",
                    quantity=quantity,
                    price=price * (Decimal("1") - self.config.global_.buy_limit_buffer),
                    purpose="CORE_REBALANCE_SELL",
                ),
                cycle_id=None,
            )
        except Exception as exc:
            self._enter_symbol_safe_mode(symbol, "CORE_SELL_SUBMISSION_FAILED")
            self.repository.log_event(
                "SAFE_MODE",
                "CORE_SELL_SUBMISSION_FAILED",
                "코어 위험축소 주문 제출에 실패했습니다",
                symbol=symbol,
                context={
                    "client_order_id": client_order_id,
                    "quantity": quantity,
                    "error": str(exc),
                },
            )
            raise

        if receipt.filled_quantity > 0:
            self.repository.apply_core_fill(client_order_id)
        remaining = max(0, receipt.quantity - receipt.filled_quantity)
        if receipt.status == "UNKNOWN" or (
            receipt.status in TERMINAL_STATUSES and remaining > 0
        ):
            self._enter_symbol_safe_mode(symbol, "CORE_SELL_INCOMPLETE")
            self.repository.log_event(
                "SAFE_MODE",
                "CORE_SELL_INCOMPLETE",
                "코어 위험축소 주문이 전량 완료되지 않았습니다",
                symbol=symbol,
                context={
                    "client_order_id": client_order_id,
                    "status": receipt.status,
                    "filled_quantity": receipt.filled_quantity,
                    "quantity": receipt.quantity,
                    "remaining_quantity": remaining,
                },
            )
            raise RuntimeError(
                f"{symbol} 코어 위험축소가 완료되지 않아 SAFE_MODE로 전환했습니다 "
                f"({receipt.filled_quantity}/{receipt.quantity}주, {receipt.status})"
            )
        return receipt

    def _enter_symbol_safe_mode(self, symbol: str, reason: str) -> None:
        position = self.repository.get_position(symbol)
        if position.state == PositionState.SAFE_MODE:
            return
        self.repository.transition_position(
            symbol,
            expected_state=position.state,
            new_state=PositionState.SAFE_MODE,
            reason_code=reason,
            expected_version=position.version,
        )
