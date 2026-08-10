from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from jd_holdings.config import StrategyConfig


class MarketClock:
    def __init__(self) -> None:
        self.calendar = xcals.get_calendar("XNYS")

    def latest_completed_session(
        self, now: datetime | None = None, *, delay_minutes: int = 5
    ) -> date:
        current = pd.Timestamp(now or datetime.now(UTC))
        if current.tzinfo is None:
            current = current.tz_localize("UTC")
        else:
            current = current.tz_convert("UTC")
        sessions = self.calendar.sessions_in_range(
            (current - pd.Timedelta(days=14)).date(), current.date()
        )
        delay = pd.Timedelta(minutes=delay_minutes)
        for session in reversed(sessions):
            if current >= self.calendar.session_close(session) + delay:
                return pd.Timestamp(session).date()
        raise RuntimeError("최근 완결된 미국 정규장 거래일을 찾을 수 없습니다")

    def completed_sessions_since(
        self,
        start: date | datetime,
        now: datetime | None = None,
    ) -> int:
        start_date = start.date() if isinstance(start, datetime) else start
        latest = self.latest_completed_session(now, delay_minutes=0)
        if latest <= start_date:
            return 0
        sessions = self.calendar.sessions_in_range(start_date, latest)
        return sum(pd.Timestamp(session).date() > start_date for session in sessions)

    def next_session_close(self, session_date: date) -> datetime:
        session = self.calendar.date_to_session(pd.Timestamp(session_date), direction="none")
        next_session = self.calendar.next_session(session)
        close = self.calendar.session_close(next_session)
        return close.to_pydatetime().astimezone(UTC)

    def classify_session(self, now: datetime | None = None) -> str:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        eastern = current.astimezone(ZoneInfo("America/New_York"))
        try:
            session = self.calendar.date_to_session(pd.Timestamp(eastern.date()), direction="none")
        except ValueError:
            return "closed"
        regular_open = self.calendar.session_open(session).to_pydatetime().astimezone(UTC)
        regular_close = self.calendar.session_close(session).to_pydatetime().astimezone(UTC)
        if regular_open <= current <= regular_close:
            return "regular"
        pre_open = regular_open - timedelta(hours=5, minutes=30)
        after_close = regular_close + timedelta(hours=4)
        if pre_open <= current < regular_open:
            return "pre_market"
        if regular_close < current <= after_close:
            return "after_hours"
        return "closed"


def classify_toss_session(calendar_payload: dict[str, Any], now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    session_names = {
        "preMarket": "pre_market",
        "regularMarket": "regular",
        "afterMarket": "after_hours",
        "dayMarket": "day_market",
    }
    for day_key in ("previousBusinessDay", "today", "nextBusinessDay"):
        day = calendar_payload.get(day_key) or {}
        for api_name, internal_name in session_names.items():
            period = day.get(api_name)
            if not period:
                continue
            start = datetime.fromisoformat(period["startTime"])
            end = datetime.fromisoformat(period["endTime"])
            if start <= current.astimezone(start.tzinfo) <= end:
                return internal_name
    return "closed"


def session_is_allowed(session_name: str, config: StrategyConfig) -> bool:
    if session_name == "regular":
        return bool(config.global_.trading_sessions.get("regular", False))
    if session_name == "after_hours":
        return bool(config.global_.trading_sessions.get("after_hours", False))
    if session_name == "pre_market":
        return bool(config.global_.trading_sessions.get("pre_market", False))
    return False
