from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from jd_holdings.infrastructure.telegram_bot import (
    BacktestCommandError,
    TelegramBotApp,
    _profit_loss,
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
    request = parse_backtest_request("/bt ALL 2025-01-01", SYMBOLS, "2011-01-01", LATEST)
    assert request.symbols == SYMBOLS
    assert request.start == date(2025, 1, 1)
    assert request.end == LATEST


def test_backtest_command_accepts_recent_trading_days():
    request = parse_backtest_request("/bt TQQQ 100", SYMBOLS, "2011-01-01", LATEST)
    assert request.symbols == ("TQQQ",)
    assert request.start == date(2026, 3, 12)
    assert request.end == LATEST


def test_backtest_command_accepts_all_recent_trading_days():
    request = parse_backtest_request("/bt ALL 250", SYMBOLS, "2011-01-01", LATEST)
    assert request.symbols == SYMBOLS
    assert request.start == date(2025, 8, 6)
    assert request.end == LATEST


def test_profit_loss_uses_after_cost_values():
    value = {
        "amount": "12.345",
        "amountAfterCost": "10.126",
        "rate": "1.234",
        "rateAfterCost": "1.018",
    }
    assert _profit_loss(value) == ("$10.13", "+1.02%")


def test_backtest_timeline_includes_signal_buy_and_take_profit_sales():
    result = SimpleNamespace(
        signals=(
            {
                "trade_date": "2026-07-01",
                "action": "FIRST_ENTRY_CANDIDATE",
                "score": 88,
                "signal_close": 50.125,
            },
        ),
        trades=(
            {
                "date": "2026-07-02",
                "side": "BUY",
                "purpose": "FIRST_ENTRY_CANDIDATE",
                "quantity": 10,
                "price": 50.255,
            },
            {
                "date": "2026-07-10",
                "side": "SELL",
                "purpose": "TP1",
                "quantity": 5,
                "price": 53.125,
            },
            {
                "date": "2026-07-15",
                "side": "SELL",
                "purpose": "TP2",
                "quantity": 5,
                "price": 55.555,
            },
        ),
    )
    timeline = TelegramBotApp._format_trade_timeline(result)
    assert "1차 매수 신호" in timeline[0]
    assert "1차 매수" in timeline[1]
    assert "1차 매도" in timeline[2]
    assert "2차 매도" in timeline[3]


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ("/bt NVDA", "지원 종목"),
        ("/bt ALL 2025/01/01", "날짜"),
        ("/bt ALL 2010-12-31", "시작일"),
        ("/bt ALL 2025-01-01 2024-01-01", "늦을 수 없습니다"),
        ("/bt ALL 2025-01-01 2026-08-05", "최신 완결 거래일"),
        ("/bt ALL 2025-01-01 2026-08-04 extra", "형식"),
        ("/bt TQQQ 0", "1~5000"),
        ("/bt TQQQ 5001", "1~5000"),
    ],
)
def test_backtest_command_rejects_unsafe_or_invalid_input(command, message):
    with pytest.raises(BacktestCommandError, match=message):
        parse_backtest_request(command, SYMBOLS, "2011-01-01", LATEST)
