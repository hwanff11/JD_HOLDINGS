from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from threading import Lock

import pandas as pd
import yfinance as yf

from jd_holdings.core.indicators import MarketDataError, normalize_ohlcv

LOGGER = logging.getLogger(__name__)
# The canonical runner asks for 420 calendar days of extra warmup. Leveraged
# ETFs began a few months after that warmup start, so allow their real inception
# gap while still rejecting a cache that starts years into the requested history.
CACHE_START_GRACE_DAYS = 180


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
            try:
                return self._read_cache(cache_path, start, end)
            except (OSError, ValueError, KeyError, MarketDataError):
                LOGGER.warning("손상된 정확 일치 시세 캐시를 건너뜁니다: %s", cache_path)
        if not refresh:
            covering = self._best_cache(symbol, start, end, require_full_range=True)
            if covering is not None:
                return covering

        end_exclusive: str | None = None
        if end:
            end_date = pd.Timestamp(end).date() + timedelta(days=1)
            end_exclusive = end_date.isoformat()
        try:
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
        except Exception as exc:
            if not refresh:
                fallback = self._best_cache(
                    symbol, start, end, require_full_range=False
                )
                if fallback is not None:
                    LOGGER.warning(
                        "%s yfinance 조회 실패로 캐시 최신일 %s까지 사용합니다: %s",
                        symbol,
                        fallback.index[-1].date(),
                        exc,
                    )
                    return fallback
            raise MarketDataError(f"yfinance 일봉 조회 실패: {symbol}") from exc
        if frame is None or frame.empty:
            if not refresh:
                fallback = self._best_cache(
                    symbol, start, end, require_full_range=False
                )
                if fallback is not None:
                    LOGGER.warning(
                        "%s yfinance 응답이 비어 캐시 최신일 %s까지 사용합니다",
                        symbol,
                        fallback.index[-1].date(),
                    )
                    return fallback
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

    @staticmethod
    def _slice_frame(
        frame: pd.DataFrame,
        start: str | date,
        end: str | date | None,
    ) -> pd.DataFrame:
        start_ts = pd.Timestamp(start)
        selected = frame.loc[frame.index >= start_ts]
        if end is not None:
            selected = selected.loc[selected.index <= pd.Timestamp(end)]
        return selected

    def _read_cache(
        self,
        path: Path,
        start: str | date,
        end: str | date | None,
    ) -> pd.DataFrame:
        return self._slice_frame(self._read_cache_full(path), start, end)

    @staticmethod
    def _read_cache_full(path: Path) -> pd.DataFrame:
        cached = pd.read_csv(path, index_col=0, parse_dates=True)
        return normalize_ohlcv(cached)

    def _best_cache(
        self,
        symbol: str,
        start: str | date,
        end: str | date | None,
        *,
        require_full_range: bool,
    ) -> pd.DataFrame | None:
        if self.cache_dir is None:
            return None
        requested_start = pd.Timestamp(start)
        requested_end = pd.Timestamp(end) if end is not None else None
        candidates: list[pd.DataFrame] = []
        for path in self.cache_dir.glob(f"{symbol.upper()}_*_adjusted.csv"):
            try:
                full_frame = self._read_cache_full(path)
            except (OSError, ValueError, KeyError, MarketDataError):
                LOGGER.warning("손상된 시세 캐시를 건너뜁니다: %s", path)
                continue
            if full_frame.empty:
                continue
            # An offline fallback may end before the request. A small start gap
            # is allowed because the runner deliberately requests extra calendar
            # warmup before the oldest cached market session, but a recent-only
            # cache must never impersonate a full-history data set.
            if full_frame.index[0] > requested_start + pd.Timedelta(
                days=CACHE_START_GRACE_DAYS
            ):
                continue
            if require_full_range and (
                requested_end is None or full_frame.index[-1] < requested_end
            ):
                continue
            frame = self._slice_frame(full_frame, start, end)
            if frame.empty:
                continue
            candidates.append(frame)
        if not candidates:
            return None
        return max(candidates, key=lambda value: (value.index[-1], len(value)))
