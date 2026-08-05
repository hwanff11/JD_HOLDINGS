from __future__ import annotations

from jd_holdings.config import StrategyConfig

from .enums import RiskReviewLevel


def evaluate_risk_review(holding_days: int, config: StrategyConfig) -> RiskReviewLevel:
    if holding_days >= config.risk_review.high_days:
        return RiskReviewLevel.HIGH
    if holding_days >= config.risk_review.review_days:
        return RiskReviewLevel.REVIEW
    if holding_days >= config.risk_review.info_days:
        return RiskReviewLevel.INFO
    return RiskReviewLevel.NONE
