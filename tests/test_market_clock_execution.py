from __future__ import annotations

from datetime import UTC, date, datetime

from jd_holdings.infrastructure.market_clock import (
    MarketClock,
    is_toss_order_maintenance_window,
)


def test_next_session_gate_blocks_signal_day_after_hours():
    clock = MarketClock()

    assert not clock.next_session_has_started(
        date(2026, 8, 3),
        datetime(2026, 8, 3, 21, 0, tzinfo=UTC),
    )
    assert clock.next_session_has_started(
        date(2026, 8, 3),
        datetime(2026, 8, 4, 10, 0, tzinfo=UTC),
    )


def test_next_session_skips_weekend():
    assert MarketClock().next_session_date(date(2026, 8, 7)) == date(2026, 8, 10)


def test_toss_order_maintenance_window_uses_seoul_time():
    assert is_toss_order_maintenance_window(
        datetime(2026, 8, 10, 23, 55, tzinfo=UTC)
    )
    assert not is_toss_order_maintenance_window(
        datetime(2026, 8, 11, 0, 0, tzinfo=UTC)
    )
