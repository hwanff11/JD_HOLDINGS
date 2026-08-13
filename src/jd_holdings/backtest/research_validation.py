from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from jd_holdings.config import StrategyConfig


@dataclass(frozen=True)
class StageThresholds:
    s1: int
    s2: int
    s3: int

    @classmethod
    def from_config(cls, config: StrategyConfig) -> "StageThresholds":
        return cls(
            s1=int(config.global_.entry_score),
            s2=int(config.additional_entry.stages[2].min_score),
            s3=int(config.additional_entry.stages[3].min_score),
        )

    def for_stage(self, stage: int) -> int:
        if stage == 1:
            return self.s1
        if stage == 2:
            return self.s2
        if stage == 3:
            return self.s3
        raise ValueError(f"unsupported research stage: {stage}")


def assert_threshold_override(
    baseline: StrategyConfig,
    candidate: StrategyConfig,
    expected: StageThresholds,
) -> None:
    """Fail fast when a research helper did not actually change the requested floors."""
    actual = StageThresholds.from_config(candidate)
    if actual != expected:
        raise AssertionError(f"threshold override mismatch: expected={expected}, actual={actual}")

    baseline_thresholds = StageThresholds.from_config(baseline)
    if expected != baseline_thresholds and actual == baseline_thresholds:
        raise AssertionError("candidate thresholds unexpectedly equal baseline thresholds")


def assert_signal_threshold_contract(
    signals: Iterable[Mapping[str, Any]],
    thresholds: StageThresholds,
) -> None:
    """Every emitted staged-entry signal must satisfy its configured score floor."""
    for signal in signals:
        stage_value = signal.get("target_stage")
        if stage_value not in (1, 2, 3):
            continue
        stage = int(stage_value)
        score = int(signal.get("score", -1))
        required = thresholds.for_stage(stage)
        if score < required:
            date = signal.get("trade_date", "unknown")
            symbol = signal.get("symbol", "unknown")
            raise AssertionError(
                f"signal below configured floor: symbol={symbol} date={date} "
                f"stage={stage} score={score} required={required}"
            )


def assert_threshold_change_has_effect_when_binding(
    *,
    baseline_signals: Iterable[Mapping[str, Any]],
    candidate_signals: Iterable[Mapping[str, Any]],
    baseline_thresholds: StageThresholds,
    candidate_thresholds: StageThresholds,
    baseline_trades: Iterable[Mapping[str, Any]] | None = None,
    candidate_trades: Iterable[Mapping[str, Any]] | None = None,
) -> None:
    """Catch research harnesses that claim to change a binding floor but produce identical output.

    The assertion only fires when the baseline emitted at least one signal in the score band that a
    stricter candidate should exclude. If no baseline signal lies in that band, identical results are
    legitimate and this check remains silent.
    """
    baseline_rows = [dict(row) for row in baseline_signals]
    candidate_rows = [dict(row) for row in candidate_signals]

    binding_examples: list[dict[str, Any]] = []
    for row in baseline_rows:
        stage_value = row.get("target_stage")
        if stage_value not in (1, 2, 3):
            continue
        stage = int(stage_value)
        before = baseline_thresholds.for_stage(stage)
        after = candidate_thresholds.for_stage(stage)
        if after <= before:
            continue
        score = int(row.get("score", -1))
        if before <= score < after:
            binding_examples.append(row)

    if not binding_examples:
        return

    signals_equal = baseline_rows == candidate_rows
    trades_equal = True
    if baseline_trades is not None and candidate_trades is not None:
        trades_equal = [dict(row) for row in baseline_trades] == [dict(row) for row in candidate_trades]

    if signals_equal and trades_equal:
        example = binding_examples[0]
        raise AssertionError(
            "binding threshold changed but research output stayed identical; "
            f"example={example} baseline={baseline_thresholds} candidate={candidate_thresholds}"
        )
