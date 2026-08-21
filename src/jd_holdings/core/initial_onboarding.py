from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any

STATUS_NOT_STARTED = "NOT_STARTED"
STATUS_ACTIVE = "ACTIVE"
STATUS_COMPLETED = "COMPLETED"
STATUS_BYPASSED = "BYPASSED_EXISTING_POSITION"
STATUS_DISABLED = "DISABLED"


@dataclasses.dataclass(frozen=True)
class InitialOnboardingPolicy:
    """One-time staged deployment policy for the first real portfolio entry."""

    enabled: bool
    cumulative_fractions: tuple[Decimal, ...]
    minimum_sessions_between_stages: int

    @classmethod
    def from_config(cls, config: Any) -> InitialOnboardingPolicy:
        allocation = config.market_regime.get("v322_allocation", {})
        raw = allocation.get("initial_onboarding", {})
        policy = cls(
            enabled=bool(raw.get("enabled", False)),
            cumulative_fractions=tuple(
                Decimal(str(value))
                for value in raw.get("cumulative_fractions", (Decimal("1"),))
            ),
            minimum_sessions_between_stages=int(
                raw.get("minimum_sessions_between_stages", 0)
            ),
        )
        policy.validate()
        return policy

    def validate(self) -> None:
        if not self.cumulative_fractions:
            raise ValueError("initial_onboarding cumulative_fractions가 비어 있습니다")
        previous = Decimal("0")
        for fraction in self.cumulative_fractions:
            if not Decimal("0") < fraction <= Decimal("1"):
                raise ValueError("initial_onboarding 누적 비율은 0 초과 1 이하여야 합니다")
            if fraction <= previous:
                raise ValueError("initial_onboarding 누적 비율은 단계마다 증가해야 합니다")
            previous = fraction
        if self.cumulative_fractions[-1] != Decimal("1"):
            raise ValueError("initial_onboarding 마지막 단계는 100%여야 합니다")
        if self.minimum_sessions_between_stages < 0:
            raise ValueError("initial_onboarding 거래일 간격은 0 이상이어야 합니다")

    @property
    def total_stages(self) -> int:
        return len(self.cumulative_fractions)

    def fraction_for_stage(self, stage: int) -> Decimal:
        if not 1 <= stage <= self.total_stages:
            raise ValueError(f"initial_onboarding 단계 범위 오류: {stage}")
        return self.cumulative_fractions[stage - 1]


def scaled_target_quantity(full_target: int, fraction: Decimal) -> int:
    if full_target < 0:
        raise ValueError("full_target은 0 이상이어야 합니다")
    if not Decimal("0") <= fraction <= Decimal("1"):
        raise ValueError("fraction은 0~1이어야 합니다")
    if fraction == Decimal("1"):
        return full_target
    return int(Decimal(full_target) * fraction)


def sessions_elapsed(calendar: Any, start_date: Any, end_date: Any) -> int:
    """Count completed exchange sessions strictly after start_date through end_date."""
    if start_date is None or end_date is None or end_date <= start_date:
        return 0
    sessions = calendar.sessions_in_range(start_date, end_date)
    return max(0, len(sessions) - 1)
