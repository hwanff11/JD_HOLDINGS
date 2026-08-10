from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from jd_holdings.config import StrategyConfig
from jd_holdings.core.enums import PositionState

from .broker import Broker
from .database import SQLiteRepository


class ReconciliationService:
    def __init__(
        self,
        config: StrategyConfig,
        repository: SQLiteRepository,
        broker: Broker,
    ) -> None:
        self.config = config
        self.repository = repository
        self.broker = broker

    def run(self) -> dict[str, list[str]]:
        broker_holdings = {item["symbol"]: item for item in self.broker.get_holdings()}
        result: dict[str, list[str]] = {}
        for symbol in self.config.enabled_symbols:
            position = self.repository.get_position(symbol)
            holding = broker_holdings.get(symbol)
            broker_qty = int(Decimal(str(holding["quantity"]))) if holding else 0
            issues: list[str] = []
            if position.quantity != broker_qty:
                issues.append(f"BROKER_DB_QTY_MISMATCH:{broker_qty}!={position.quantity}")
            if position.state == PositionState.EMPTY and broker_qty > 0:
                issues.append("DB_EMPTY_BROKER_POSITION")
            if position.state != PositionState.EMPTY and broker_qty == 0:
                issues.append("DB_POSITION_BROKER_EMPTY")
            plan = self.repository.active_tp_plan(symbol)
            if broker_qty > 0 and plan:
                expected_tp = (
                    int(plan["tp1_target_qty"])
                    - int(plan["tp1_filled_qty"])
                    + int(plan["tp2_target_qty"])
                    - int(plan["tp2_filled_qty"])
                )
                if expected_tp != broker_qty:
                    issues.append(f"TP_PLAN_QTY_MISMATCH:{expected_tp}!={broker_qty}")
            local_orders = self.repository.open_orders(symbol)
            if any(
                order["status"] == "UNKNOWN" and not order.get("broker_order_id")
                for order in local_orders
            ):
                issues.append("UNKNOWN_ORDER_WITHOUT_BROKER_ID")
            try:
                broker_orders = self.broker.list_orders(status="OPEN", symbol=symbol)
                local_broker_ids = {
                    str(order["broker_order_id"])
                    for order in local_orders
                    if order.get("broker_order_id")
                }
                broker_ids = {
                    str(order["orderId"]) for order in broker_orders if order.get("orderId")
                }
                if local_broker_ids != broker_ids:
                    issues.append("BROKER_DB_OPEN_ORDER_MISMATCH")
            except Exception as exc:
                issues.append(f"BROKER_OPEN_ORDER_LOOKUP_FAILED:{type(exc).__name__}")
            if issues:
                result[symbol] = issues
                if position.state != PositionState.SAFE_MODE:
                    self.repository.transition_position(
                        symbol,
                        expected_state=position.state,
                        new_state=PositionState.SAFE_MODE,
                        reason_code="BROKER_DB_MISMATCH",
                        expected_version=position.version,
                    )
                self.repository.log_event(
                    "SAFE_MODE",
                    "RECONCILIATION_FAILED",
                    ";".join(issues),
                    symbol=symbol,
                )
        if self.config.idle_cash.enabled:
            cash_symbol = self.config.idle_cash.symbol
            state = self.repository.get_idle_cash_state()
            holding = broker_holdings.get(cash_symbol)
            broker_qty = int(Decimal(str(holding["quantity"]))) if holding else 0
            issues: list[str] = []
            # 계좌에 이미 있던 개인 SGOV는 자동 인수하지 않는다. JDSS 원장 수량이
            # 실제 계좌 수량을 초과할 때만 자금관리 정합성 위반이다.
            if state.managed_quantity > broker_qty:
                issues.append(
                    f"SGOV_MANAGED_QTY_EXCEEDS_BROKER:{state.managed_quantity}>{broker_qty}"
                )
            local_orders = [
                order
                for order in self.repository.open_orders(cash_symbol)
                if str(order["purpose"]).startswith("SGOV_")
            ]
            if any(
                order["status"] == "UNKNOWN" and not order.get("broker_order_id")
                for order in local_orders
            ):
                issues.append("SGOV_UNKNOWN_ORDER_WITHOUT_BROKER_ID")
            try:
                broker_orders = self.broker.list_orders(status="OPEN", symbol=cash_symbol)
                local_ids = {
                    str(order["broker_order_id"])
                    for order in local_orders
                    if order.get("broker_order_id")
                }
                broker_ids = {
                    str(order["orderId"])
                    for order in broker_orders
                    if order.get("orderId")
                }
                if local_ids != broker_ids:
                    issues.append("SGOV_BROKER_DB_OPEN_ORDER_MISMATCH")
            except Exception as exc:
                issues.append(f"SGOV_OPEN_ORDER_LOOKUP_FAILED:{type(exc).__name__}")
            self.repository.set_system_value("idle_cash_safe_mode", "1" if issues else "0")
            if issues:
                result[cash_symbol] = issues
                self.repository.log_event(
                    "SAFE_MODE",
                    "SGOV_RECONCILIATION_FAILED",
                    ";".join(issues),
                    symbol=cash_symbol,
                )
        self.repository.set_system_value("last_reconciliation", datetime.now(UTC).isoformat())
        return result
