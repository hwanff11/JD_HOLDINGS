from __future__ import annotations

from datetime import date

import pytest

from jd_holdings.infrastructure.telegram_bot import (
    BacktestCommandError,
    parse_backtest_request,
)

SYMBOLS = ("TQQQ", "SOXL")
LATEST = date(2026, 8, 4)


def test_backtest_command_defaults_to_all_symbols_and_full_period():
    request = parse_backtest_request("/bt", SYMBOLS, "2011-01-01", LATEST)
    assert request.symbols == SYMBOLS
    assert request.start == date(2011, 1, 1)
    assert request.end == LATEST


def test_backtest_command_accepts_symbol_and_date_range():
    request = parse_backtest_request(
        "/backtest tqqq 2021-01-01 2024-12-31",
        SYMBOLS,
        "2011-01-01",
        LATEST,
    )
    assert request.symbols == ("TQQQ",)
    assert request.start == date(2021, 1, 1)
    assert request.end == date(2024, 12, 31)


def test_backtest_command_accepts_all_with_start_only():
    request = parse_backtest_request(
        "/bt ALL 2025-01-01", SYMBOLS, "2011-01-01", LATEST
    )
    assert request.symbols == SYMBOLS
    assert request.start == date(2025, 1, 1)
    assert request.end == LATEST


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ("/bt NVDA", "지원 종목"),
        ("/bt ALL 2025/01/01", "날짜"),
        ("/bt ALL 2010-12-31", "시작일"),
        ("/bt ALL 2025-01-01 2024-01-01", "늦을 수 없습니다"),
        ("/bt ALL 2025-01-01 2026-08-05", "최신 완결 거래일"),
        ("/bt ALL 2025-01-01 2026-08-04 extra", "형식"),
    ],
)
def test_backtest_command_rejects_unsafe_or_invalid_input(command, message):
    with pytest.raises(BacktestCommandError, match=message):
        parse_backtest_request(command, SYMBOLS, "2011-01-01", LATEST)
