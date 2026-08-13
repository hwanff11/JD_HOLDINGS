from __future__ import annotations

from decimal import Decimal

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.managed_account import (
    available_managed_cash,
    current_v322_capital_state,
    managed_cash_balance,
    record_v322_equity,
)


def test_hwm75_state_starts_at_50k_and_only_uses_75_percent_of_profit(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    assert current_v322_capital_state(config, repository) == (
        Decimal("50000"),
        Decimal("50000"),
    )

    high_water, risk_budget = record_v322_equity(
        config, repository, Decimal("60000")
    )
    assert high_water == Decimal("60000")
    assert risk_budget == Decimal("57500.00")

    # Falling equity never lowers the remembered high-water mark and never receives a top-up.
    high_water, risk_budget = record_v322_equity(
        config, repository, Decimal("54000")
    )
    assert high_water == Decimal("60000")
    assert risk_budget == Decimal("54000")


def test_retained_profit_stays_cash_but_is_not_all_spendable(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    broker = DryRunBroker(buying_power=Decimal("60000"))
    record_v322_equity(config, repository, Decimal("60000"))

    # No fills means the reconstructed JDSS cash is still its original $50k.
    assert managed_cash_balance(config, repository) == Decimal("50000")
    assert available_managed_cash(config, repository, broker) == Decimal("50000")


def test_risk_budget_never_exceeds_current_completed_equity(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    record_v322_equity(config, repository, Decimal("100000"))
    assert current_v322_capital_state(config, repository)[1] == Decimal("87500.00")

    record_v322_equity(config, repository, Decimal("40000"))
    high_water, risk_budget = current_v322_capital_state(config, repository)
    assert high_water == Decimal("100000")
    assert risk_budget == Decimal("40000")
