from __future__ import annotations

import tomllib
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from conftest import make_score, make_snapshot

from jd_holdings import __version__
from jd_holdings.core.enums import DecisionType
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.telegram_bot import (
    IDLE_CASH_COMMANDS,
    BacktestCommandError,
    TelegramBotApp,
    _format_idle_cash_event,
    _guide_cards,
    _is_toss_order_maintenance_window,
    parse_history_request,
    _profit_loss,
    _regime_label,
    _won,
    parse_backtest_request,
)

SYMBOLS = ("TQQQ", "SOXL")
LATEST = date(2026, 8, 4)
SEOUL_TZ = ZoneInfo("Asia/Seoul")


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 8, 11, 8, 49, tzinfo=SEOUL_TZ), False),
        (datetime(2026, 8, 11, 8, 50, tzinfo=SEOUL_TZ), True),
        (datetime(2026, 8, 11, 8, 59, tzinfo=SEOUL_TZ), True),
        (datetime(2026, 8, 11, 9, 0, tzinfo=SEOUL_TZ), False),
    ],
)
def test_toss_order_maintenance_window(now, expected):
    assert _is_toss_order_maintenance_window(now) is expected


def test_history_command_defaults_to_all_symbols_and_seven_days():
    assert parse_history_request("/history", ("TQQQ", "SOXL")) == (("TQQQ", "SOXL"), 7)
    assert parse_history_request("/h 10", ("TQQQ", "SOXL")) == (("TQQQ", "SOXL"), 10)


def test_history_command_accepts_symbol_and_days():
    assert parse_history_request("/history TQQQ 14", ("TQQQ", "SOXL")) == (("TQQQ",), 14)


@pytest.mark.parametrize("command", ["/h 0", "/h 91", "/h TQQQ 7 extra", "/h NVDA 7"])
def test_history_command_rejects_invalid_input(command):
    with pytest.raises(ValueError):
        parse_history_request(command, ("TQQQ", "SOXL"))


def test_backtest_command_defaults_to_soxl_and_300_trading_days():
    request = parse_backtest_request("/bt", SYMBOLS, "2011-01-01", LATEST)
    assert request.symbols == ("SOXL",)
    calendar = MarketClock().calendar
    assert len(calendar.sessions_in_range(request.start, request.end)) == 300
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
        "rate": "0.01234",
        "rateAfterCost": "0.01018",
    }
    assert _profit_loss(value) == ("$10.13", "+1.02%")


def test_won_formats_with_thousands_separator():
    assert _won("1234567.4") == "₩1,234,567"


def test_dry_run_sgov_fill_alert_is_explicitly_labeled():
    event = "SGOV 유휴자금 예치 196주 (FILLED)"

    assert _format_idle_cash_event(event, "dry_run") == (
        "🧪 모의체결 — SGOV 유휴자금 예치 196주 (FILLED)"
    )
    assert _format_idle_cash_event(event, "live") == (
        "💵 SGOV 유휴자금 예치 196주 (FILLED)"
    )


def test_dry_run_sgov_non_fill_event_is_labeled_as_simulation():
    event = "SGOV SGOV_SWEEP_BUY 미체결 잔량 재가격 준비"

    assert _format_idle_cash_event(event, "dry_run").startswith("🧪 모의처리 —")


@pytest.mark.parametrize(
    ("regime", "label"),
    [
        ("GREEN", "🟢 강세장"),
        ("YELLOW", "🟡 중립장"),
        ("RED", "🔴 약세장"),
    ],
)
def test_regime_label_has_visible_color(regime, label):
    assert _regime_label(regime) == label


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
            {
                "date": "2026-07-16",
                "side": "SELL",
                "purpose": "REMAINDER_EXIT",
                "quantity": 1,
                "price": 52.0,
            },
        ),
        skipped_signals=(
            {
                "execution_date": "2026-07-03",
                "reason": "SKIPPED_BY_CHASE_RULE",
            },
        ),
    )
    timeline = TelegramBotApp._format_trade_timeline(result)
    assert "1차매수" in timeline[0]
    assert "매수미체결" in timeline[1]
    assert "1차익절" in timeline[2]
    assert "2차완청" in timeline[3]
    assert "잔여청산" in timeline[4]


def test_final_code_version_matches_strategy_release(config):
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    assert __version__ == "2.2.2"
    assert project["version"] == __version__
    assert config.config_version == __version__


def test_sgov_is_the_only_idle_cash_command():
    assert IDLE_CASH_COMMANDS == ("sgov",)


def test_telegram_guide_matches_final_contract():
    cards = _guide_cards()
    guide = "\n".join(cards)
    for expected in ("2.2", "55점", "-2%", "-5%", "-7%", "+4%", "+6%", "+2%", "SGOV", "/sgov"):
        assert expected in guide
    for expected in ("CCI 5 / 10", "RSI 5 / 14", "EMA 5 / 20 / 60", "볼린저 하단", "ATR 비율", "종가 위치"):
        assert expected in guide
    assert len(cards) == 4
    assert all(len(card) < 4096 for card in cards)
    assert "50점" not in guide
    assert "-4% 하락" not in guide
    assert "+8.0%" not in guide


def test_score_message_explains_indicators_and_final_gates():
    result = SimpleNamespace(
        symbol="TQQQ",
        snapshot=make_snapshot(),
        score=make_score(55, reversal=5),
        decision=SimpleNamespace(action=DecisionType.NO_ACTION),
    )

    message = TelegramBotApp._format_score_message(result)

    for expected in (
        "강한 과매도",
        "단기 반등 미확인",
        "하단 접촉·이탈",
        "거래 활발",
        "전략 적정 변동성",
        "당일 고가권 마감",
        "총점 55점 이상 : ✅ 충족",
        "반등 5점 이상 : ✅ 충족",
        "RED 국면 아님 : ✅ 충족",
        "현재 매수 조건 미충족",
    ):
        assert expected in message
    assert len(message) < 4096


def test_backtest_timeline_defaults_to_latest_15_events():
    result = SimpleNamespace(
        signals=(),
        skipped_signals=(),
        trades=tuple(
            {
                "date": f"2026-07-{day:02d}",
                "side": "BUY",
                "purpose": "FIRST_ENTRY_CANDIDATE",
                "quantity": 1,
                "price": 10,
                "score": 55,
                "cycle_id": str(day),
            }
            for day in range(1, 17)
        ),
    )
    timeline = TelegramBotApp._format_trade_timeline(result)
    assert len(timeline) == 16
    assert "전체 16건 중 최근 15건" in timeline[0]


def test_backtest_command_accepts_arbitrary_ticker():
    request = parse_backtest_request("/bt NVDA 100", SYMBOLS, "2011-01-01", LATEST)
    assert request.symbols == ("NVDA",)


@pytest.mark.parametrize(
    ("command", "message"),
    [
        ("/bt VERYLONGSYMBOLNAME12345", "유효한 종목 티커"),
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
