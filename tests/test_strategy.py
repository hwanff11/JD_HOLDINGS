from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import make_score, make_snapshot

from jd_holdings.core.enums import DecisionType, PositionState
from jd_holdings.core.execution import calculate_limit_price, calculate_order_quantity
from jd_holdings.core.models import PositionSnapshot
from jd_holdings.core.strategy import evaluate_additional_entry, evaluate_entry
from jd_holdings.core.take_profit import calculate_take_profit


def test_first_entry_budget_uses_score_exposure(config):
    decision = evaluate_entry(
        make_snapshot(),
        make_score(84),
        PositionSnapshot(symbol="TQQQ", cash_remaining=Decimal("10000")),
        config,
    )
    assert decision.allowed
    assert decision.action == DecisionType.FIRST_ENTRY_CANDIDATE
    assert decision.cycle_exposure_cap == Decimal("6000.00")
    assert decision.planned_budget == Decimal("900.00")


def test_additional_entry_cap_expansion_example(config):
    position = PositionSnapshot(
        symbol="TQQQ",
        state=PositionState.HOLDING_1ST,
        cycle_exposure_cap=Decimal("6000"),
        staged_entry_capital=Decimal("900"),
        anchor_price=Decimal("100"),
        entry_count=1,
    )
    decision = evaluate_additional_entry(
        make_snapshot(close=Decimal("97")), make_score(89), position, 2, config
    )
    assert decision.allowed
    assert decision.cycle_exposure_cap == Decimal("8000")
    assert decision.target_cumulative_capital == Decimal("2800.00")
    assert decision.planned_budget == Decimal("1900.00")
    assert decision.stage_trigger_price == Decimal("97.0000")


def test_stage_trigger_boundary(config):
    position = PositionSnapshot(
        symbol="TQQQ",
        state=PositionState.HOLDING_1ST,
        cycle_exposure_cap=Decimal("6000"),
        staged_entry_capital=Decimal("900"),
        anchor_price=Decimal("100"),
        entry_count=1,
    )
    blocked = evaluate_additional_entry(
        make_snapshot(close=Decimal("97.01")), make_score(89), position, 2, config
    )
    assert not blocked.allowed
    assert "STAGE_TRIGGER_NOT_MET" in blocked.reason_codes


def test_limit_buffer_never_exceeds_stage_ceiling(config):
    assert calculate_limit_price(Decimal("96.90"), Decimal("97.00"), config) == Decimal("97.00")
    with pytest.raises(ValueError):
        calculate_limit_price(Decimal("97.01"), Decimal("97.00"), config)


def test_quantity_includes_fee(config):
    quantity = calculate_order_quantity(Decimal("1000"), Decimal("100"), config.global_.buy_fee)
    assert quantity == 9


def test_take_profit_odd_quantity_assigns_extra_share_to_tp1(config):
    plan = calculate_take_profit(Decimal("100"), 11, Decimal("0.05"), config)
    assert plan.tp1_rate == Decimal("0.040")
    assert plan.tp2_rate == Decimal("0.080")
    assert plan.tp1_quantity == 6
    assert plan.tp2_quantity == 5
    assert plan.tp1_price == Decimal("104.00")
    assert plan.tp2_price == Decimal("108.00")
