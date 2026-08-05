from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from jd_holdings.config import StrategyConfig

from .enums import MarketRegime, PositionState
from .models import ScoreResult


@dataclass(frozen=True)
class EligibilityResult:
    allowed: bool
    reason_codes: tuple[str, ...]


def evaluate_eligibility(
    *,
    data_ok: bool,
    system_ok: bool,
    state: PositionState,
    allowed_states: tuple[PositionState, ...],
    regime: MarketRegime,
    score: ScoreResult,
    required_score: int,
    minimum_reversal_score: int,
    available_capital: Decimal,
    config: StrategyConfig,
) -> EligibilityResult:
    reasons: list[str] = []
    if not data_ok:
        reasons.append("DATA_INVALID")
    if not system_ok:
        reasons.append("SYSTEM_NOT_NORMAL")
    if state == PositionState.SAFE_MODE:
        reasons.append("SAFE_MODE_BLOCK")
    elif state not in allowed_states:
        reasons.append("STATE_NOT_ELIGIBLE")
    if regime == MarketRegime.RED:
        reasons.append("REGIME_RED_BLOCK")
    if score.total < required_score:
        reasons.append("ENTRY_SCORE_FAIL")
    if score.reversal_score < minimum_reversal_score:
        reasons.append("REVERSAL_GATE_FAIL")
    if available_capital <= 0:
        reasons.append("EXPOSURE_LIMIT")
    if config.global_.stop_loss_enabled:
        reasons.append("CONFIG_SAFETY_FAILURE")
    return EligibilityResult(allowed=not reasons, reason_codes=tuple(reasons))
