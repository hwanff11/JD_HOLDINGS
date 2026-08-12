from __future__ import annotations

import logging
from contextvars import ContextVar
from decimal import Decimal

from jd_holdings.core.enums import PositionState
from jd_holdings.core.models import OrderReceipt, OrderRequest

from .order_manager import build_client_order_id
from .trading_service import QuoteChangedError, ReviewQuote, TradingService

LOGGER = logging.getLogger(__name__)
TERMINAL_STATUSES = {"FILLED", "CANCELED", "REJECTED", "REPLACED"}
_execution_approval_id: ContextVar[int | None] = ContextVar(
    "jdss_execution_approval_id", default=None
)


class FinalTradingService(TradingService):
    """Production trading service with the final V3.1 operational safety layer."""

    def execute(self, approval_id: int, token: str, *, now=None) -> OrderReceipt:
        marker = _execution_approval_id.set(approval_id)
        try:
            return super().execute(approval_id, token, now=now)
        except Exception as exc:
            # Expected approval/quote errors are still useful in the persistent event log.
            self.repository.log_event(
                "WARNING",
                "ORDER_EXECUTION_ERROR",
                str(exc),
                context={
                    "approval_id": approval_id,
                    "exception": type(exc).__name__,
                },
            )
            raise
        finally:
            _execution_approval_id.reset(marker)

    def _execute_core_buy(self, signal: dict, quote: ReviewQuote) -> OrderReceipt:
        if self.order_manager.settings.trading_mode != "dry_run":
            raise RuntimeError("JDSS V3 코어 매수는 live 모드가 잠겨 있습니다")

        symbol = str(signal["symbol"])
        signal_id = int(signal["signal_id"])
        core = self.repository.get_core_position(symbol)
        if not bool(core["trend_active"]):
            raise QuoteChangedError("월간 코어 추세가 꺼져 승인을 취소했습니다")

        approval_id = _execution_approval_id.get()
        unique_context = f"core-v{core['version']}-a{approval_id or 'direct'}"
        client_order_id = build_client_order_id(
            symbol=symbol,
            purpose="CORE_REBALANCE_BUY",
            signal_id=signal_id,
            unique_context=unique_context,
        )
        request = OrderRequest(
            client_order_id=client_order_id,
            symbol=symbol,
            side="BUY",
            order_type="LIMIT",
            quantity=quote.quantity,
            price=quote.limit_price,
            purpose="CORE_REBALANCE_BUY",
            signal_id=signal_id,
        )

        try:
            receipt = self.order_manager.submit(request, cycle_id=None)
        except Exception as exc:
            local = self.repository.get_order_by_client_id(client_order_id)
            self._set_cash_intent_status(signal_id, "CANCELED")
            if local is not None and str(local["status"]) == "UNKNOWN":
                self._enter_symbol_safe_mode(symbol, "CORE_ORDER_SUBMISSION_UNKNOWN")
                self.repository.mark_signal(
                    signal_id,
                    status="UNKNOWN",
                    processed=True,
                    reason="CORE_ORDER_SUBMISSION_UNKNOWN",
                )
                self.repository.log_event(
                    "SAFE_MODE",
                    "CORE_ORDER_SUBMISSION_UNKNOWN",
                    "코어 매수 주문의 브로커 처리 결과를 확인할 수 없습니다",
                    symbol=symbol,
                    context={
                        "signal_id": signal_id,
                        "client_order_id": client_order_id,
                        "error": str(exc),
                    },
                )
            else:
                self._reopen_core_signal(signal_id, "CORE_BUY_SUBMISSION_FAILED")
                self.repository.log_event(
                    "WARNING",
                    "CORE_BUY_SUBMISSION_FAILED",
                    "코어 매수 주문 제출에 실패해 동일 신호를 재승인 가능 상태로 되돌렸습니다",
                    symbol=symbol,
                    context={
                        "signal_id": signal_id,
                        "client_order_id": client_order_id,
                        "error": str(exc),
                    },
                )
            raise

        if receipt.filled_quantity > 0:
            self.repository.apply_core_fill(client_order_id)

        remaining = max(0, receipt.quantity - receipt.filled_quantity)
        if receipt.status == "UNKNOWN":
            self._enter_symbol_safe_mode(symbol, "CORE_ORDER_STATUS_UNKNOWN")
            self.repository.mark_signal(
                signal_id,
                status="UNKNOWN",
                processed=True,
                reason="CORE_ORDER_STATUS_UNKNOWN",
            )
            self.repository.log_event(
                "SAFE_MODE",
                "CORE_ORDER_STATUS_UNKNOWN",
                "코어 매수 주문 상태가 UNKNOWN입니다",
                symbol=symbol,
                context={
                    "signal_id": signal_id,
                    "client_order_id": client_order_id,
                    "filled_quantity": receipt.filled_quantity,
                    "quantity": receipt.quantity,
                },
            )
            self._set_cash_intent_status(signal_id, "CANCELED")
            return receipt

        if receipt.status in TERMINAL_STATUSES and remaining > 0:
            self._reopen_core_signal(signal_id, "CORE_BUY_REAPPROVAL_REQUIRED")
            self.repository.log_event(
                "WARNING",
                "CORE_BUY_INCOMPLETE",
                "코어 매수가 전량 체결되지 않아 잔여 목표를 다시 승인해야 합니다",
                symbol=symbol,
                context={
                    "signal_id": signal_id,
                    "client_order_id": client_order_id,
                    "status": receipt.status,
                    "filled_quantity": receipt.filled_quantity,
                    "quantity": receipt.quantity,
                    "remaining_quantity": remaining,
                },
            )
            self._set_cash_intent_status(signal_id, "CANCELED")
            return receipt

        self.repository.mark_signal(signal_id, status="PROCESSED", processed=True)
        self._set_cash_intent_status(signal_id, "COMPLETED")
        return receipt

    def _reopen_core_signal(self, signal_id: int, reason: str) -> None:
        self.repository.mark_signal(
            signal_id,
            status="ACTIVE",
            processed=False,
            reason=reason,
        )

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
