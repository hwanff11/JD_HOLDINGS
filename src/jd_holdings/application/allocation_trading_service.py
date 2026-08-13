from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from decimal import Decimal

from jd_holdings.core.enums import DecisionType, PositionState
from jd_holdings.core.execution import calculate_limit_price, calculate_order_quantity
from jd_holdings.core.models import OrderReceipt, OrderRequest
from jd_holdings.core.twin_core import target_quantity
from jd_holdings.infrastructure.market_clock import session_is_allowed

from .database import ApprovalError
from .managed_account import (
    available_managed_cash,
    committed_core_buy_quantity,
    managed_equity,
)
from .order_manager import build_client_order_id
from .trading_service import QuoteChangedError, ReviewQuote, TradingService

LOGGER = logging.getLogger(__name__)
TERMINAL_STATUSES = {"FILLED", "CANCELED", "REJECTED", "REPLACED"}
_execution_approval_id: ContextVar[int | None] = ContextVar(
    "jdss_execution_approval_id", default=None
)


class AllocationTradingService(TradingService):
    """Production trading service with the V3.2.2 operational safety layer."""

    def execute(self, approval_id: int, token: str, *, now=None) -> OrderReceipt:
        marker = _execution_approval_id.set(approval_id)
        try:
            return super().execute(approval_id, token, now=now)
        except Exception as exc:
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

    def _build_quote(
        self,
        signal_id: int,
        *,
        now: datetime | None,
        allow_cash_release: bool,
    ) -> ReviewQuote:
        signal = self.repository.get_signal(signal_id)
        if str(signal["action"]) != DecisionType.CORE_REBALANCE_BUY.value:
            return super()._build_quote(
                signal_id, now=now, allow_cash_release=allow_cash_release
            )

        current = now or datetime.now(UTC)
        signal = self._active_signal(signal_id, now=current)
        session = self.market_clock.classify_session(current)
        if not session_is_allowed(session, self.config):
            raise ApprovalError(f"현재 주문 허용 세션이 아닙니다: {session}")
        ceiling = Decimal(str(signal["max_chase_price"]))
        current_price = self.broker.get_price(str(signal["symbol"]))
        limit = calculate_limit_price(current_price, ceiling, self.config)
        budget = Decimal(str(signal["planned_budget"]))
        signal_close = Decimal(str(signal["signal_close"]))
        planned_per_share = (
            signal_close
            * (Decimal("1") + self.config.global_.buy_limit_buffer)
            * (Decimal("1") + self.config.global_.buy_fee)
        )
        planned_quantity = int(budget / planned_per_share) if planned_per_share > 0 else 0
        core = self.repository.get_core_position(str(signal["symbol"]))
        target_weight = Decimal(str(core["target_weight"]))
        current_target = target_quantity(
            managed_equity(self.config, self.repository, self.broker),
            target_weight,
            limit,
            self.config.global_.buy_fee,
        )
        committed_quantity = committed_core_buy_quantity(
            self.repository, str(signal["symbol"])
        )
        remaining_target = max(
            0,
            current_target - int(core["qty"]) - committed_quantity,
        )
        maximum_quantity = min(planned_quantity, remaining_target)
        quantity = calculate_order_quantity(
            budget,
            limit,
            self.config.global_.buy_fee,
            maximum_quantity=maximum_quantity,
        )
        if quantity < 1:
            raise ApprovalError("V3.2.2 목표수량이 이미 충족되었거나 매수수량이 0주입니다")
        total = Decimal(quantity) * limit * (Decimal("1") + self.config.global_.buy_fee)
        if self.idle_cash_manager is not None and allow_cash_release:
            self.idle_cash_manager.ensure_buying_power(
                total,
                signal_id=signal_id,
                expires_at=datetime.fromisoformat(str(signal["valid_until"])),
            )
        reserved = self.repository.reserved_cash_release_amount(signal_id)
        buying_power = available_managed_cash(
            self.config,
            self.repository,
            self.broker,
            additional_reservation=reserved,
        )
        if total > buying_power:
            raise ApprovalError("JDSS HWM75 위험예산 내 매수가능금액이 부족합니다")
        return ReviewQuote(
            signal_id=signal_id,
            symbol=str(signal["symbol"]),
            session=session,
            current_price=current_price,
            execution_ceiling=ceiling,
            limit_price=limit,
            quantity=quantity,
            planned_budget=budget,
            estimated_fee=Decimal(quantity) * limit * self.config.global_.buy_fee,
        )

    def _signal_gate_failure(self, signal: dict) -> str | None:
        if str(signal["action"]) != DecisionType.CORE_REBALANCE_BUY.value:
            return "V322_DIRECT_SIGNAL_DISABLED"
        if (
            signal["strategy_version"] != self.config.version
            or signal["config_version"] != self.config.config_version
        ):
            return "SIGNAL_VERSION_MISMATCH"
        if self.repository.get_system_value("v322_portfolio_safe_mode") == "1":
            return "V322_PORTFOLIO_SAFE_MODE"
        symbol = str(signal["symbol"])
        if symbol in self.config.enabled_symbols:
            position = self.repository.get_position(symbol)
            if position.state == PositionState.SAFE_MODE:
                return "SIGNAL_SAFE_MODE"
        core = self.repository.get_core_position(symbol)
        if not bool(core["trend_active"]):
            return "V322_TARGET_OFF"
        return None

    def _execute_core_buy(self, signal: dict, quote: ReviewQuote) -> OrderReceipt:
        if self.order_manager.settings.trading_mode != "dry_run":
            raise RuntimeError("JDSS V3.2.2 allocation 매수는 live 모드가 잠겨 있습니다")

        symbol = str(signal["symbol"])
        signal_id = int(signal["signal_id"])
        core = self.repository.get_core_position(symbol)
        if not bool(core["trend_active"]):
            raise QuoteChangedError("V3.2.2 목표비중이 0으로 바뀌어 승인을 취소했습니다")

        approval_id = _execution_approval_id.get()
        unique_context = f"v322-v{core['version']}-a{approval_id or 'direct'}"
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
                    "V3.2.2 매수 주문의 브로커 처리 결과를 확인할 수 없습니다",
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
                    "V3.2.2 매수 주문 제출 실패로 동일 신호를 재승인 가능 상태로 되돌렸습니다",
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
            self._set_cash_intent_status(signal_id, "CANCELED")
            return receipt

        if receipt.status in TERMINAL_STATUSES and remaining > 0:
            self._reopen_core_signal(signal_id, "CORE_BUY_REAPPROVAL_REQUIRED")
            self.repository.log_event(
                "WARNING",
                "CORE_BUY_INCOMPLETE",
                "V3.2.2 매수가 전량 체결되지 않아 잔여 목표를 다시 승인해야 합니다",
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
        if symbol not in self.config.enabled_symbols:
            self.repository.set_system_value("v322_portfolio_safe_mode", "1")
            self.repository.log_event(
                "SAFE_MODE",
                reason,
                "QQQ allocation 안전모드 진입",
                symbol=symbol,
            )
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
