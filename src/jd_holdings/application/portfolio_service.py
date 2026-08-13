from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd

from jd_holdings import __version__
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import StrategyConfig
from jd_holdings.core.enums import PositionState
from jd_holdings.core.models import OrderRequest
from jd_holdings.core.twin_core import target_quantity
from jd_holdings.core.v322_allocation import (
    ALLOCATION_SYMBOLS,
    V322Policy,
    replay_targets,
    virtual_active_series,
)
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource

from .broker import Broker
from .database import SQLiteRepository
from .managed_account import (
    current_v322_capital_state,
    managed_equity,
    marked_managed_equity,
    raw_managed_cash_balance,
    record_v322_equity,
)
from .order_manager import OrderManager, build_client_order_id

TERMINAL_STATUSES = {"FILLED", "CANCELED", "REJECTED", "REPLACED"}


@dataclass(frozen=True)
class PortfolioRunResult:
    trade_date: str
    signals: tuple[int, ...]
    events: tuple[str, ...]


class PortfolioService:
    """V3.2.2 QQQ/TQQQ/SOXL allocator with HWM75 controlled compounding."""

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
        self.policy = V322Policy.from_config(config)
        self.repository = repository
        self.broker = broker
        self.order_manager = order_manager
        self.data_source = data_source
        self.market_clock = market_clock
        self.trading_mode = trading_mode
        self._ensure_allocation_rows()

    def _ensure_allocation_rows(self) -> None:
        underlyings = {"QQQ": "QQQ", "TQQQ": "QQQ", "SOXL": self.policy.rs_benchmark}
        now = datetime.now(UTC).isoformat()
        with self.repository.transaction() as connection:
            for symbol in ALLOCATION_SYMBOLS:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO core_positions(
                        symbol, underlying, target_weight, updated_at
                    ) VALUES (?, ?, '0', ?)
                    """,
                    (symbol, underlyings[symbol], now),
                )
                connection.execute(
                    "UPDATE core_positions SET underlying = ? WHERE symbol = ?",
                    (underlyings[symbol], symbol),
                )

    def run_allocation(self, now: datetime | None = None) -> PortfolioRunResult | None:
        if not self.config.portfolio.enabled:
            return None
        if self.trading_mode == "live" or self.config.portfolio.live_enabled:
            raise RuntimeError("JDSS V3.2.2는 live 모드가 잠겨 있습니다")
        current = now or datetime.now(UTC)
        completed = self.market_clock.latest_completed_session(
            current, delay_minutes=self.config.scheduler.signal_delay_minutes
        )
        marker = self.repository.get_system_value("last_v322_allocation_trade_date")
        if marker == completed.isoformat():
            return None

        raw, target = self._calculate_target(completed)
        completed_ts = next(
            (timestamp for timestamp in target.index if timestamp.date() == completed), None
        )
        if completed_ts is None:
            raise ValueError(
                f"V3.2.2 target에 완결 거래일이 없습니다: {completed.isoformat()}"
            )
        target_row = target.loc[completed_ts]
        marked_equity = self._completed_marked_equity(raw, completed_ts)
        high_water, risk_budget = record_v322_equity(
            self.config, self.repository, marked_equity
        )

        old_weights = {
            symbol: Decimal(str(self.repository.get_core_position(symbol)["target_weight"]))
            for symbol in ALLOCATION_SYMBOLS
        }
        new_weights = {
            symbol: Decimal(str(float(target_row[symbol])))
            for symbol in ALLOCATION_SYMBOLS
        }
        changed = any(old_weights[symbol] != new_weights[symbol] for symbol in ALLOCATION_SYMBOLS)
        signal_ids: list[int] = []
        events: list[str] = []

        if changed:
            for symbol in ALLOCATION_SYMBOLS:
                self.repository.invalidate_active_signals(
                    symbol, reason="SUPERSEDED_BY_V322_ALLOCATION"
                )
            # Risk-reducing sells are completed before any new buy approval is created.
            for symbol in ALLOCATION_SYMBOLS:
                signal_id, event = self._apply_target(
                    symbol,
                    new_weights[symbol],
                    risk_budget,
                    completed,
                    allow_buy=False,
                )
                if signal_id is not None:
                    signal_ids.append(signal_id)
                if event:
                    events.append(event)
            for symbol in ALLOCATION_SYMBOLS:
                signal_id, event = self._apply_target(
                    symbol,
                    new_weights[symbol],
                    risk_budget,
                    completed,
                    allow_buy=True,
                )
                if signal_id is not None:
                    signal_ids.append(signal_id)
                if event:
                    events.append(event)

        events.append(
            "V3.2.2 배분 "
            f"레버리지 {float(target_row['leverage']):.2f}x · "
            f"RS6M {'ON' if bool(target_row['semiconductor_active']) else 'OFF'} · "
            f"JDSS TQQQ {'ON' if bool(target_row['jdss_tqqq_active']) else 'OFF'} / "
            f"SOXL {'ON' if bool(target_row['jdss_soxl_active']) else 'OFF'} · "
            f"목표 QQQ {new_weights['QQQ'] * 100:.2f}% / "
            f"TQQQ {new_weights['TQQQ'] * 100:.2f}% / "
            f"SOXL {new_weights['SOXL'] * 100:.2f}%"
        )
        events.append(
            f"HWM ${high_water:,.2f} · 위험예산 ${risk_budget:,.2f} · "
            f"완결종가 평가액 ${marked_equity:,.2f}"
        )
        self.repository.set_system_value(
            "last_v322_allocation_trade_date", completed.isoformat()
        )
        self.repository.set_system_value(
            "last_v322_allocation_state",
            (
                f"{float(target_row['leverage']):.2f}|"
                f"{int(bool(target_row['semiconductor_active']))}|"
                f"{new_weights['QQQ']}|{new_weights['TQQQ']}|{new_weights['SOXL']}"
            ),
        )
        return PortfolioRunResult(
            trade_date=completed.isoformat(),
            signals=tuple(dict.fromkeys(signal_ids)),
            events=tuple(events),
        )

    def run_month_end(self, now: datetime | None = None) -> PortfolioRunResult | None:
        """Backward-compatible scheduler entry point; V3.2.2 evaluates each completed session."""
        return self.run_allocation(now)

    def _calculate_target(
        self, completed
    ) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
        strategy_start = datetime.fromisoformat(self.config.backtest.default_start).date()
        warmup_start = strategy_start - timedelta(days=420)
        symbols = ("SPY", "QQQ", "TQQQ", "SOXL", self.policy.rs_benchmark, "SMH")
        raw: dict[str, pd.DataFrame] = {}
        for symbol in dict.fromkeys(symbols):
            raw[symbol] = self.data_source.daily(
                symbol, warmup_start, completed, refresh=True
            )

        sector_data = {
            name: raw[name]
            for name in ("SOXX", "SMH")
            if name in raw
        }
        engine = StrategyBacktestEngine(self.config)
        virtual_results = {
            symbol: engine.run(
                symbol,
                raw[symbol],
                raw["SPY"],
                raw["QQQ"],
                start=strategy_start,
                end=completed,
                slippage=self.config.backtest.default_slippage,
                sector_data=sector_data if symbol == "SOXL" else None,
            )
            for symbol in self.config.enabled_symbols
        }
        common = raw["QQQ"].index
        for symbol in ("TQQQ", "SOXL", self.policy.rs_benchmark):
            common = common.intersection(raw[symbol].index)
        active = {
            symbol: virtual_active_series(virtual_results[symbol], common)
            for symbol in self.config.enabled_symbols
        }
        targets = replay_targets(
            raw["QQQ"].reindex(common),
            raw[self.policy.rs_benchmark].reindex(common),
            active["TQQQ"],
            active["SOXL"],
            self.policy,
        )
        return raw, targets

    def _completed_marked_equity(
        self, raw: dict[str, pd.DataFrame], timestamp: pd.Timestamp
    ) -> Decimal:
        cash = raw_managed_cash_balance(self.config, self.repository)
        value = cash
        sell_multiplier = Decimal("1") - self.config.global_.sell_fee
        for core in self.repository.core_positions():
            symbol = str(core["symbol"])
            quantity = int(core["qty"])
            if quantity <= 0 or symbol not in raw or timestamp not in raw[symbol].index:
                continue
            close = Decimal(str(raw[symbol].loc[timestamp, "close"]))
            value += Decimal(quantity) * close * sell_multiplier
        # Direct H40 positions must remain zero in V3.2.2, but include them in marked
        # equity if a stale dry-run database is inspected before migration.
        for symbol in self.config.enabled_symbols:
            position = self.repository.get_position(symbol)
            if position.quantity <= 0:
                continue
            close = Decimal(str(raw[symbol].loc[timestamp, "close"]))
            value += Decimal(position.quantity) * close * sell_multiplier
        return max(Decimal("0"), value)

    def _apply_target(
        self,
        symbol: str,
        weight: Decimal,
        risk_budget: Decimal,
        trade_date,
        *,
        allow_buy: bool,
    ) -> tuple[int | None, str | None]:
        core = self.repository.get_core_position(symbol)
        previous_weight = Decimal(str(core["target_weight"]))
        if previous_weight != weight:
            self.repository.set_core_target(
                symbol,
                active=weight > 0,
                target_weight=weight,
                signal_trade_date=trade_date,
            )
            core = self.repository.get_core_position(symbol)

        price = self.broker.get_price(symbol)
        target = target_quantity(
            risk_budget, weight, price, self.config.global_.buy_fee
        )
        difference = target - int(core["qty"])
        if difference < 0 and not allow_buy:
            receipt = self._sell_core(symbol, -difference, price, trade_date.isoformat())
            return (
                None,
                f"{symbol} 위험축소 {-difference}주 ({receipt.status}, "
                f"{receipt.filled_quantity}/{receipt.quantity}주)",
            )
        if difference <= 0 or not allow_buy:
            return None, None

        planned_budget = (
            Decimal(difference)
            * price
            * (Decimal("1") + self.config.global_.buy_limit_buffer)
            * (Decimal("1") + self.config.global_.buy_fee)
        )
        signal_id, created = self.repository.create_core_buy_signal(
            symbol=symbol,
            trade_date=trade_date,
            signal_close=price,
            planned_budget=planned_budget,
            valid_until=self.market_clock.next_session_close(trade_date),
            code_version=__version__,
        )
        if not created:
            return None, None
        return signal_id, f"{symbol} 목표 {weight * 100:.2f}% · {difference}주 매수 승인 대기"

    def portfolio_equity(self) -> Decimal:
        return managed_equity(self.config, self.repository, self.broker)

    def snapshot(self) -> dict[str, object]:
        marked = marked_managed_equity(self.config, self.repository, self.broker)
        high_water, risk_budget = current_v322_capital_state(self.config, self.repository)
        rows = []
        for core in self.repository.core_positions():
            symbol = str(core["symbol"])
            if symbol not in ALLOCATION_SYMBOLS:
                continue
            price = self.broker.get_price(symbol)
            market_value = price * int(core["qty"])
            rows.append(
                {
                    "symbol": symbol,
                    "underlying": core["underlying"],
                    "trend_active": bool(core["trend_active"]),
                    "target_weight": Decimal(str(core["target_weight"])),
                    "core_quantity": int(core["qty"]),
                    "core_market_value": market_value,
                    "core_weight": market_value / marked if marked else Decimal("0"),
                    "booster_quantity": 0,
                    "booster_cost_basis": Decimal("0"),
                    "signal_trade_date": core["signal_trade_date"],
                }
            )
        return {
            "equity": marked,
            "risk_budget": risk_budget,
            "high_water": high_water,
            "rows": rows,
        }

    def _sell_core(self, symbol: str, quantity: int, price: Decimal, context: str):
        if not self.config.portfolio.risk_reducing_sells_automatic:
            raise RuntimeError("V3.2.2 자동 위험축소가 비활성화되어 있습니다")
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
                "V3.2.2 위험축소 주문 제출에 실패했습니다",
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
                "V3.2.2 위험축소 주문이 전량 완료되지 않았습니다",
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
                f"{symbol} 위험축소가 완료되지 않아 SAFE_MODE로 전환했습니다 "
                f"({receipt.filled_quantity}/{receipt.quantity}주, {receipt.status})"
            )
        return receipt

    def _enter_symbol_safe_mode(self, symbol: str, reason: str) -> None:
        # QQQ has no virtual JDSS position row. TQQQ/SOXL retain the existing
        # position-state SAFE_MODE gate; QQQ uses a portfolio-level system flag.
        if symbol not in self.config.enabled_symbols:
            self.repository.set_system_value("v322_portfolio_safe_mode", "1")
            return
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
