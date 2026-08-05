from __future__ import annotations

from decimal import Decimal

from jd_holdings.core.models import OrderReceipt, OrderRequest

from .broker import Broker
from .database import SQLiteRepository
from .order_manager import OrderManager, build_client_order_id


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

    def cancel_open_tp_orders(self, symbol: str) -> list[str]:
        filled_client_ids: list[str] = []
        for order in self.repository.open_orders(symbol):
            if order["purpose"] not in {"TP1", "TP2"}:
                continue
            broker_id = order.get("broker_order_id")
            if not broker_id:
                continue
            try:
                self.broker.cancel_order(str(broker_id))
            except Exception:
                pass
            receipt = self.order_manager.refresh_order(order["client_order_id"])
            if receipt.status not in {"FILLED", "CANCELED", "REJECTED", "REPLACED"}:
                raise RuntimeError(f"{symbol} {order['purpose']} 주문 취소가 확정되지 않았습니다")
            if receipt.filled_quantity > 0:
                filled_client_ids.append(str(order["client_order_id"]))
            elif not self.repository.mark_order_applied(str(order["client_order_id"])):
                # 이미 반영된 종료 주문은 정상적인 멱등 재호출입니다.
                continue
        return filled_client_ids
