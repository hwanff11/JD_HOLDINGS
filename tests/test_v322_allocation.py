from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from jd_holdings.core.v322_allocation import (
    AllocationState,
    V322Policy,
    advance_state,
    apply_jdss_overlay,
    base_leverage,
    base_weights,
    hwm_risk_budget,
    semiconductor_wins,
)


def row(**overrides) -> pd.Series:
    values = {
        "close": 120.0,
        "sma_short": 115.0,
        "sma_long": 100.0,
        "ret_short": 0.03,
        "ret_medium": 0.08,
        "ret_long": 0.15,
        "volatility": 0.20,
        "sma_long_slope": 0.02,
    }
    values.update(overrides)
    return pd.Series(values)


def test_frozen_v322_policy_is_loaded_from_production_config(config):
    policy = V322Policy.from_config(config)
    assert policy.initial_capital == Decimal("50000")
    assert policy.hwm_reinvestment_fraction == Decimal("0.75")
    assert policy.volatility_brake == pytest.approx(0.30)
    assert policy.rs_benchmark == "SOXX"
    assert policy.rs_lookback == 126
    assert policy.rs_sleeve_fraction == pytest.approx(0.50)
    assert policy.jdss_overlay_weight == pytest.approx(0.05)
    assert policy.monthly_reset == "first_session_close"


def test_leverage_ladder_matches_frozen_contract(config):
    policy = V322Policy.from_config(config)
    assert base_leverage(row(volatility=0.31), policy) == 0.5
    assert base_leverage(row(close=95.0), policy) == 1.0
    assert base_leverage(row(), policy) == 1.5
    vote_only = row(ret_medium=-0.01, ret_short=0.03, ret_long=0.10, sma_long_slope=0.02)
    assert base_leverage(vote_only, policy) == 1.25
    weak = row(
        ret_short=-0.01,
        ret_medium=-0.01,
        ret_long=0.10,
        sma_short=95.0,
        sma_long_slope=-0.01,
    )
    assert base_leverage(weak, policy) == 1.0


def test_rs6m_split_and_one_way_exit(config):
    policy = V322Policy.from_config(config)
    qqq = row(ret_long=0.10)
    semi = pd.Series({"rs_return": 0.20})
    assert semiconductor_wins(qqq, semi) is True
    state = advance_state(None, pd.Timestamp("2026-08-03"), qqq, semi, policy)
    assert state.semiconductor_active is True
    assert state.leverage == 1.5
    assert base_weights(state, policy) == {
        "QQQ": pytest.approx(0.75),
        "TQQQ": pytest.approx(0.125),
        "SOXL": pytest.approx(0.125),
    }

    lost = pd.Series({"rs_return": 0.05})
    exited = advance_state(state, pd.Timestamp("2026-08-04"), qqq, lost, policy)
    assert exited.semiconductor_active is False
    assert base_weights(exited, policy) == {
        "QQQ": pytest.approx(0.75),
        "TQQQ": pytest.approx(0.25),
    }
    # The sleeve cannot re-enter during the same month even if RS recovers.
    recovered = advance_state(exited, pd.Timestamp("2026-08-05"), qqq, semi, policy)
    assert recovered.semiconductor_active is False
    # A new month resets the RS decision.
    next_month = advance_state(recovered, pd.Timestamp("2026-09-01"), qqq, semi, policy)
    assert next_month.semiconductor_active is True


def test_volatility_brake_is_one_way_until_next_month(config):
    policy = V322Policy.from_config(config)
    semi = pd.Series({"rs_return": 0.20})
    state = AllocationState("2026-08", 1.5, True)
    braked = advance_state(
        state,
        pd.Timestamp("2026-08-10"),
        row(volatility=0.35),
        semi,
        policy,
    )
    assert braked.leverage == 0.5
    recovered_vol = advance_state(
        braked,
        pd.Timestamp("2026-08-11"),
        row(volatility=0.15),
        semi,
        policy,
    )
    assert recovered_vol.leverage == 0.5
    reset = advance_state(
        recovered_vol,
        pd.Timestamp("2026-09-01"),
        row(volatility=0.15),
        semi,
        policy,
    )
    assert reset.leverage == 1.5


def test_jdss_overlay_replaces_qqq_without_adding_weight(config):
    policy = V322Policy.from_config(config)
    base = {"QQQ": 0.75, "TQQQ": 0.25}
    one = apply_jdss_overlay(
        base, active_tqqq=False, active_soxl=True, policy=policy
    )
    assert one == {
        "QQQ": pytest.approx(0.70),
        "TQQQ": pytest.approx(0.25),
        "SOXL": pytest.approx(0.05),
    }
    both = apply_jdss_overlay(
        base, active_tqqq=True, active_soxl=True, policy=policy
    )
    assert both["QQQ"] == pytest.approx(0.70)
    assert both["TQQQ"] == pytest.approx(0.275)
    assert both["SOXL"] == pytest.approx(0.025)
    assert sum(both.values()) == pytest.approx(1.0)


def test_hwm75_only_reinvests_three_quarters_of_high_water_profit(config):
    policy = V322Policy.from_config(config)
    assert hwm_risk_budget(Decimal("50000"), Decimal("50000"), policy) == Decimal("50000")
    assert hwm_risk_budget(Decimal("60000"), Decimal("60000"), policy) == Decimal("57500.00")
    # Drawdown never gets topped up; deployable budget is capped by current equity.
    assert hwm_risk_budget(Decimal("60000"), Decimal("54000"), policy) == Decimal("54000")
