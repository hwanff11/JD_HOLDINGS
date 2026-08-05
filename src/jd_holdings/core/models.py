from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .enums import (
    DecisionType,
    MarketRegime,
    PositionState,
    RiskReviewLevel,
    SignalGrade,
)

ZERO = Decimal("0")


@dataclass(frozen=True)
class IndicatorSnapshot:
    symbol: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    previous_close: Decimal
    volume: int
    cci5: float
    cci10: float
    rsi5: float
    rsi14: float
    ema5: float
    ema20: float
    ema60: float
    bb_lower: float
    atr14: float
    atr_pct: float
    volume_ratio: float
    close_position: float


@dataclass(frozen=True)
class ScoreResult:
    total: int
    grade: SignalGrade
    regime: MarketRegime
    regime_score: int
    oversold_score: int
    reversal_score: int
    volume_score: int
    atr_score: int

    def detail(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "grade": self.grade.value,
            "regime": self.regime.value,
            "regime_score": self.regime_score,
            "oversold_score": self.oversold_score,
            "reversal_score": self.reversal_score,
            "volume_score": self.volume_score,
            "atr_score": self.atr_score,
        }


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    state: PositionState = PositionState.EMPTY
    cycle_id: str | None = None
    quantity: int = 0
    average_price: Decimal = ZERO
    current_cost_basis: Decimal = ZERO
    cycle_exposure_cap: Decimal = ZERO
    staged_entry_capital: Decimal = ZERO
    cash_remaining: Decimal = ZERO
    entry_count: int = 0
    anchor_price: Decimal = ZERO
    last_entry_price: Decimal = ZERO
    last_entry_date: date | None = None
    rebuy_count: int = 0
    rebuy_recovery_armed: bool = False
    tp1_filled_qty: int = 0
    risk_review_level: RiskReviewLevel = RiskReviewLevel.NONE
    version: int = 0


@dataclass(frozen=True)
class TradeDecision:
    action: DecisionType
    allowed: bool
    reason_codes: tuple[str, ...]
    target_stage: int | None = None
    cycle_exposure_cap: Decimal = ZERO
    target_cumulative_capital: Decimal = ZERO
    planned_budget: Decimal = ZERO
    stage_trigger_price: Decimal | None = None


@dataclass(frozen=True)
class TakeProfitPlan:
    average_price: Decimal
    atr_pct: Decimal
    tp1_rate: Decimal
    tp2_rate: Decimal
    tp1_price: Decimal
    tp2_price: Decimal
    tp1_quantity: int
    tp2_quantity: int


@dataclass(frozen=True)
class PendingSignal:
    signal_id: int
    symbol: str
    trade_date: date
    action: DecisionType
    target_stage: int | None
    signal_close: Decimal
    max_chase_price: Decimal
    stage_trigger_price: Decimal | None
    planned_budget: Decimal
    cycle_exposure_cap: Decimal
    score: ScoreResult
    valid_until: datetime


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: int
    price: Decimal | None
    purpose: str
    signal_id: int | None = None


@dataclass(frozen=True)
class OrderReceipt:
    client_order_id: str
    broker_order_id: str
    status: str
    quantity: int
    filled_quantity: int = 0
    average_fill_price: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict)
