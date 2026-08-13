from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import make_score, make_snapshot

from jd_holdings.core.enums import DecisionType, PositionState
from jd_holdings.core.execution import calculate_limit_price, calculate_order_quantity
from jd_holdings.core.models import PositionSnapshot
from jd_holdings.core.strategy import evaluate_additional_entry, evaluate_entry
from jd_holdings.core.take_profit import calculate_take_profit


def test_final_config_contract(config):
    assert config.version == "JDSS-3.2.2-RS6M-ONEWAY-HWM75"
    assert config.global_.entry_score == 55
    assert config.global_.approval_required
    assert not config.global_.stop_loss_enabled
    assert config.position.stage_weights == tuple(
        Decimal(value) for value in ("0.40", "0.30", "0.20")
    )
    assert [
        config.additional_entry.stages[stage].min_drop_from_anchor for stage in (2, 3)
    ] == [Decimal("0.02"), Decimal("0.05")]
    assert [config.additional_entry.stages[stage].min_score for stage in (2, 3)] == [55, 55]
    assert config.take_profit.tp1_base == Decimal("0.04")
    assert config.take_profit.tp1_fraction == Decimal("0.30")
    assert config.take_profit.tp2_base == Decimal("0.10")
    assert not config.take_profit.remainder_exit.enabled
    assert not config.rebuy.enabled
    assert config.market_regime["soxl_sector_guard"]["blocked_stages"] == [1, 3]


def test_first_entry_score_55_is_required(config):
    position = PositionSnapshot(symbol="TQQQ", cash_remaining=Decimal("20000"))
    blocked = evaluate_entry(make_snapshot(), make_score(54), position, config)
    allowed = evaluate_entry(make_snapshot(), make_score(55), position, config)
    assert not blocked.allowed
    assert "ENTRY_SCORE_FAIL" in blocked.reason_codes
    assert allowed.allowed


def test_first_entry_budget_uses_score_exposure(config):
    decision = evaluate_entry(
        make_snapshot(),
        make_score(84),
        PositionSnapshot(symbol="TQQQ", cash_remaining=Decimal("20000")),
        config,
    )
    assert decision.allowed
    assert decision.action == DecisionType.FIRST_ENTRY_CANDIDATE
    assert decision.cycle_exposure_cap == Decimal("20000.00")
    assert decision.planned_budget == Decimal("8000.00")


def test_entry_without_reversal_is_blocked(config):
    decision = evaluate_entry(
        make_snapshot(),
        make_score(76, reversal=0),
        PositionSnapshot(symbol="TQQQ", cash_remaining=Decimal("20000")),
        config,
    )
    assert not decision.allowed
    assert "REVERSAL_GATE_FAIL" in decision.reason_codes


def test_entry_with_one_reversal_condition_can_enter(config):
    decision = evaluate_entry(
        make_snapshot(),
        make_score(76, reversal=5),
        PositionSnapshot(symbol="TQQQ", cash_remaining=Decimal("20000")),
        config,
    )
    assert decision.allowed
    assert decision.cycle_exposure_cap == Decimal("20000.00")
    assert decision.planned_budget == Decimal("8000.00")


def test_soxl_sector_guard_blocks_first_entry_in_weak_sector(config):
    decision = evaluate_entry(
        make_snapshot(symbol="SOXL", close=Decimal("90")),
        make_score(89),
        PositionSnapshot(symbol="SOXL", cash_remaining=Decimal("20000")),
        config,
        sector_benchmarks={
            "SOXX": make_snapshot(symbol="SOXX", close=Decimal("490"), ema60=500.0)
        },
    )
    assert not decision.allowed
    assert "SOXL_SECTOR_GUARD" in decision.reason_codes


def test_soxl_sector_guard_allows_first_entry_when_benchmark_is_healthy(config):
    decision = evaluate_entry(
        make_snapshot(symbol="SOXL", close=Decimal("90")),
        make_score(89),
        PositionSnapshot(symbol="SOXL", cash_remaining=Decimal("20000")),
        config,
        sector_benchmarks={
            "SOXX": make_snapshot(symbol="SOXX", close=Decimal("510"), ema60=500.0)
        },
    )
    assert decision.allowed


def test_additional_entry_stage2_uses_first_fill_anchor(config):
    position = PositionSnapshot(
        symbol="TQQQ",
        state=PositionState.HOLDING_1ST,
        cycle_exposure_cap=Decimal("20000"),
        staged_entry_capital=Decimal("8000"),
        anchor_price=Decimal("100"),
        entry_count=1,
    )
    decision = evaluate_additional_entry(
        make_snapshot(close=Decimal("98")), make_score(55), position, 2, config
    )
    assert decision.allowed
    assert decision.cycle_exposure_cap == Decimal("20000")
    assert decision.target_cumulative_capital == Decimal("14000.00")
    assert decision.planned_budget == Decimal("6000.00")
    assert decision.stage_trigger_price == Decimal("98.0000")


def test_stage3_trigger_is_five_percent_from_first_fill(config):
    position = PositionSnapshot(
        symbol="TQQQ",
        state=PositionState.HOLDING_2ND,
        cycle_exposure_cap=Decimal("20000"),
        staged_entry_capital=Decimal("14000"),
        anchor_price=Decimal("100"),
        entry_count=2,
    )
    blocked = evaluate_additional_entry(
        make_snapshot(close=Decimal("95.01")), make_score(55), position, 3, config
    )
    allowed = evaluate_additional_entry(
        make_snapshot(close=Decimal("95.00")), make_score(55), position, 3, config
    )
    assert not blocked.allowed
    assert "STAGE_TRIGGER_NOT_MET" in blocked.reason_codes
    assert allowed.allowed
    assert allowed.stage_trigger_price == Decimal("95.0000")
    assert allowed.planned_budget == Decimal("4000.00")


def test_stage4_is_not_configured(config):
    assert 4 not in config.additional_entry.stages
    with pytest.raises(ValueError, match="지원하지 않는 추가매수 단계"):
        evaluate_additional_entry(
            make_snapshot(close=Decimal("90")),
            make_score(55),
            PositionSnapshot(
                symbol="TQQQ",
                state=PositionState.HOLDING_3RD,
                cycle_exposure_cap=Decimal("20000"),
                staged_entry_capital=Decimal("18000"),
                anchor_price=Decimal("100"),
                entry_count=3,
            ),
            4,
            config,
        )


def test_additional_entry_without_reversal_is_blocked(config):
    position = PositionSnapshot(
        symbol="TQQQ",
        state=PositionState.HOLDING_1ST,
        cycle_exposure_cap=Decimal("20000"),
        staged_entry_capital=Decimal("8000"),
        anchor_price=Decimal("100"),
        entry_count=1,
    )
    decision = evaluate_additional_entry(
        make_snapshot(close=Decimal("96")), make_score(89, reversal=0), position, 2, config
    )
    assert not decision.allowed
    assert "REVERSAL_GATE_FAIL" in decision.reason_codes


def test_soxl_sector_guard_blocks_stage3_when_benchmark_below_ema60(config):
    position = PositionSnapshot(
        symbol="SOXL",
        state=PositionState.HOLDING_2ND,
        cycle_exposure_cap=Decimal("20000"),
        staged_entry_capital=Decimal("14000"),
        anchor_price=Decimal("100"),
        entry_count=2,
    )
    decision = evaluate_additional_entry(
        make_snapshot(symbol="SOXL", close=Decimal("92")),
        make_score(89),
        position,
        3,
        config,
        sector_benchmarks={
            "SOXX": make_snapshot(symbol="SOXX", close=Decimal("490"), ema60=500.0)
        },
    )
    assert not decision.allowed
    assert "SOXL_SECTOR_GUARD" in decision.reason_codes


def test_soxl_sector_guard_does_not_block_stage2(config):
    position = PositionSnapshot(
        symbol="SOXL",
        state=PositionState.HOLDING_1ST,
        cycle_exposure_cap=Decimal("20000"),
        staged_entry_capital=Decimal("8000"),
        anchor_price=Decimal("100"),
        entry_count=1,
    )
    decision = evaluate_additional_entry(
        make_snapshot(symbol="SOXL", close=Decimal("96")),
        make_score(89),
        position,
        2,
        config,
        sector_benchmarks={
            "SOXX": make_snapshot(symbol="SOXX", close=Decimal("490"), ema60=500.0)
        },
    )
    assert decision.allowed


def test_stage2_trigger_boundary(config):
    position = PositionSnapshot(
        symbol="TQQQ",
        state=PositionState.HOLDING_1ST,
        cycle_exposure_cap=Decimal("20000"),
        staged_entry_capital=Decimal("8000"),
        anchor_price=Decimal("100"),
        entry_count=1,
    )
    blocked = evaluate_additional_entry(
        make_snapshot(close=Decimal("98.01")), make_score(89), position, 2, config
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


def test_take_profit_uses_configured_30_percent_tp1(config):
    plan = calculate_take_profit(Decimal("100"), 11, Decimal("0.05"), config)
    assert plan.tp1_rate == Decimal("0.04")
    assert plan.tp2_rate == Decimal("0.10")
    assert plan.tp1_quantity == 4
    assert plan.tp2_quantity == 7
    assert plan.tp1_price == Decimal("104.00")
    assert plan.tp2_price == Decimal("110.00")
