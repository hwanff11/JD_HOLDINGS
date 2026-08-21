from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest

from jd_holdings.application.broker import DryRunBroker
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.initial_onboarding_portfolio import (
    ONBOARDING_STAGE_FILLED_KEY,
    ONBOARDING_STATUS_KEY,
    InitialOnboardingPortfolioService,
)
from jd_holdings.application.order_manager import OrderManager
from jd_holdings.application.portfolio_service import TARGET_QTY_GENERATION_KEY
from jd_holdings.core.initial_onboarding import (
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    STATUS_NOT_STARTED,
    InitialOnboardingPolicy,
    scaled_target_quantity,
)
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.settings import RuntimeSettings


def _settings(tmp_path) -> RuntimeSettings:
    return RuntimeSettings(
        trading_mode="dry_run",
        live_confirmation="",
        telegram_bot_token=None,
        allowed_chat_ids=(),
        database_path=tmp_path / "jdss.db",
        log_path=tmp_path / "jdss.log",
    )


class StubInitialOnboardingService(InitialOnboardingPortfolioService):
    def __init__(self, *args, target: pd.DataFrame, **kwargs):
        self._target = target
        super().__init__(*args, **kwargs)

    def _calculate_target(self, completed):
        del completed
        return {}, self._target

    def _completed_marked_equity(self, raw, timestamp):
        del raw, timestamp
        return Decimal("50000")


def _service(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "jdss.db", config)
    broker = DryRunBroker(
        {"QQQ": Decimal("500"), "TQQQ": Decimal("100"), "SOXL": Decimal("50")},
        buying_power=Decimal("50000"),
    )
    market_clock = MarketClock()
    completed = market_clock.latest_completed_session(
        datetime(2026, 8, 1, 12, tzinfo=UTC),
        delay_minutes=config.scheduler.signal_delay_minutes,
    )
    target = pd.DataFrame(
        [
            {
                "trade_date": completed.isoformat(),
                "leverage": 1.5,
                "semiconductor_active": True,
                "jdss_tqqq_active": False,
                "jdss_soxl_active": False,
                "QQQ": 0.75,
                "TQQQ": 0.125,
                "SOXL": 0.125,
            }
        ],
        index=[pd.Timestamp(completed)],
    )
    service = StubInitialOnboardingService(
        config,
        repository,
        broker,
        OrderManager(repository, broker, _settings(tmp_path)),
        object(),
        market_clock,
        trading_mode="dry_run",
        target=target,
    )
    return service, repository, broker, market_clock


def _seed_full_targets(repository: SQLiteRepository) -> dict[str, int]:
    full = {"QQQ": 74, "TQQQ": 62, "SOXL": 124}
    weights = {
        "QQQ": Decimal("0.75"),
        "TQQQ": Decimal("0.125"),
        "SOXL": Decimal("0.125"),
    }
    signal_date = datetime(2026, 8, 3).date()
    for symbol, quantity in full.items():
        repository.set_core_target(
            symbol,
            active=True,
            target_weight=weights[symbol],
            signal_trade_date=signal_date,
            target_qty=quantity,
        )
    repository.set_system_value(TARGET_QTY_GENERATION_KEY, signal_date.isoformat())
    return full


def _set_core_quantities(repository: SQLiteRepository, quantities: dict[str, int]) -> None:
    with repository.transaction() as connection:
        for symbol, quantity in quantities.items():
            connection.execute(
                "UPDATE core_positions SET qty = ? WHERE symbol = ?",
                (quantity, symbol),
            )


def test_initial_onboarding_policy_is_configured_as_50_75_100(config):
    policy = InitialOnboardingPolicy.from_config(config)

    assert policy.enabled is True
    assert policy.cumulative_fractions == (
        Decimal("0.5"),
        Decimal("0.75"),
        Decimal("1.0"),
    )
    assert policy.minimum_sessions_between_stages == 3
    assert scaled_target_quantity(101, Decimal("0.5")) == 50
    assert scaled_target_quantity(101, Decimal("0.75")) == 75
    assert scaled_target_quantity(101, Decimal("1")) == 101


def test_onboarding_blocks_risk_increase_until_operator_starts(tmp_path, config):
    service, repository, _broker, _clock = _service(tmp_path, config)
    full = _seed_full_targets(repository)

    assert service.onboarding_status() == STATUS_NOT_STARTED
    assert service._effective_target_quantity(repository.get_core_position("QQQ")) == 0

    snapshot = service.start_onboarding(datetime(2026, 8, 4, 12, tzinfo=UTC))

    assert snapshot["status"] == STATUS_ACTIVE
    assert snapshot["stage"] == 1
    assert snapshot["fraction"] == Decimal("0.5")
    assert snapshot["effective_targets"] == {
        symbol: scaled_target_quantity(quantity, Decimal("0.5"))
        for symbol, quantity in full.items()
    }


def test_onboarding_requires_fill_then_three_sessions_before_each_advance(tmp_path, config):
    service, repository, _broker, _clock = _service(tmp_path, config)
    full = _seed_full_targets(repository)
    service.start_onboarding(datetime(2026, 8, 4, 12, tzinfo=UTC))

    stage1 = {
        symbol: scaled_target_quantity(quantity, Decimal("0.5"))
        for symbol, quantity in full.items()
    }
    _set_core_quantities(repository, stage1)

    with pytest.raises(RuntimeError, match="3일"):
        service.advance_onboarding(datetime(2026, 8, 4, 12, tzinfo=UTC))
    assert repository.get_system_value(ONBOARDING_STAGE_FILLED_KEY) == "2026-08-03"

    stage2_snapshot = service.advance_onboarding(
        datetime(2026, 8, 7, 12, tzinfo=UTC)
    )
    assert stage2_snapshot["stage"] == 2
    assert stage2_snapshot["fraction"] == Decimal("0.75")

    stage2 = {
        symbol: scaled_target_quantity(quantity, Decimal("0.75"))
        for symbol, quantity in full.items()
    }
    _set_core_quantities(repository, stage2)
    with pytest.raises(RuntimeError, match="3일"):
        service.advance_onboarding(datetime(2026, 8, 7, 12, tzinfo=UTC))

    stage3_snapshot = service.advance_onboarding(
        datetime(2026, 8, 12, 12, tzinfo=UTC)
    )
    assert stage3_snapshot["stage"] == 3
    assert stage3_snapshot["fraction"] == Decimal("1.0")


def test_final_stage_fill_permanently_completes_onboarding(tmp_path, config):
    service, repository, _broker, _clock = _service(tmp_path, config)
    full = _seed_full_targets(repository)
    service.start_onboarding(datetime(2026, 8, 4, 12, tzinfo=UTC))
    repository.set_system_value("v322_initial_onboarding_stage", "3")
    repository.set_system_value("v322_initial_onboarding_stage_started_trade_date", "2026-08-11")
    repository.set_system_value(ONBOARDING_STAGE_FILLED_KEY, "")
    _set_core_quantities(repository, full)

    snapshot = service.advance_onboarding(datetime(2026, 8, 12, 12, tzinfo=UTC))

    assert snapshot["status"] == STATUS_COMPLETED
    assert repository.get_system_value(ONBOARDING_STATUS_KEY) == STATUS_COMPLETED
    assert snapshot["fraction"] == Decimal("1")
    with pytest.raises(RuntimeError, match="이미 완료"):
        service.start_onboarding(datetime(2026, 8, 13, 12, tzinfo=UTC))
