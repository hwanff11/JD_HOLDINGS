from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from jd_holdings.config import StrategyConfig
from jd_holdings.core.enums import ApprovalStage, DecisionType, PositionState
from jd_holdings.core.execution import (
    calculate_execution_price_ceiling,
    calculate_limit_price,
    calculate_order_quantity,
)
from jd_holdings.core.models import OrderReceipt, OrderRequest
from jd_holdings.infrastructure.market_clock import MarketClock, session_is_allowed

from .broker import Broker
from .database import ApprovalError, SQLiteRepository
from .idle_cash_manager import IdleCashManager
from .order_manager import OrderManager, build_client_order_id
from .position_manager import PositionManager
from .tp_manager import TakeProfitManager

BASE_STATE_BY_ACTION_STAGE = {
    (DecisionType.FIRST_ENTRY_CANDIDATE.value, 1): PositionState.EMPTY,
    (DecisionType.ADD_ENTRY_CANDIDATE.value, 2): PositionState.HOLDING_1ST,
    (DecisionType.ADD_ENTRY_CANDIDATE.value, 3): PositionState.HOLDING_2ND,
    (DecisionType.ADD_ENTRY_CANDIDATE.value, 4): PositionState.HOLDING_3RD,
    (DecisionType.REBUY_CANDIDATE.value, None): PositionState.PARTIAL_TP_1,
}

WAITING_FILL_BY_ACTION_STAGE = {
    (DecisionType.FIRST_ENTRY_CANDIDATE.value, 1): PositionState.WAITING_1ST_FILL,
    (DecisionType.ADD_ENTRY_CANDIDATE.value, 2): PositionState.WAITING_2ND_FILL,
    (DecisionType.ADD_ENTRY_CANDIDATE.value, 3): PositionState.WAITING_3RD_FILL,
    (DecisionType.ADD_ENTRY_CANDIDATE.value, 4): PositionState.WAITING_4TH_FILL,
    (DecisionType.REBUY_CANDIDATE.value, None): PositionState.WAITING_REBUY_FILL,
}


@dataclass(frozen=True)
class ReviewQuote:
    signal_id: int
    symbol: str
    session: str
    current_price: Decimal
    execution_ceiling: Decimal
    limit_price: Decimal
    quantity: int
    planned_budget: Decimal
    estimated_fee: Decimal
    execution_approval_id: int | None = None
    execution_token: str | None = None


class QuoteChangedError(RuntimeError):
    pass


class TradingService:
    def __init__(
        self,
        config: StrategyConfig,
        repository: SQLiteRepository,
        broker: Broker,
        order_manager: OrderManager,
        position_manager: PositionManager,
        tp_manager: TakeProfitManager,
        market_clock: MarketClock | None = None,
        idle_cash_manager: IdleCashManager | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.broker = broker
        self.order_manager = order_manager
        self.position_manager = position_manager
        self.tp_manager = tp_manager
        self.market_clock = market_clock or MarketClock()
        self.idle_cash_manager = idle_cash_manager

    def create_review_approval(self, signal_id: int) -> tuple[int, str]:
        signal = self._active_signal(signal_id)
        return self.repository.create_approval(
            int(signal["signal_id"]),
            ApprovalStage.REVIEW,
            timedelta(minutes=self.config.global_.review_token_ttl_minutes),
        )

    def active_signals(self, symbol: str | None = None) -> list[dict]:
        """Return only signals that are still executable under the active contract."""
        self.repository.expire_stale_signals()
        eligible: list[dict] = []
        for signal in self.repository.active_signals(symbol):
            try:
                eligible.append(self._active_signal(int(signal["signal_id"])))
            except ApprovalError:
                continue
        return eligible

    def consume_review(
        self,
        approval_id: int,
        token: str,
        *,
        now: datetime | None = None,
    ) -> ReviewQuote:
        signal_id, _ = self.repository.consume_approval(approval_id, token, ApprovalStage.REVIEW)
        quote = self._build_quote(signal_id, now=now)
        payload = {
            "current_price": str(quote.current_price),
            "execution_ceiling": str(quote.execution_ceiling),
            "limit_price": str(quote.limit_price),
            "quantity": quote.quantity,
            "session": quote.session,
        }
        execution_id, execution_token = self.repository.create_approval(
            signal_id,
            ApprovalStage.EXECUTION,
            timedelta(seconds=self.config.global_.execution_token_ttl_seconds),
            payload,
        )
        return ReviewQuote(
            **{
                **quote.__dict__,
                "execution_approval_id": execution_id,
                "execution_token": execution_token,
            }
        )

    def execute(
        self,
        approval_id: int,
        token: str,
        *,
        now: datetime | None = None,
    ) -> OrderReceipt:
        signal_id, approved = self.repository.consume_approval(
            approval_id, token, ApprovalStage.EXECUTION
        )
        quote = self._build_quote(signal_id, now=now)
        comparison = {
            "execution_ceiling": str(quote.execution_ceiling),
            "limit_price": str(quote.limit_price),
            "quantity": quote.quantity,
            "session": quote.session,
        }
        approved_comparison = {
            key: approved[key]
            for key in ("execution_ceiling", "limit_price", "quantity", "session")
        }
        if comparison != approved_comparison:
            raise QuoteChangedError("가격 또는 수량이 바뀌어 새로운 최종 확인이 필요합니다")
        signal = self._active_signal(signal_id)
        action = str(signal["action"])
        target_stage = int(signal["target_stage"]) if signal["target_stage"] else None
        position = self.repository.get_position(signal["symbol"])
        expected = BASE_STATE_BY_ACTION_STAGE[(action, target_stage)]
        if position.state != expected:
            raise RuntimeError("승인 후 포지션 상태가 변경되었습니다")
        settled_tp_orders = self.tp_manager.cancel_open_tp_orders(signal["symbol"])
        for client_order_id in settled_tp_orders:
            self.position_manager.apply_sell_fill(client_order_id)
        position = self.repository.get_position(signal["symbol"])
        if position.state != expected:
            raise QuoteChangedError("익절 체결로 포지션이 바뀌어 매수 승인을 취소했습니다")
        waiting = WAITING_FILL_BY_ACTION_STAGE[(action, target_stage)]
        purpose = self._purpose(action, target_stage)
        client_order_id = build_client_order_id(
            symbol=signal["symbol"],
            purpose=purpose,
            signal_id=signal_id,
            unique_context=f"v{position.version}",
        )
        request = OrderRequest(
            client_order_id=client_order_id,
            symbol=signal["symbol"],
            side="BUY",
            order_type="LIMIT",
            quantity=quote.quantity,
            price=quote.limit_price,
            purpose=purpose,
            signal_id=signal_id,
        )
        self.repository.transition_position(
            signal["symbol"],
            expected_state=expected,
            new_state=waiting,
            reason_code="ORDER_SUBMISSION_STARTED",
            expected_version=position.version,
        )
        try:
            receipt = self.order_manager.submit(request, cycle_id=position.cycle_id)
        except Exception as exc:
            local = self.repository.get_order_by_client_id(client_order_id)
            if local is None or local["status"] == "REJECTED":
                current_position = self.repository.get_position(signal["symbol"])
                self.repository.transition_position(
                    signal["symbol"],
                    expected_state=current_position.state,
                    new_state=expected,
                    reason_code="ORDER_SUBMISSION_FAILED",
                    expected_version=current_position.version,
                )
                if expected != PositionState.EMPTY and self.repository.active_tp_plan(
                    signal["symbol"]
                ):
                    self.tp_manager.place_orders(signal["symbol"])
            else:
                self.repository.mark_signal(
                    signal_id,
                    status="UNKNOWN",
                    processed=True,
                    reason="ORDER_SUBMISSION_UNKNOWN",
                )
                self.repository.log_event(
                    "SAFE_MODE",
                    "ORDER_SUBMISSION_UNKNOWN",
                    str(exc),
                    symbol=signal["symbol"],
                    context={"client_order_id": client_order_id},
                )
            raise
        if receipt.status in {"REJECTED", "CANCELED"} and receipt.filled_quantity == 0:
            current_position = self.repository.get_position(signal["symbol"])
            self.repository.transition_position(
                signal["symbol"],
                expected_state=current_position.state,
                new_state=expected,
                reason_code="ORDER_NOT_FILLED",
                expected_version=current_position.version,
            )
            self.repository.mark_order_applied(client_order_id)
            self.repository.mark_signal(
                signal_id, status="FAILED", processed=True, reason="ORDER_NOT_FILLED"
            )
            if expected != PositionState.EMPTY and self.repository.active_tp_plan(signal["symbol"]):
                self.tp_manager.place_orders(signal["symbol"])
            return receipt
        if receipt.status == "FILLED" and receipt.filled_quantity > 0:
            self.position_manager.apply_buy_fill(
                client_order_id,
                atr_pct=Decimal(str(signal["atr_pct"])),
            )
            self.tp_manager.place_orders(signal["symbol"])
        self.repository.mark_signal(signal_id, status="PROCESSED", processed=True)
        return receipt

    def _build_quote(self, signal_id: int, *, now: datetime | None) -> ReviewQuote:
        signal = self._active_signal(signal_id)
        current = now or datetime.now(UTC)
        session = self.market_clock.classify_session(current)
        if not session_is_allowed(session, self.config):
            raise ApprovalError(f"현재 주문 허용 세션이 아닙니다: {session}")
        position = self.repository.get_position(signal["symbol"])
        action = DecisionType(signal["action"])
        stage_trigger = (
            Decimal(str(signal["stage_trigger_price"]))
            if signal["stage_trigger_price"] is not None
            else None
        )
        ceiling = calculate_execution_price_ceiling(
            action,
            Decimal(str(signal["signal_close"])),
            self.config,
            stage_trigger_price=stage_trigger,
            average_price=position.average_price,
        )
        current_price = self.broker.get_price(signal["symbol"])
        limit = calculate_limit_price(current_price, ceiling, self.config)
        maximum_quantity = (
            position.tp1_filled_qty if action == DecisionType.REBUY_CANDIDATE else None
        )
        budget = Decimal(str(signal["planned_budget"]))
        quantity = calculate_order_quantity(
            budget,
            limit,
            self.config.global_.buy_fee,
            maximum_quantity,
        )
        if quantity < 1:
            raise ApprovalError("계산된 매수수량이 0주입니다")
        buying_power = self.broker.get_buying_power("USD")
        total = Decimal(quantity) * limit * (Decimal("1") + self.config.global_.buy_fee)
        if self.idle_cash_manager is not None:
            self.idle_cash_manager.ensure_buying_power(total)
            buying_power = self.broker.get_buying_power("USD")
        if total > buying_power:
            raise ApprovalError("실제 달러 매수가능금액이 부족합니다")
        return ReviewQuote(
            signal_id=signal_id,
            symbol=signal["symbol"],
            session=session,
            current_price=current_price,
            execution_ceiling=ceiling,
            limit_price=limit,
            quantity=quantity,
            planned_budget=budget,
            estimated_fee=Decimal(quantity) * limit * self.config.global_.buy_fee,
        )

    def _active_signal(self, signal_id: int) -> dict:
        signal = self.repository.get_signal(signal_id)
        if signal["status"] != "ACTIVE" or int(signal["processed"]):
            raise ApprovalError("활성 상태가 아닌 신호입니다")
        if datetime.fromisoformat(signal["valid_until"]) < datetime.now(UTC):
            self.repository.mark_signal(
                signal_id, status="EXPIRED", processed=True, reason="SIGNAL_EXPIRED"
            )
            raise ApprovalError("신호 유효시간이 만료되었습니다")
        invalid_reason = self._signal_gate_failure(signal)
        if invalid_reason is not None:
            self.repository.mark_signal(
                signal_id, status="INVALID", processed=True, reason=invalid_reason
            )
            self.repository.log_event(
                "WARNING",
                "SIGNAL_INVALIDATED",
                f"활성 신호를 무효화했습니다: {invalid_reason}",
                symbol=str(signal["symbol"]),
                context={"signal_id": signal_id, "score": int(signal["score"])},
            )
            raise ApprovalError(f"현재 전략 조건에 맞지 않는 신호입니다: {invalid_reason}")
        return signal

    def _signal_gate_failure(self, signal: dict) -> str | None:
        if (
            signal["strategy_version"] != self.config.version
            or signal["config_version"] != self.config.config_version
        ):
            return "SIGNAL_VERSION_MISMATCH"
        action = str(signal["action"])
        if action == DecisionType.FIRST_ENTRY_CANDIDATE.value:
            required_score = self.config.global_.entry_score
            required_reversal = self.config.global_.minimum_reversal_score
        elif action == DecisionType.ADD_ENTRY_CANDIDATE.value:
            target_stage = int(signal["target_stage"] or 0)
            rule = self.config.additional_entry.stages.get(target_stage)
            if rule is None:
                return "SIGNAL_STAGE_INVALID"
            required_score = rule.min_score
            required_reversal = self.config.global_.minimum_reversal_score
        elif action == DecisionType.REBUY_CANDIDATE.value:
            required_score = self.config.rebuy.minimum_score
            required_reversal = self.config.rebuy.minimum_reversal_score
        else:
            return "SIGNAL_ACTION_INVALID"
        if int(signal["score"]) < required_score:
            return "SIGNAL_SCORE_BELOW_MINIMUM"
        detail = signal.get("score_detail") or {}
        if int(detail.get("reversal_score", 0)) < required_reversal:
            return "SIGNAL_REVERSAL_BELOW_MINIMUM"
        if str(signal["regime"]) == "RED":
            return "SIGNAL_RED_REGIME"
        return None

    @staticmethod
    def _purpose(action: str, target_stage: int | None) -> str:
        if action == DecisionType.FIRST_ENTRY_CANDIDATE.value:
            return "ENTRY_1"
        if action == DecisionType.ADD_ENTRY_CANDIDATE.value:
            return f"ENTRY_{target_stage}"
        return "REBUY"
