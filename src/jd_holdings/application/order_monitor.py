from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from jd_holdings.config import StrategyConfig
from jd_holdings.core.enums import PositionState
from jd_holdings.core.remainder_exit import remainder_exit_due
from jd_holdings.infrastructure.market_clock import MarketClock

from .broker import Broker
from .database import SQLiteRepository
from .order_manager import OrderManager
from .position_manager import PositionManager
from .tp_manager import TP_PURPOSES, TakeProfitManager

TERMINAL_STATUSES = {"FILLED", "CANCELED", "REJECTED", "REPLACED"}
BASE_STATE_BY_PURPOSE = {
    "ENTRY_1": PositionState.EMPTY,
    "ENTRY_2": PositionState.HOLDING_1ST,
    "ENTRY_3": PositionState.HOLDING_2ND,
    "ENTRY_4": PositionState.HOLDING_3RD,
    "REBUY": PositionState.PARTIAL_TP_1,
}


class OrderMonitor:
    def __init__(
        self,
        config: StrategyConfig,
        repository: SQLiteRepository,
        broker: Broker,
        order_manager: OrderManager,
        position_manager: PositionManager,
        tp_manager: TakeProfitManager,
        market_clock: MarketClock | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.broker = broker
        self.order_manager = order_manager
        self.position_manager = position_manager
        self.tp_manager = tp_manager
        self.market_clock = market_clock or MarketClock()

    def run_once(self, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(UTC)
        events: list[str] = []
        tp_recovery_symbols: set[str] = set()
        remainder_recovery_symbols: set[str] = set()
        tp1_completed_symbols: set[str] = set()

        for local in self.repository.open_orders():
            if not local.get("broker_order_id"):
                continue
            receipt = self.order_manager.refresh_order(local["client_order_id"])
            purpose = str(local["purpose"])
            symbol = str(local["symbol"])
            if purpose in BASE_STATE_BY_PURPOSE:
                created = datetime.fromisoformat(local["created_at"])
                elapsed = (current - created).total_seconds()
                if (
                    receipt.status == "PARTIAL_FILLED"
                    and elapsed >= self.config.global_.buy_fill_timeout_seconds
                ):
                    self.broker.cancel_order(receipt.broker_order_id)
                    receipt = self.order_manager.refresh_order(local["client_order_id"])
                if receipt.status in TERMINAL_STATUSES:
                    if receipt.filled_quantity > 0:
                        signal = self.repository.get_signal(int(local["signal_id"]))
                        self.position_manager.apply_buy_fill(
                            local["client_order_id"],
                            atr_pct=Decimal(str(signal["atr_pct"])),
                        )
                        self.tp_manager.place_orders(symbol)
                        events.append(f"{symbol} {purpose} 체결 반영")
                    else:
                        self._restore_unfilled_buy(local)
                        events.append(f"{symbol} {purpose} 미체결 종료")
                continue

            if purpose not in TP_PURPOSES:
                continue
            if receipt.filled_quantity > 0:
                self.position_manager.apply_sell_fill(local["client_order_id"])
                events.append(f"{symbol} {purpose} {receipt.filled_quantity}주 체결 반영")
                if (
                    purpose == "TP1"
                    and self.repository.get_position(symbol).state == PositionState.PARTIAL_TP_1
                ):
                    tp1_completed_symbols.add(symbol)
            if receipt.status in {"CANCELED", "REJECTED"}:
                if purpose == "REMAINDER_EXIT":
                    remainder_recovery_symbols.add(symbol)
                else:
                    plan = self.repository.active_tp_plan(symbol)
                    target_key = f"{purpose.lower()}_target_qty"
                    if plan and receipt.filled_quantity < int(plan[target_key]):
                        tp_recovery_symbols.add(symbol)

        for symbol in tp1_completed_symbols:
            self._reset_tp2_clock_after_tp1(symbol, events)

        for symbol in tp_recovery_symbols - tp1_completed_symbols:
            self._recover_standard_tp(symbol, events)

        for symbol in remainder_recovery_symbols:
            self._recover_remainder_exit(symbol, events)

        self._switch_due_remainder_exits(current, events)
        return events

    def _reset_tp2_clock_after_tp1(self, symbol: str, events: list[str]) -> None:
        settled = self.tp_manager.cancel_open_tp_orders(symbol)
        for client_order_id in settled:
            self.position_manager.apply_sell_fill(client_order_id)
        position = self.repository.get_position(symbol)
        if (
            position.state == PositionState.PARTIAL_TP_1
            and position.quantity > 0
            and self.repository.active_tp_plan(symbol)
        ):
            self.tp_manager.place_orders(symbol)
            events.append(f"{symbol} TP1 완료 후 TP2 기준시점 재설정")

    def _recover_standard_tp(self, symbol: str, events: list[str]) -> None:
        settled = self.tp_manager.cancel_open_tp_orders(symbol)
        for client_order_id in settled:
            self.position_manager.apply_sell_fill(client_order_id)
        position = self.repository.get_position(symbol)
        if position.quantity > 0 and self.repository.active_tp_plan(symbol):
            self.tp_manager.place_orders(symbol)
            events.append(f"{symbol} 익절 주문 자동 복구")

    def _recover_remainder_exit(self, symbol: str, events: list[str]) -> None:
        settled = self.tp_manager.cancel_open_tp_orders(symbol)
        for client_order_id in settled:
            self.position_manager.apply_sell_fill(client_order_id)
        position = self.repository.get_position(symbol)
        if (
            position.state == PositionState.PARTIAL_TP_1
            and position.quantity > 0
            and self.repository.active_tp_plan(symbol)
        ):
            self.tp_manager.place_remainder_exit(symbol)
            events.append(f"{symbol} 잔여청산 주문 자동 복구")

    def _switch_due_remainder_exits(
        self,
        current: datetime,
        events: list[str],
    ) -> None:
        rule = self.config.take_profit.remainder_exit
        if not rule.enabled:
            return
        for symbol in self.config.enabled_symbols:
            position = self.repository.get_position(symbol)
            if position.state != PositionState.PARTIAL_TP_1 or position.quantity <= 0:
                continue
            open_orders = self.repository.open_orders(symbol)
            if any(order["purpose"] == "REMAINDER_EXIT" for order in open_orders):
                continue
            tp2_orders = [order for order in open_orders if order["purpose"] == "TP2"]
            if not tp2_orders:
                continue
            tp2_created = max(datetime.fromisoformat(order["created_at"]) for order in tp2_orders)
            elapsed_sessions = self.market_clock.completed_sessions_since(tp2_created, current)
            if not remainder_exit_due(elapsed_sessions, rule):
                continue

            settled = self.tp_manager.cancel_open_tp_orders(symbol)
            for client_order_id in settled:
                self.position_manager.apply_sell_fill(client_order_id)
            position = self.repository.get_position(symbol)
            if (
                position.state == PositionState.PARTIAL_TP_1
                and position.quantity > 0
                and self.repository.active_tp_plan(symbol)
            ):
                receipt = self.tp_manager.place_remainder_exit(symbol)
                target_pct = rule.target_from_avg * Decimal("100")
                events.append(
                    f"{symbol} TP1 후 {elapsed_sessions}거래일 경과: "
                    f"잔량 +{target_pct.normalize()}% 회수주문 전환 ({receipt.status})"
                )

    def _restore_unfilled_buy(self, order: dict) -> None:
        position = self.repository.get_position(order["symbol"])
        base = BASE_STATE_BY_PURPOSE[order["purpose"]]
        self.repository.transition_position(
            order["symbol"],
            expected_state=position.state,
            new_state=base,
            reason_code="BUY_ORDER_ZERO_FILL",
            expected_version=position.version,
        )
        self.repository.mark_order_applied(order["client_order_id"])
        if base != PositionState.EMPTY and self.repository.active_tp_plan(order["symbol"]):
            self.tp_manager.place_orders(order["symbol"])
