from __future__ import annotations

import logging
from decimal import Decimal

from jd_holdings.core.enums import PositionState
from jd_holdings.core.models import OrderReceipt, OrderRequest
from jd_holdings.core.take_profit import ceil_to_tick

from .broker import Broker
from .database import SQLiteRepository
from .order_manager import OrderManager, build_client_order_id

LOGGER = logging.getLogger(__name__)
TP_PURPOSES = {"TP1", "TP2", "REMAINDER_EXIT"}


class TakeProfitManager:
    def __init__(
        self,
        repository: SQLiteRepository,
        broker: Broker,
        order_manager: OrderManager,
    ) -> None:
        self.repository = repository
        self.broker = broker
        self.order_manager = order_manager

    def place_orders(self, symbol: str) -> list[OrderReceipt]:
        symbol = symbol.upper()
        position = self.repository.get_position(symbol)
        plan = self.repository.active_tp_plan(symbol)
        if not plan:
            raise RuntimeError(f"{symbol} 활성 TP 계획이 없습니다")
        revision = self.repository.bump_tp_revision(int(plan["tp_plan_id"]))
        receipts: list[OrderReceipt] = []
        legs = (
            (
                "TP1",
                Decimal(str(plan["tp1_price"])),
                int(plan["tp1_target_qty"]) - int(plan["tp1_filled_qty"]),
            ),
            (
                "TP2",
                Decimal(str(plan["tp2_price"])),
                int(plan["tp2_target_qty"]) - int(plan["tp2_filled_qty"]),
            ),
        )
        for purpose, price, quantity in legs:
            if quantity <= 0:
                continue
            client_order_id = build_client_order_id(
                symbol=symbol,
                purpose=purpose,
                signal_id=None,
                unique_context=f"tp{plan['tp_plan_id']}-r{revision}",
            )
            request = OrderRequest(
                client_order_id=client_order_id,
                symbol=symbol,
                side="SELL",
                order_type="LIMIT",
                quantity=quantity,
                price=price,
                purpose=purpose,
            )
            receipts.append(self.order_manager.submit(request, cycle_id=position.cycle_id))
        return receipts

    def place_remainder_exit(self, symbol: str) -> OrderReceipt:
        symbol = symbol.upper()
        position = self.repository.get_position(symbol)
        plan = self.repository.active_tp_plan(symbol)
        rule = self.repository.config.take_profit.remainder_exit
        if not rule.enabled:
            raise RuntimeError("잔여청산 규칙이 비활성화되어 있습니다")
        if not plan:
            raise RuntimeError(f"{symbol} 활성 TP 계획이 없습니다")
        if position.state != PositionState.PARTIAL_TP_1 or position.quantity <= 0:
            raise RuntimeError(f"{symbol}은 TP1 이후 잔여 보유 상태가 아닙니다")

        revision = self.repository.bump_tp_revision(int(plan["tp_plan_id"]))
        price = ceil_to_tick(
            position.average_price * (Decimal("1") + rule.target_from_avg)
        )
        client_order_id = build_client_order_id(
            symbol=symbol,
            purpose="REMAINDER_EXIT",
            signal_id=None,
            unique_context=f"tp{plan['tp_plan_id']}-r{revision}-remainder",
        )
        request = OrderRequest(
            client_order_id=client_order_id,
            symbol=symbol,
            side="SELL",
            order_type="LIMIT",
            quantity=position.quantity,
            price=price,
            purpose="REMAINDER_EXIT",
        )
        return self.order_manager.submit(request, cycle_id=position.cycle_id)

    def cancel_open_tp_orders(self, symbol: str) -> list[str]:
        filled_client_ids: list[str] = []
        for order in self.repository.open_orders(symbol):
            if order["purpose"] not in TP_PURPOSES:
                continue
            broker_id = order.get("broker_order_id")
            if not broker_id:
                continue
            try:
                self.broker.cancel_order(str(broker_id))
            except Exception as exc:
                LOGGER.warning("기존 TP 주문 취소 중 오류 발생: %s", exc)
            receipt = self.order_manager.refresh_order(order["client_order_id"])
            if receipt.status not in {"FILLED", "CANCELED", "REJECTED", "REPLACED"}:
                raise RuntimeError(f"{symbol} {order['purpose']} 주문 취소가 확정되지 않았습니다")
            if receipt.filled_quantity > 0:
                filled_client_ids.append(str(order["client_order_id"]))
            elif not self.repository.mark_order_applied(str(order["client_order_id"])):
                continue
        return filled_client_ids
