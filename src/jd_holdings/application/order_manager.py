from __future__ import annotations

import hashlib
import logging
from decimal import Decimal

from jd_holdings.core.models import OrderReceipt, OrderRequest
from jd_holdings.infrastructure.toss_client import TossApiError, receipt_from_order
from jd_holdings.settings import RuntimeSettings

from .broker import Broker
from .database import SQLiteRepository
from .managed_account import reserve_buy_order_with_managed_cash

LOGGER = logging.getLogger(__name__)


def build_client_order_id(
    *, symbol: str, purpose: str, signal_id: int | None, unique_context: str
) -> str:
    source = f"JDSS|{symbol}|{purpose}|{signal_id}|{unique_context}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    purpose_short = purpose.replace("ENTRY_", "E")[:5]
    return f"JDSS-{symbol[:5]}-{purpose_short}-{digest}"[:36]


class OrderManager:
    def __init__(
        self,
        repository: SQLiteRepository,
        broker: Broker,
        settings: RuntimeSettings,
    ) -> None:
        self.repository = repository
        self.broker = broker
        self.settings = settings

    def submit(
        self,
        request: OrderRequest,
        *,
        cycle_id: str | None,
    ) -> OrderReceipt:
        existing = self.repository.get_order_by_client_id(request.client_order_id)
        if existing:
            if existing.get("broker_order_id"):
                try:
                    return receipt_from_order(
                        self.broker.get_order(str(existing["broker_order_id"])),
                        request.client_order_id,
                    )
                except Exception as exc:
                    LOGGER.warning("주문 상태 최신화 중 오류 발생: %s", exc)
            return OrderReceipt(
                client_order_id=request.client_order_id,
                broker_order_id=str(existing.get("broker_order_id") or ""),
                status=str(existing["status"]),
                quantity=int(existing["qty"]),
                filled_quantity=int(existing["filled_qty"]),
                average_fill_price=Decimal(existing["average_fill_price"])
                if existing.get("average_fill_price")
                else None,
            )

        if request.side.upper() == "BUY":
            if request.price is None:
                raise RuntimeError("JDSS 매수는 관리현금 검증 가능한 지정가만 허용합니다")
            reserved = reserve_buy_order_with_managed_cash(
                self.repository.config,
                self.repository,
                self.broker,
                client_order_id=request.client_order_id,
                signal_id=request.signal_id,
                cycle_id=cycle_id,
                symbol=request.symbol,
                order_type=request.order_type,
                price=request.price,
                quantity=request.quantity,
                purpose=request.purpose,
            )
        else:
            reserved = self.repository.reserve_order(
                client_order_id=request.client_order_id,
                signal_id=request.signal_id,
                cycle_id=cycle_id,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                price=request.price,
                quantity=request.quantity,
                purpose=request.purpose,
            )
        if not reserved:
            raise RuntimeError("주문 멱등키 예약에 실패했습니다")
        if self.settings.trading_mode == "live":
            self.settings.require_live_trading()
        try:
            receipt = self.broker.place_order(request)
        except TossApiError as exc:
            status = "UNKNOWN" if exc.retryable else "REJECTED"
            self.repository.update_order(
                request.client_order_id,
                status=status,
                raw={
                    "error": str(exc),
                    "code": exc.code,
                    "request_id": exc.request_id,
                },
            )
            raise
        except Exception as exc:
            self.repository.update_order(
                request.client_order_id,
                status="UNKNOWN",
                raw={"error": type(exc).__name__, "message": str(exc)},
            )
            raise
        self.repository.update_order(
            request.client_order_id,
            status=receipt.status,
            broker_order_id=receipt.broker_order_id,
            filled_qty=receipt.filled_quantity,
            average_fill_price=receipt.average_fill_price,
            raw=receipt.raw,
        )
        return receipt

    def refresh_order(self, client_order_id: str) -> OrderReceipt:
        local = self.repository.get_order_by_client_id(client_order_id)
        if not local or not local.get("broker_order_id"):
            raise KeyError(client_order_id)
        receipt = receipt_from_order(
            self.broker.get_order(str(local["broker_order_id"])), client_order_id
        )
        self.repository.update_order(
            client_order_id,
            status=receipt.status,
            filled_qty=receipt.filled_quantity,
            average_fill_price=receipt.average_fill_price,
            raw=receipt.raw,
        )
        return receipt
