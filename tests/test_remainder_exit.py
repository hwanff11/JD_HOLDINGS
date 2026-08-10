from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from jd_holdings.core.remainder_exit import remainder_exit_due, remainder_exit_price


def test_remainder_exit_due_uses_configured_trading_days(config):
    rule = config.take_profit.remainder_exit
    assert not remainder_exit_due(19, rule)
    assert remainder_exit_due(20, rule)
    assert remainder_exit_due(21, rule)


def test_remainder_exit_disabled_never_becomes_due(config):
    rule = replace(config.take_profit.remainder_exit, enabled=False)
    assert not remainder_exit_due(20, rule)
    assert not remainder_exit_due(200, rule)


def test_remainder_exit_price_uses_configured_average_profit_and_tick_rounding(config):
    rule = config.take_profit.remainder_exit
    assert remainder_exit_price(Decimal("100"), rule) == Decimal("102.00")
    assert remainder_exit_price(Decimal("1.012"), rule) == Decimal("1.04")


def test_remainder_exit_rejects_negative_elapsed_sessions(config):
    with pytest.raises(ValueError, match="경과 거래일"):
        remainder_exit_due(-1, config.take_profit.remainder_exit)
