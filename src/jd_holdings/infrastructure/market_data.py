from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from threading import Lock

import pandas as pd
import yfinance as yf

from jd_holdings.core.indicators import MarketDataError, normalize_ohlcv

LOGGER = logging.getLogger(__name__)


class YFinanceDataSource:
    """Adjusted daily OHLCV source used by research and strategy analysis."""

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._lock = Lock()
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            # The hardened systemd unit makes the user's home read-only. yfinance
            # otherwise tries ~/.cache/py-yfinance for timezone/cookie/ISIN caches
            # and logs cache failures on every fresh process. Keep all yfinance
            # internal caches under JDSS_CACHE_PATH, which systemd explicitly allows.
            yfinance_cache = self.cache_dir / "yfinance"
            yfinance_cache.mkdir(parents=True, exist_ok=True)
            yf.set_tz_cache_location(str(yfinance_cache))

    def daily(
        self,
        symbol: str,
        start: str | date,
        end: str | date | None = None,
        *,
        refresh: bool = False,
    ) -> pd.DataFrame:
        symbol = symbol.upper()
        cache_path = self._cache_path(symbol, start, end)
        if cache_path and cache_path.exists() and not refresh:
            cached = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            return normalize_ohlcv(cached)

        end_exclusive: str | None = None
        if end:
            end_date = pd.Timestamp(end).date() + timedelta(days=1)
            end_exclusive = end_date.isoformat()
        with self._lock:
            frame = yf.download(
                symbol,
                start=str(start),
                end=end_exclusive,
                interval="1d",
                auto_adjust=True,
                actions=False,
                progress=False,
                threads=False,
                repair=True,
                multi_level_index=False,
            )
        if frame is None or frame.empty:
            raise MarketDataError(f"yfinance 일봉 조회 실패: {symbol}")
        normalized = normalize_ohlcv(frame)
        if cache_path:
            normalized.to_csv(cache_path)
        return normalized

    def current_price(self, symbol: str) -> tuple[pd.Timestamp, float]:
        """Return the newest extended-hours one-minute price available from Yahoo."""
        ticker = yf.Ticker(symbol.upper())
        frame = ticker.history(period="1d", interval="1m", prepost=True, auto_adjust=True)
        if frame is None or frame.empty or "Close" not in frame.columns:
            raise MarketDataError(f"yfinance 현재가 조회 실패: {symbol}")
        valid = frame["Close"].dropna()
        if valid.empty:
            raise MarketDataError(f"yfinance 현재가가 비어 있습니다: {symbol}")
        return pd.Timestamp(valid.index[-1]), float(valid.iloc[-1])

    def _cache_path(self, symbol: str, start: str | date, end: str | date | None) -> Path | None:
        if not self.cache_dir:
            return None
        safe_start = str(start).replace("/", "-")
        safe_end = str(end or "latest").replace("/", "-")
        return self.cache_dir / f"{symbol}_{safe_start}_{safe_end}_adjusted.csv"
