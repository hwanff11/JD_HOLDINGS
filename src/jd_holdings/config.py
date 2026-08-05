from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when strategy.yaml is incomplete or internally inconsistent."""


@dataclass(frozen=True)
class GlobalConfig:
    capital_per_symbol: Decimal
    buy_fee: Decimal
    sell_fee: Decimal
    approval_required: bool
    stop_loss_enabled: bool
    entry_score: int
    minimum_reversal_score: int
    entry_max_chase_pct: Decimal
    buy_limit_buffer: Decimal
    trading_sessions: dict[str, bool]
    review_token_ttl_minutes: int
    execution_token_ttl_seconds: int
    buy_fill_timeout_seconds: int


@dataclass(frozen=True)
class PositionConfig:
    stage_weights: tuple[Decimal, ...]
    cumulative_weights: tuple[Decimal, ...]


@dataclass(frozen=True)
class ExposureConfig:
    score_82_87: Decimal
    score_88_91: Decimal
    score_92_100: Decimal
    allow_cap_increase: bool
    allow_cap_decrease_during_cycle: bool


@dataclass(frozen=True)
class StageRule:
    min_drop_from_anchor: Decimal
    min_score: int


@dataclass(frozen=True)
class AdditionalEntryConfig:
    anchor: str
    max_stage_per_day: int
    stages: dict[int, StageRule]


@dataclass(frozen=True)
class TakeProfitConfig:
    tp1_base: Decimal
    tp2_base: Decimal
    use_atr: bool
    tp1_atr_multiplier: Decimal
    tp2_atr_multiplier: Decimal


@dataclass(frozen=True)
class RebuyConditionConfig:
    values: dict[str, Any]
    mode: str


@dataclass(frozen=True)
class RebuyConfig:
    enabled: bool
    minimum_score: int
    minimum_reversal_score: int
    min_drop_from_avg: Decimal
    max_rebuy_per_cycle: int
    recovery: RebuyConditionConfig
    reoversold: RebuyConditionConfig


@dataclass(frozen=True)
class RiskReviewConfig:
    info_days: int
    review_days: int
    high_days: int


@dataclass(frozen=True)
class SchedulerConfig:
    signal_delay_minutes: int
    poll_interval_seconds: int
    order_monitor_interval_seconds: int


@dataclass(frozen=True)
class BacktestConfig:
    default_start: str
    default_slippage: Decimal
    annualization_days: int


@dataclass(frozen=True)
class StrategyConfig:
    version: str
    config_version: str
    global_: GlobalConfig
    symbols: dict[str, bool]
    indicators: dict[str, Any]
    market_regime: dict[str, Any]
    scoring: dict[str, Any]
    position: PositionConfig
    exposure: ExposureConfig
    additional_entry: AdditionalEntryConfig
    take_profit: TakeProfitConfig
    rebuy: RebuyConfig
    risk_review: RiskReviewConfig
    scheduler: SchedulerConfig
    backtest: BacktestConfig

    @property
    def enabled_symbols(self) -> tuple[str, ...]:
        return tuple(symbol for symbol, enabled in self.symbols.items() if enabled)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _require(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ConfigError(f"필수 설정이 없습니다: {key}")
    return data[key]


def load_config(path: str | Path | None = None) -> StrategyConfig:
    config_path = Path(path) if path else Path(__file__).resolve().parents[2] / "strategy.yaml"
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ConfigError("strategy.yaml 최상위 값은 mapping이어야 합니다.")

    global_raw = _require(raw, "global")
    position_raw = _require(raw, "position")
    exposure_raw = _require(raw, "exposure")
    add_raw = _require(raw, "additional_entry")
    tp_raw = _require(raw, "take_profit")
    rebuy_raw = _require(raw, "rebuy")
    risk_raw = _require(raw, "risk_review")
    scheduler_raw = _require(raw, "scheduler")
    backtest_raw = _require(raw, "backtest")

    config = StrategyConfig(
        version=str(_require(raw, "version")),
        config_version=str(_require(raw, "config_version")),
        global_=GlobalConfig(
            capital_per_symbol=_decimal(global_raw["capital_per_symbol"]),
            buy_fee=_decimal(global_raw["buy_fee"]),
            sell_fee=_decimal(global_raw["sell_fee"]),
            approval_required=bool(global_raw["approval_required"]),
            stop_loss_enabled=bool(global_raw["stop_loss_enabled"]),
            entry_score=int(global_raw["entry_score"]),
            minimum_reversal_score=int(global_raw["minimum_reversal_score"]),
            entry_max_chase_pct=_decimal(global_raw["entry_max_chase_pct"]),
            buy_limit_buffer=_decimal(global_raw["buy_limit_buffer"]),
            trading_sessions=dict(global_raw["trading_sessions"]),
            review_token_ttl_minutes=int(global_raw["review_token_ttl_minutes"]),
            execution_token_ttl_seconds=int(global_raw["execution_token_ttl_seconds"]),
            buy_fill_timeout_seconds=int(global_raw["buy_fill_timeout_seconds"]),
        ),
        symbols={key.upper(): bool(value["enabled"]) for key, value in raw["symbols"].items()},
        indicators=dict(_require(raw, "indicators")),
        market_regime=dict(_require(raw, "market_regime")),
        scoring=dict(_require(raw, "scoring")),
        position=PositionConfig(
            stage_weights=tuple(_decimal(v) for v in position_raw["stage_weights"]),
            cumulative_weights=tuple(_decimal(v) for v in position_raw["cumulative_weights"]),
        ),
        exposure=ExposureConfig(
            score_82_87=_decimal(exposure_raw["score_82_87"]),
            score_88_91=_decimal(exposure_raw["score_88_91"]),
            score_92_100=_decimal(exposure_raw["score_92_100"]),
            allow_cap_increase=bool(exposure_raw["allow_cap_increase"]),
            allow_cap_decrease_during_cycle=bool(exposure_raw["allow_cap_decrease_during_cycle"]),
        ),
        additional_entry=AdditionalEntryConfig(
            anchor=str(add_raw["anchor"]),
            max_stage_per_day=int(add_raw["max_stage_per_day"]),
            stages={
                stage: StageRule(
                    min_drop_from_anchor=_decimal(add_raw[f"stage{stage}"]["min_drop_from_anchor"]),
                    min_score=int(add_raw[f"stage{stage}"]["min_score"]),
                )
                for stage in (2, 3, 4)
            },
        ),
        take_profit=TakeProfitConfig(
            tp1_base=_decimal(tp_raw["tp1_base"]),
            tp2_base=_decimal(tp_raw["tp2_base"]),
            use_atr=bool(tp_raw["use_atr"]),
            tp1_atr_multiplier=_decimal(tp_raw["tp1_atr_multiplier"]),
            tp2_atr_multiplier=_decimal(tp_raw["tp2_atr_multiplier"]),
        ),
        rebuy=RebuyConfig(
            enabled=bool(rebuy_raw["enabled"]),
            minimum_score=int(rebuy_raw["minimum_score"]),
            minimum_reversal_score=int(rebuy_raw["minimum_reversal_score"]),
            min_drop_from_avg=_decimal(rebuy_raw["min_drop_from_avg"]),
            max_rebuy_per_cycle=int(rebuy_raw["max_rebuy_per_cycle"]),
            recovery=RebuyConditionConfig(
                values={k: v for k, v in rebuy_raw["recovery"].items() if k != "mode"},
                mode=str(rebuy_raw["recovery"]["mode"]),
            ),
            reoversold=RebuyConditionConfig(
                values={k: v for k, v in rebuy_raw["reoversold"].items() if k != "mode"},
                mode=str(rebuy_raw["reoversold"]["mode"]),
            ),
        ),
        risk_review=RiskReviewConfig(
            info_days=int(risk_raw["info_days"]),
            review_days=int(risk_raw["review_days"]),
            high_days=int(risk_raw["high_days"]),
        ),
        scheduler=SchedulerConfig(
            signal_delay_minutes=int(scheduler_raw["signal_delay_minutes"]),
            poll_interval_seconds=int(scheduler_raw["poll_interval_seconds"]),
            order_monitor_interval_seconds=int(scheduler_raw["order_monitor_interval_seconds"]),
        ),
        backtest=BacktestConfig(
            default_start=str(backtest_raw["default_start"]),
            default_slippage=_decimal(backtest_raw["default_slippage"]),
            annualization_days=int(backtest_raw["annualization_days"]),
        ),
    )
    validate_config(config)
    return config


def validate_config(config: StrategyConfig) -> None:
    errors: list[str] = []
    if not 0 <= config.global_.entry_score <= 100:
        errors.append("entry_score는 0~100이어야 합니다")
    if sum(config.position.stage_weights) != Decimal("1"):
        errors.append("stage_weights 합계는 1.0이어야 합니다")
    if config.position.cumulative_weights[-1] != Decimal("1"):
        errors.append("cumulative_weights 마지막 값은 1.0이어야 합니다")
    expected_cumulative: list[Decimal] = []
    total = Decimal("0")
    for weight in config.position.stage_weights:
        if weight <= 0:
            errors.append("각 stage weight는 양수여야 합니다")
        total += weight
        expected_cumulative.append(total)
    if tuple(expected_cumulative) != config.position.cumulative_weights:
        errors.append("cumulative_weights가 stage_weights의 누적합과 다릅니다")
    drops = [config.additional_entry.stages[stage].min_drop_from_anchor for stage in (2, 3, 4)]
    scores = [config.additional_entry.stages[stage].min_score for stage in (2, 3, 4)]
    if not drops[0] < drops[1] < drops[2]:
        errors.append("추가매수 하락폭은 2차 < 3차 < 4차여야 합니다")
    if not scores[0] <= scores[1] <= scores[2]:
        errors.append("추가매수 점수는 2차 <= 3차 <= 4차여야 합니다")
    if config.take_profit.tp1_base >= config.take_profit.tp2_base:
        errors.append("TP1은 TP2보다 작아야 합니다")
    if config.global_.buy_fee < 0 or config.global_.sell_fee < 0:
        errors.append("수수료는 0 이상이어야 합니다")
    if config.global_.capital_per_symbol <= 0:
        errors.append("종목별 자금은 양수여야 합니다")
    if config.global_.review_token_ttl_minutes <= 0:
        errors.append("검토 토큰 TTL은 양수여야 합니다")
    if config.global_.execution_token_ttl_seconds <= 0:
        errors.append("실행 토큰 TTL은 양수여야 합니다")
    if config.global_.entry_max_chase_pct < 0:
        errors.append("추격매수 상한은 0 이상이어야 합니다")
    if config.global_.stop_loss_enabled:
        errors.append("JDSS v1.1.2는 자동 손절을 허용하지 않습니다")
    if errors:
        raise ConfigError("; ".join(errors))
