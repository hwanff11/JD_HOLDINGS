from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from jd_holdings.config import StrategyConfig
from jd_holdings.core.enums import PositionState

from .broker import Broker
from .database import SQLiteRepository
from .order_manager import OrderManager
from .position_manager import PositionManager
from .tp_manager import TakeProfitManager

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
    ) -> None:
        self.config = config
        self.repository = repository
        self.broker = broker
        self.order_manager = order_manager
        self.position_manager = position_manager
        self.tp_manager = tp_manager

    def run_once(self, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(UTC)
        events: list[str] = []
        tp_recovery_symbols: set[str] = set()
        for local in self.repository.open_orders():
            if not local.get("broker_order_id"):
                continue
            receipt = self.order_manager.refresh_order(local["client_order_id"])
            purpose = str(local["purpose"])
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
                        self.tp_manager.place_orders(local["symbol"])
                        events.append(f"{local['symbol']} {purpose} 체결 반영")
                    else:
                        self._restore_unfilled_buy(local)
                        events.append(f"{local['symbol']} {purpose} 미체결 종료")
            elif purpose in {"TP1", "TP2"}:
                if receipt.filled_quantity > 0:
                    self.position_manager.apply_sell_fill(local["client_order_id"])
                    events.append(
                        f"{local['symbol']} {purpose} {receipt.filled_quantity}주 체결 반영"
                    )
                if receipt.status in {"CANCELED", "REJECTED"}:
                    plan = self.repository.active_tp_plan(local["symbol"])
                    if plan and receipt.filled_quantity < int(
                        plan[f"{purpose.lower()}_target_qty"]
                    ):
                        tp_recovery_symbols.add(str(local["symbol"]))
        for symbol in tp_recovery_symbols:
            settled = self.tp_manager.cancel_open_tp_orders(symbol)
            for client_order_id in settled:
                self.position_manager.apply_sell_fill(client_order_id)
            position = self.repository.get_position(symbol)
            if position.quantity > 0 and self.repository.active_tp_plan(symbol):
                self.tp_manager.place_orders(symbol)
                events.append(f"{symbol} 익절 주문 자동 복구")
        return events

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
