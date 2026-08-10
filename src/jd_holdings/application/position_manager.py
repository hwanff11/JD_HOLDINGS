from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from jd_holdings.config import StrategyConfig
from jd_holdings.core.enums import DecisionType, PositionState
from jd_holdings.core.take_profit import calculate_take_profit

from .broker import Broker
from .database import SQLiteRepository

WAITING_FILL_BY_ACTION_STAGE = {
    (DecisionType.FIRST_ENTRY_CANDIDATE.value, 1): PositionState.WAITING_1ST_FILL,
    (DecisionType.ADD_ENTRY_CANDIDATE.value, 2): PositionState.WAITING_2ND_FILL,
    (DecisionType.ADD_ENTRY_CANDIDATE.value, 3): PositionState.WAITING_3RD_FILL,
    (DecisionType.ADD_ENTRY_CANDIDATE.value, 4): PositionState.WAITING_4TH_FILL,
    (DecisionType.REBUY_CANDIDATE.value, None): PositionState.WAITING_REBUY_FILL,
}

HOLDING_BY_ACTION_STAGE = {
    (DecisionType.FIRST_ENTRY_CANDIDATE.value, 1): PositionState.HOLDING_1ST,
    (DecisionType.ADD_ENTRY_CANDIDATE.value, 2): PositionState.HOLDING_2ND,
    (DecisionType.ADD_ENTRY_CANDIDATE.value, 3): PositionState.HOLDING_3RD,
    (DecisionType.ADD_ENTRY_CANDIDATE.value, 4): PositionState.HOLDING_4TH,
    (DecisionType.REBUY_CANDIDATE.value, None): PositionState.HOLDING_REBUY,
}


def tp1_completed_at_key(symbol: str, cycle_id: str) -> str:
    return f"tp1_completed_at:{symbol.upper()}:{cycle_id}"


class PositionManager:
    def __init__(
        self,
        config: StrategyConfig,
        repository: SQLiteRepository,
        broker: Broker,
    ) -> None:
        self.config = config
        self.repository = repository
        self.broker = broker

    def apply_buy_fill(self, client_order_id: str, *, atr_pct: Decimal) -> dict[str, Any] | None:
        order = self.repository.get_order_by_client_id(client_order_id)
        if not order:
            raise KeyError(client_order_id)
        if int(order["applied"]):
            return self.repository.active_tp_plan(order["symbol"])
        if int(order["filled_qty"]) <= 0 or not order.get("average_fill_price"):
            return None
        signal = self.repository.get_signal(int(order["signal_id"]))
        symbol = str(order["symbol"])
        position = self.repository.get_position(symbol)
        action = str(signal["action"])
        target_stage = int(signal["target_stage"]) if signal["target_stage"] else None
        expected = WAITING_FILL_BY_ACTION_STAGE[(action, target_stage)]
        target_state = HOLDING_BY_ACTION_STAGE[(action, target_stage)]
        if position.state != expected:
            raise RuntimeError(
                f"매수 체결 반영 상태 불일치: {position.state.value} != {expected.value}"
            )

        holdings = self.broker.get_holdings(symbol)
        holding = next((item for item in holdings if item.get("symbol") == symbol), None)
        fill_qty = int(order["filled_qty"])
        fill_price = Decimal(str(order["average_fill_price"]))
        if holding:
            new_qty = int(Decimal(str(holding["quantity"])))
            new_average = Decimal(str(holding["averagePurchasePrice"]))
        else:
            new_qty = position.quantity + fill_qty
            old_cost = position.average_price * Decimal(position.quantity)
            new_average = (old_cost + fill_price * fill_qty) / Decimal(new_qty)
        current_cost = new_average * Decimal(new_qty)
        gross = fill_price * Decimal(fill_qty) * (Decimal("1") + self.config.global_.buy_fee)
        cycle_id = position.cycle_id
        entry_count = position.entry_count
        staged = position.staged_entry_capital
        anchor = position.anchor_price
        rebuy_count = position.rebuy_count
        if action == DecisionType.FIRST_ENTRY_CANDIDATE.value:
            cycle_id = f"JDSS-{symbol}-{uuid.uuid4().hex[:12]}"
            entry_count = 1
            staged = gross
            anchor = fill_price
        elif action == DecisionType.ADD_ENTRY_CANDIDATE.value:
            entry_count = int(target_stage or entry_count + 1)
            staged += gross
        else:
            rebuy_count += 1

        tp = calculate_take_profit(new_average, new_qty, atr_pct, self.config)
        tp_plan_id = self.repository.create_tp_plan(
            cycle_id=cycle_id,
            symbol=symbol,
            source_event=str(order["purpose"]),
            average_price=new_average,
            atr_pct=atr_pct,
            tp1_price=tp.tp1_price,
            tp1_target_qty=tp.tp1_quantity,
            tp2_price=tp.tp2_price,
            tp2_target_qty=tp.tp2_quantity,
        )
        self.repository.transition_position(
            symbol,
            expected_state=expected,
            new_state=target_state,
            reason_code="BUY_FILL_CONFIRMED",
            updates={
                "cycle_id": cycle_id,
                "qty": new_qty,
                "avg_price": new_average,
                "current_cost_basis": current_cost,
                "cycle_exposure_cap": Decimal(str(signal["cycle_exposure_cap"])),
                "staged_entry_capital": staged,
                "cash_remaining": max(
                    Decimal("0"), self.config.global_.capital_per_symbol - current_cost
                ),
                "entry_count": entry_count,
                "anchor_price": anchor,
                "last_entry_price": fill_price,
                "last_entry_date": signal["trade_date"],
                "rebuy_count": rebuy_count,
                "rebuy_recovery_armed": False,
                "tp1_filled_qty": 0,
                "tp_plan_id": tp_plan_id,
            },
            expected_version=position.version,
        )
        self.repository.mark_order_applied(client_order_id)
        return self.repository.active_tp_plan(symbol)

    def apply_sell_fill(self, client_order_id: str) -> None:
        order = self.repository.get_order_by_client_id(client_order_id)
        if not order or int(order["applied"]):
            return
        if int(order["filled_qty"]) <= 0:
            return
        symbol = str(order["symbol"])
        position = self.repository.get_position(symbol)
        plan = self.repository.active_tp_plan(symbol)
        if not plan:
            raise RuntimeError("활성 TP 계획이 없습니다")
        purpose = str(order["purpose"])
        terminal = str(order["status"]) in {
            "FILLED",
            "CANCELED",
            "REJECTED",
            "REPLACED",
        }
        tp_leg = "TP2" if purpose == "REMAINDER_EXIT" else purpose
        self.repository.update_tp_fills(
            int(plan["tp_plan_id"]), leg=tp_leg, filled_qty=int(order["filled_qty"])
        )
        holdings = self.broker.get_holdings(symbol)
        holding = next((item for item in holdings if item.get("symbol") == symbol), None)
        quantity = int(Decimal(str(holding["quantity"]))) if holding else 0
        average = Decimal(str(holding["averagePurchasePrice"])) if holding else Decimal("0")
        current_cost = average * quantity
        if purpose == "TP1" and int(order["filled_qty"]) < int(plan["tp1_target_qty"]):
            self.repository.transition_position(
                symbol,
                expected_state=position.state,
                new_state=position.state,
                reason_code="TP1_PARTIAL_FILL",
                updates={
                    "qty": quantity,
                    "avg_price": average,
                    "current_cost_basis": current_cost,
                },
                expected_version=position.version,
            )
            if terminal:
                self.repository.mark_order_applied(client_order_id)
            return
        if quantity == 0:
            new_state = PositionState.EMPTY
            updates = {
                "cycle_id": None,
                "qty": 0,
                "avg_price": Decimal("0"),
                "current_cost_basis": Decimal("0"),
                "cycle_exposure_cap": Decimal("0"),
                "staged_entry_capital": Decimal("0"),
                "cash_remaining": self.config.global_.capital_per_symbol,
                "entry_count": 0,
                "anchor_price": Decimal("0"),
                "last_entry_price": Decimal("0"),
                "last_entry_date": None,
                "rebuy_count": 0,
                "rebuy_recovery_armed": False,
                "tp1_filled_qty": 0,
                "tp_plan_id": None,
            }
        elif purpose == "TP1" and int(order["filled_qty"]) >= int(plan["tp1_target_qty"]):
            new_state = PositionState.PARTIAL_TP_1
            updates = {
                "qty": quantity,
                "avg_price": average,
                "current_cost_basis": current_cost,
                "cash_remaining": max(
                    Decimal("0"), self.config.global_.capital_per_symbol - current_cost
                ),
                "tp1_filled_qty": int(order["filled_qty"]),
                "rebuy_recovery_armed": False,
            }
        else:
            new_state = position.state
            updates = {
                "qty": quantity,
                "avg_price": average,
                "current_cost_basis": current_cost,
            }
        self.repository.transition_position(
            symbol,
            expected_state=position.state,
            new_state=new_state,
            reason_code=f"{purpose}_FILL_CONFIRMED",
            updates=updates,
            expected_version=position.version,
        )
        if (
            purpose == "TP1"
            and new_state == PositionState.PARTIAL_TP_1
            and position.cycle_id
        ):
            self.repository.set_system_value(
                tp1_completed_at_key(symbol, position.cycle_id),
                str(order["updated_at"]),
            )
        if quantity == 0:
            self.repository.deactivate_tp_plan(int(plan["tp_plan_id"]))
        if terminal:
            self.repository.mark_order_applied(client_order_id)
