from __future__ import annotations

from decimal import Decimal

from jd_holdings.config import StrategyConfig

from .eligibility import evaluate_eligibility
from .enums import DecisionType, PositionState
from .models import IndicatorSnapshot, PositionSnapshot, ScoreResult, TradeDecision


def score_to_exposure(score: int, config: StrategyConfig) -> Decimal:
    if score >= 92:
        ratio = config.exposure.score_92_100
    elif score >= 88:
        ratio = config.exposure.score_88_91
    elif score >= config.global_.entry_score:
        ratio = config.exposure.score_82_87
    else:
        return Decimal("0")
    return (config.global_.capital_per_symbol * ratio).quantize(Decimal("0.01"))


def update_cycle_exposure_cap(previous_cap: Decimal, score: int, config: StrategyConfig) -> Decimal:
    proposed = score_to_exposure(score, config)
    if previous_cap <= 0:
        return proposed
    if config.exposure.allow_cap_increase:
        return min(config.global_.capital_per_symbol, max(previous_cap, proposed))
    return previous_cap


def calculate_stage_budget(
    cycle_exposure_cap: Decimal,
    target_stage: int,
    staged_entry_capital: Decimal,
    config: StrategyConfig,
) -> tuple[Decimal, Decimal]:
    if not 1 <= target_stage <= len(config.position.cumulative_weights):
        raise ValueError(f"지원하지 않는 매수 단계: {target_stage}")
    target = cycle_exposure_cap * config.position.cumulative_weights[target_stage - 1]
    target = target.quantize(Decimal("0.01"))
    budget = max(Decimal("0"), target - staged_entry_capital).quantize(Decimal("0.01"))
    return target, budget


def soxl_sector_guard_blocks(
    symbol: str,
    target_stage: int,
    sector_benchmarks: dict[str, IndicatorSnapshot] | None,
    config: StrategyConfig,
) -> bool:
    """Block configured SOXL entry stages when semiconductor benchmarks are weak."""
    guard = config.market_regime.get("soxl_sector_guard", {})
    if symbol.upper() != "SOXL" or not guard.get("enabled", False):
        return False
    blocked_stages = {int(stage) for stage in guard.get("blocked_stages", (3, 4))}
    if target_stage not in blocked_stages:
        return False
    if not sector_benchmarks:
        return False

    candidates = [
        str(value).upper()
        for value in guard.get("benchmark_candidates", ("SOXX", "SMH"))
    ]
    available = [sector_benchmarks[name] for name in candidates if name in sector_benchmarks]
    if not available:
        return False

    rule = str(guard.get("rule", "any_benchmark_below_ema60"))
    below = [float(snapshot.close) < snapshot.ema60 for snapshot in available]
    if rule == "all_benchmarks_below_ema60":
        return all(below)
    return any(below)


def first_entry_trend_guard_blocks(
    snapshot: IndicatorSnapshot,
    config: StrategyConfig,
) -> bool:
    """Block only the first entry during a confirmed bearish trend.

    This guard intentionally does not affect averaging stages.  The default
    research rule requires both price below EMA60 and EMA20 below EMA60, which
    is stricter than merely buying below a long moving average and therefore
    preserves more ordinary oversold-rebound opportunities.
    """
    guard = config.market_regime.get("first_entry_trend_guard", {})
    if not guard.get("enabled", False):
        return False
    symbols = {str(value).upper() for value in guard.get("symbols", ("TQQQ", "SOXL"))}
    if snapshot.symbol.upper() not in symbols:
        return False
    rule = str(guard.get("rule", "price_below_ema60_and_ema20_below_ema60"))
    price_below = float(snapshot.close) < snapshot.ema60
    ema_bearish = snapshot.ema20 < snapshot.ema60
    if rule == "ema20_below_ema60":
        return ema_bearish
    return price_below and ema_bearish


def evaluate_entry(
    snapshot: IndicatorSnapshot,
    score: ScoreResult,
    position: PositionSnapshot,
    config: StrategyConfig,
    *,
    data_ok: bool = True,
    system_ok: bool = True,
    sector_benchmarks: dict[str, IndicatorSnapshot] | None = None,
) -> TradeDecision:
    cycle_cap = score_to_exposure(score.total, config)
    target, budget = calculate_stage_budget(cycle_cap, 1, Decimal("0"), config)
    eligibility = evaluate_eligibility(
        data_ok=data_ok,
        system_ok=system_ok,
        state=position.state,
        allowed_states=(PositionState.EMPTY,),
        regime=score.regime,
        score=score,
        required_score=config.global_.entry_score,
        minimum_reversal_score=config.global_.minimum_reversal_score,
        available_capital=budget,
        config=config,
    )
    reasons = list(eligibility.reason_codes)
    if soxl_sector_guard_blocks(snapshot.symbol, 1, sector_benchmarks, config):
        reasons.append("SOXL_SECTOR_GUARD")
    if first_entry_trend_guard_blocks(snapshot, config):
        reasons.append("FIRST_ENTRY_TREND_GUARD")
    allowed = not reasons
    return TradeDecision(
        action=DecisionType.FIRST_ENTRY_CANDIDATE if allowed else DecisionType.NO_ACTION,
        allowed=allowed,
        reason_codes=tuple(reasons) or ("ENTRY_SCORE_PASS",),
        target_stage=1 if allowed else None,
        cycle_exposure_cap=cycle_cap,
        target_cumulative_capital=target,
        planned_budget=budget,
    )


def expected_holding_state(stage: int) -> PositionState:
    states = {
        1: PositionState.HOLDING_1ST,
        2: PositionState.HOLDING_2ND,
        3: PositionState.HOLDING_3RD,
        4: PositionState.HOLDING_4TH,
    }
    return states[stage]


def evaluate_additional_entry(
    snapshot: IndicatorSnapshot,
    score: ScoreResult,
    position: PositionSnapshot,
    target_stage: int,
    config: StrategyConfig,
    *,
    data_ok: bool = True,
    system_ok: bool = True,
    sector_benchmarks: dict[str, IndicatorSnapshot] | None = None,
) -> TradeDecision:
    if target_stage not in config.additional_entry.stages:
        raise ValueError("추가매수 단계는 2, 3, 4 중 하나여야 합니다")
    rule = config.additional_entry.stages[target_stage]
    trigger = position.anchor_price * (Decimal("1") - rule.min_drop_from_anchor)
    new_cap = update_cycle_exposure_cap(position.cycle_exposure_cap, score.total, config)
    target, budget = calculate_stage_budget(
        new_cap, target_stage, position.staged_entry_capital, config
    )
    eligibility = evaluate_eligibility(
        data_ok=data_ok,
        system_ok=system_ok,
        state=position.state,
        allowed_states=(expected_holding_state(target_stage - 1),),
        regime=score.regime,
        score=score,
        required_score=rule.min_score,
        minimum_reversal_score=config.global_.minimum_reversal_score,
        available_capital=budget,
        config=config,
    )
    reasons = list(eligibility.reason_codes)
    if position.anchor_price <= 0:
        reasons.append("ANCHOR_MISSING")
    elif snapshot.close > trigger:
        reasons.append("STAGE_TRIGGER_NOT_MET")
    if soxl_sector_guard_blocks(snapshot.symbol, target_stage, sector_benchmarks, config):
        reasons.append("SOXL_SECTOR_GUARD")
    allowed = not reasons
    return TradeDecision(
        action=DecisionType.ADD_ENTRY_CANDIDATE if allowed else DecisionType.NO_ACTION,
        allowed=allowed,
        reason_codes=tuple(reasons) or ("ADDITIONAL_ENTRY_PASS",),
        target_stage=target_stage if allowed else None,
        cycle_exposure_cap=new_cap,
        target_cumulative_capital=target,
        planned_budget=budget,
        stage_trigger_price=trigger.quantize(Decimal("0.0001")),
    )


def evaluate_rebuy_recovery(snapshot: IndicatorSnapshot, config: StrategyConfig) -> bool:
    values = config.rebuy.recovery.values
    conditions = [
        snapshot.cci5 > float(values["cci5_gt"]),
        snapshot.rsi5 >= float(values["rsi5_gte"]),
        float(snapshot.close) > snapshot.ema5 if values["close_above_ema5"] else False,
    ]
    return any(conditions) if config.rebuy.recovery.mode == "any" else all(conditions)


def evaluate_reoversold(snapshot: IndicatorSnapshot, config: StrategyConfig) -> bool:
    values = config.rebuy.reoversold.values
    conditions = [
        snapshot.cci5 <= float(values["cci5_lte"]),
        snapshot.rsi5 <= float(values["rsi5_lte"]),
        float(snapshot.close) <= snapshot.bb_lower if values["close_below_lower_band"] else False,
    ]
    return any(conditions) if config.rebuy.reoversold.mode == "any" else all(conditions)


def evaluate_rebuy(
    snapshot: IndicatorSnapshot,
    score: ScoreResult,
    position: PositionSnapshot,
    config: StrategyConfig,
    *,
    data_ok: bool = True,
    system_ok: bool = True,
) -> TradeDecision:
    available_capital = max(
        Decimal("0"), position.cycle_exposure_cap - position.current_cost_basis
    )
    eligibility = evaluate_eligibility(
        data_ok=data_ok,
        system_ok=system_ok,
        state=position.state,
        allowed_states=(PositionState.PARTIAL_TP_1,),
        regime=score.regime,
        score=score,
        required_score=config.rebuy.minimum_score,
        minimum_reversal_score=config.rebuy.minimum_reversal_score,
        available_capital=available_capital,
        config=config,
    )
    reasons = list(eligibility.reason_codes)
    if not config.rebuy.enabled:
        reasons.append("REBUY_DISABLED")
    if position.rebuy_count >= config.rebuy.max_rebuy_per_cycle:
        reasons.append("REBUY_ALREADY_USED")
    if not position.rebuy_recovery_armed:
        reasons.append("REBUY_RECOVERY_NOT_ARMED")
    if not evaluate_reoversold(snapshot, config):
        reasons.append("REOVERSOLD_NOT_MET")
    trigger = position.average_price * (Decimal("1") - config.rebuy.min_drop_from_avg)
    if snapshot.close > trigger:
        reasons.append("REBUY_PRICE_NOT_MET")
    requested = Decimal(position.tp1_filled_qty) * snapshot.close * (
        Decimal("1") + config.global_.buy_fee
    )
    budget = min(available_capital, requested).quantize(Decimal("0.01"))
    if budget <= 0:
        reasons.append("EXPOSURE_LIMIT")
    allowed = not reasons
    return TradeDecision(
        action=DecisionType.REBUY_CANDIDATE if allowed else DecisionType.NO_ACTION,
        allowed=allowed,
        reason_codes=tuple(reasons) or ("REBUY_PASS",),
        cycle_exposure_cap=position.cycle_exposure_cap,
        target_cumulative_capital=position.cycle_exposure_cap,
        planned_budget=budget,
        stage_trigger_price=trigger.quantize(Decimal("0.0001")),
    )


def evaluate_strategy(
    snapshot: IndicatorSnapshot,
    score: ScoreResult,
    position: PositionSnapshot,
    config: StrategyConfig,
    *,
    data_ok: bool = True,
    system_ok: bool = True,
    sector_benchmarks: dict[str, IndicatorSnapshot] | None = None,
) -> TradeDecision:
    if position.state == PositionState.EMPTY:
        return evaluate_entry(
            snapshot,
            score,
            position,
            config,
            data_ok=data_ok,
            system_ok=system_ok,
            sector_benchmarks=sector_benchmarks,
        )
    for stage in (2, 3, 4):
        if position.state == expected_holding_state(stage - 1):
            return evaluate_additional_entry(
                snapshot,
                score,
                position,
                stage,
                config,
                data_ok=data_ok,
                system_ok=system_ok,
                sector_benchmarks=sector_benchmarks,
            )
    if position.state == PositionState.PARTIAL_TP_1:
        return evaluate_rebuy(
            snapshot,
            score,
            position,
            config,
            data_ok=data_ok,
            system_ok=system_ok,
        )
    return TradeDecision(
        action=DecisionType.NO_ACTION,
        allowed=False,
        reason_codes=("STATE_NOT_ELIGIBLE",),
    )
