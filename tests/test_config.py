from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from jd_holdings.config import ConfigError, PositionConfig, validate_config


def test_default_config_is_valid_and_complete(config):
    assert config.version == "JDSS-1.3.0"
    assert config.enabled_symbols == ("TQQQ", "SOXL")
    assert sum(config.position.stage_weights) == Decimal("1")
    assert config.global_.stop_loss_enabled is False
    assert config.global_.entry_score == 76
    assert config.global_.minimum_reversal_score == 0
    assert config.scoring["grades"] == {"S": 92, "A": 88, "B": 82, "WATCH": 76}
    assert config.rebuy.enabled is False
    assert config.take_profit.use_atr is False


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
