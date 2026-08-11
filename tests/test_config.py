from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from jd_holdings.config import ConfigError, PositionConfig, validate_config


def test_default_config_is_valid_and_complete(config):
    assert config.version == "JDSS-3.0.0-TWIN-H05"
    assert config.config_version == "3.0.0"
    assert config.enabled_symbols == ("TQQQ", "SOXL")
    assert sum(config.position.stage_weights) == Decimal("1")
    assert config.global_.stop_loss_enabled is False
    assert config.global_.approval_required is True
    assert config.global_.entry_score == 55
    assert config.global_.minimum_reversal_score == 5
    assert config.scoring["grades"] == {"S": 90, "A": 82, "B": 72, "WATCH": 50}
    assert config.scoring["calibration"]["exponents"] == {
        "regime": 1.0,
        "oversold": 0.45,
        "reversal": 0.55,
        "volume": 0.65,
        "atr": 0.9,
    }
    assert config.market_regime["soxl_sector_guard"]["enabled"] is True
    assert config.market_regime["soxl_sector_guard"]["blocked_stages"] == [1, 3, 4]
    assert config.rebuy.enabled is False
    assert config.take_profit.use_atr is False
    assert config.take_profit.tp1_base == Decimal("0.04")
    assert config.take_profit.tp2_base == Decimal("0.06")
    assert config.take_profit.remainder_exit.enabled is True
    assert config.take_profit.remainder_exit.wait_trading_days == 20
    assert config.take_profit.remainder_exit.target_from_avg == Decimal("0.02")
    assert config.idle_cash.enabled is True
    assert config.idle_cash.symbol == "SGOV"
    assert config.idle_cash.cash_buffer == Decimal("250")
    assert config.idle_cash.orderbook_limit_offset == Decimal("0.01")
    assert config.idle_cash.reprice_after_seconds == 60
    assert config.idle_cash.require_sale_fill_before_entry is True
    assert config.portfolio.enabled is True
    assert config.portfolio.total_capital == Decimal("20000")
    assert config.portfolio.core_target_weight == Decimal("0.15")
    assert config.portfolio.booster_max_weight == Decimal("0.05")
    assert config.portfolio.trend_months == 10
    assert config.portfolio.core_underlyings == {"TQQQ": "QQQ", "SOXL": "SOXX"}
    assert config.portfolio.live_enabled is False
    assert config.global_.capital_per_symbol == Decimal("1000")


def test_invalid_stage_weights_are_rejected(config):
    broken = replace(
        config,
        position=PositionConfig(
            stage_weights=(Decimal("0.5"), Decimal("0.5")),
            cumulative_weights=(Decimal("0.5"), Decimal("0.9")),
        ),
    )
    with pytest.raises(ConfigError):
        validate_config(broken)


def test_invalid_calibration_exponent_is_rejected(config):
    scoring = {
        **config.scoring,
        "calibration": {
            **config.scoring["calibration"],
            "exponents": {
                **config.scoring["calibration"]["exponents"],
                "volume": 0,
            },
        },
    }
    with pytest.raises(ConfigError, match="volume 보정 지수"):
        validate_config(replace(config, scoring=scoring))


def test_v3_live_flag_is_rejected(config):
    with pytest.raises(ConfigError, match="live_enabled"):
        validate_config(
            replace(config, portfolio=replace(config.portfolio, live_enabled=True))
        )
