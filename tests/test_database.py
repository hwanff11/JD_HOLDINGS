from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import make_score, make_snapshot

from jd_holdings.application.database import ApprovalError, SQLiteRepository, StateConflictError
from jd_holdings.core.enums import ApprovalStage, PositionState
from jd_holdings.core.execution import max_chase_price
from jd_holdings.core.strategy import evaluate_entry


@pytest.fixture
def repository(tmp_path, config):
    return SQLiteRepository(tmp_path / "jdss.db", config)


def create_signal(repository, config):
    snapshot = make_snapshot()
    score = make_score(84)
    decision = evaluate_entry(snapshot, score, repository.get_position("TQQQ"), config)
    return repository.create_signal(
        symbol="TQQQ",
        trade_date=date(2026, 8, 4),
        score=score,
        atr_pct=Decimal("0.05"),
        decision=decision,
        signal_close=snapshot.close,
        max_chase_price=max_chase_price(snapshot.close, config),
        valid_until=datetime.now(UTC) + timedelta(days=1),
        code_version="test",
        cycle_id=None,
    )


def test_signal_is_idempotent(repository, config):
    first_id, first_created = create_signal(repository, config)
    second_id, second_created = create_signal(repository, config)
    assert first_created is True
    assert second_created is False
    assert first_id == second_id


def test_approval_token_is_one_time(repository, config):
    signal_id, _ = create_signal(repository, config)
    approval_id, token = repository.create_approval(
        signal_id, ApprovalStage.REVIEW, timedelta(minutes=5)
    )
    consumed_signal, payload = repository.consume_approval(approval_id, token, ApprovalStage.REVIEW)
    assert consumed_signal == signal_id
    assert payload == {}
    with pytest.raises(ApprovalError):
        repository.consume_approval(approval_id, token, ApprovalStage.REVIEW)


def test_optimistic_state_transition(repository):
    position = repository.get_position("TQQQ")
    updated = repository.transition_position(
        "TQQQ",
        expected_state=PositionState.EMPTY,
        new_state=PositionState.WAITING_1ST_FILL,
        reason_code="TEST",
        expected_version=position.version,
    )
    assert updated.version == position.version + 1
    with pytest.raises(StateConflictError):
        repository.transition_position(
            "TQQQ",
            expected_state=PositionState.EMPTY,
            new_state=PositionState.HOLDING_1ST,
            reason_code="STALE",
            expected_version=position.version,
        )
