from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from jd_holdings import __version__
from jd_holdings.config import StrategyConfig
from jd_holdings.core.execution import max_chase_price
from jd_holdings.core.indicators import calculate_indicators, snapshot_from_row
from jd_holdings.core.models import IndicatorSnapshot, ScoreResult, TradeDecision
from jd_holdings.core.regime import evaluate_regime
from jd_holdings.core.scoring import calculate_score
from jd_holdings.core.strategy import evaluate_rebuy_recovery, evaluate_strategy
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource

from .database import SQLiteRepository


@dataclass(frozen=True)
class AnalysisResult:
    symbol: str
    trade_date: date
    snapshot: IndicatorSnapshot
    score: ScoreResult
    decision: TradeDecision
    signal_id: int | None
    signal_created: bool


class AnalysisService:
    def __init__(
        self,
        config: StrategyConfig,
        repository: SQLiteRepository,
        data_source: YFinanceDataSource,
        market_clock: MarketClock | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.data_source = data_source
        self.market_clock = market_clock or MarketClock()

    def analyze_all(self, now: datetime | None = None) -> list[AnalysisResult]:
        current = now or datetime.now(UTC)
        completed = self.market_clock.latest_completed_session(
            current, delay_minutes=self.config.scheduler.signal_delay_minutes
        )
        start = completed - timedelta(days=500)
        spy = calculate_indicators(
            self.data_source.daily("SPY", start, completed, refresh=True), self.config
        )
        qqq = calculate_indicators(
            self.data_source.daily("QQQ", start, completed, refresh=True), self.config
        )
        results: list[AnalysisResult] = []
        for symbol in self.config.enabled_symbols:
            target = calculate_indicators(
                self.data_source.daily(symbol, start, completed, refresh=True), self.config
            )
            results.append(self._analyze(symbol, completed, target, spy, qqq))
        self.repository.set_system_value("last_analysis_trade_date", completed.isoformat())
        self.repository.set_system_value("last_analysis_at", current.astimezone(UTC).isoformat())
        return results

    def _analyze(self, symbol, completed, target, spy, qqq) -> AnalysisResult:
        timestamp = next(
            (
                index
                for index in target.index
                if index.date() == completed and index in spy.index and index in qqq.index
            ),
            None,
        )
        if timestamp is None:
            raise ValueError(f"{symbol}/SPY/QQQ 완결 일봉 거래일이 일치하지 않습니다: {completed}")
        snapshot = snapshot_from_row(symbol, timestamp, target.loc[timestamp])
        spy_snapshot = snapshot_from_row("SPY", timestamp, spy.loc[timestamp])
        qqq_snapshot = snapshot_from_row("QQQ", timestamp, qqq.loc[timestamp])
        regime = evaluate_regime(spy_snapshot, qqq_snapshot)
        score = calculate_score(snapshot, regime, self.config)
        position = self.repository.get_position(symbol)

        if (
            position.state.value == "PARTIAL_TP_1"
            and not position.rebuy_recovery_armed
            and evaluate_rebuy_recovery(snapshot, self.config)
        ):
            position = self.repository.transition_position(
                symbol,
                expected_state=position.state,
                new_state=position.state,
                reason_code="REBUY_RECOVERY_ARMED",
                updates={"rebuy_recovery_armed": True},
                expected_version=position.version,
            )
        decision = evaluate_strategy(snapshot, score, position, self.config)
        signal_id = None
        created = False
        if decision.allowed:
            signal_id, created = self.repository.create_signal(
                symbol=symbol,
                trade_date=completed,
                score=score,
                atr_pct=Decimal(str(snapshot.atr_pct)),
                decision=decision,
                signal_close=snapshot.close,
                max_chase_price=max_chase_price(snapshot.close, self.config),
                valid_until=self.market_clock.next_session_close(completed),
                code_version=__version__,
                cycle_id=position.cycle_id,
            )
            if created:
                self.repository.log_event(
                    "INFO",
                    "SIGNAL_CREATED",
                    f"{decision.action.value} {score.total}점",
                    symbol=symbol,
                    context={"signal_id": signal_id, "trade_date": completed.isoformat()},
                )
        return AnalysisResult(
            symbol=symbol,
            trade_date=completed,
            snapshot=snapshot,
            score=score,
            decision=decision,
            signal_id=signal_id,
            signal_created=created,
        )
