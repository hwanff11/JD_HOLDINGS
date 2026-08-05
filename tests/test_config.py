from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from jd_holdings.config import ConfigError, PositionConfig, validate_config


def test_default_config_is_valid_and_complete(config):
    assert config.version == "JDSS-1.1.2"
    assert config.enabled_symbols == ("TQQQ", "SOXL")
    assert sum(config.position.stage_weights) == Decimal("1")
    assert config.global_.stop_loss_enabled is False


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
