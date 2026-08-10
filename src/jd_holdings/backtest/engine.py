from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd

from jd_holdings.config import StrategyConfig
from jd_holdings.core.enums import DecisionType, MarketRegime, PositionState
from jd_holdings.core.execution import (
    calculate_execution_price_ceiling,
    calculate_order_quantity,
)
from jd_holdings.core.indicators import calculate_indicators, snapshot_from_row
from jd_holdings.core.models import (
    IndicatorSnapshot,
    PositionSnapshot,
    ScoreResult,
    TakeProfitPlan,
    TradeDecision,
)
from jd_holdings.core.regime import evaluate_regime
from jd_holdings.core.scoring import calculate_score
from jd_holdings.core.strategy import (
    evaluate_rebuy_recovery,
    evaluate_strategy,
    expected_holding_state,
)
from jd_holdings.core.take_profit import calculate_take_profit

from .performance import summarize_performance


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    start_date: date
    end_date: date
    strategy_version: str
    config_version: str
    slippage: float
    metrics: dict[str, float | int]
    trades: tuple[dict[str, Any], ...]
    signals: tuple[dict[str, Any], ...]
    skipped_signals: tuple[dict[str, Any], ...]
    closed_cycles: tuple[dict[str, Any], ...]
    open_position: dict[str, Any]
    equity_curve: pd.Series = field(repr=False, compare=False)

    def to_dict(self, *, include_equity: bool = False) -> dict[str, Any]:
        result = {
            "symbol": self.symbol,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "strategy_version": self.strategy_version,
            "config_version": self.config_version,
            "slippage": self.slippage,
            "metrics": self.metrics,
            "trades": list(self.trades),
            "signals": list(self.signals),
            "skipped_signals": list(self.skipped_signals),
            "closed_cycles": list(self.closed_cycles),
            "open_position": self.open_position,
        }
        if include_equity:
            result["equity_curve"] = {
                timestamp.date().isoformat(): round(float(value), 2)
                for timestamp, value in self.equity_curve.items()
            }
        return result


@dataclass
class _Pending:
    snapshot: IndicatorSnapshot
    score: ScoreResult
    decision: TradeDecision


@dataclass
class _SimulationState:
    symbol: str
    capital: Decimal
    cash: Decimal
    state: PositionState = PositionState.EMPTY
    cycle_id: str | None = None
    quantity: int = 0
    average_price: Decimal = Decimal("0")
    current_cost_basis: Decimal = Decimal("0")
    cycle_exposure_cap: Decimal = Decimal("0")
    staged_entry_capital: Decimal = Decimal("0")
    entry_count: int = 0
    anchor_price: Decimal = Decimal("0")
    last_entry_price: Decimal = Decimal("0")
    last_entry_date: date | None = None
    rebuy_count: int = 0
    rebuy_recovery_armed: bool = False
    tp1_filled_qty: int = 0
    tp_plan: TakeProfitPlan | None = None
    tp1_done: bool = False
    cycle_number: int = 0
    cycle_start_date: date | None = None
    cycle_start_equity: Decimal = Decimal("0")
    cycle_cashflows: Decimal = Decimal("0")
    cycle_holding_days: int = 0
    cycle_mae: float = 0.0
    cycle_mfe: float = 0.0
    cycle_used_rebuy: bool = False

    def snapshot(self) -> PositionSnapshot:
        return PositionSnapshot(
            symbol=self.symbol,
            state=self.state,
            cycle_id=self.cycle_id,
            quantity=self.quantity,
            average_price=self.average_price,
            current_cost_basis=self.current_cost_basis,
            cycle_exposure_cap=self.cycle_exposure_cap,
            staged_entry_capital=self.staged_entry_capital,
            cash_remaining=self.cash,
            entry_count=self.entry_count,
            anchor_price=self.anchor_price,
            last_entry_price=self.last_entry_price,
            last_entry_date=self.last_entry_date,
            rebuy_count=self.rebuy_count,
            rebuy_recovery_armed=self.rebuy_recovery_armed,
            tp1_filled_qty=self.tp1_filled_qty,
        )


class BacktestEngine:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def run(
        self,
        symbol: str,
        symbol_data: pd.DataFrame,
        spy_data: pd.DataFrame,
        qqq_data: pd.DataFrame,
        *,
        start: str | date | None = None,
        end: str | date | None = None,
        slippage: Decimal | float | None = None,
        indicators_precomputed: bool = False,
        snapshots_precomputed: Mapping[str, Mapping[pd.Timestamp, IndicatorSnapshot]] | None = None,
        regimes_precomputed: Mapping[pd.Timestamp, MarketRegime] | None = None,
        sector_data: Mapping[str, pd.DataFrame] | None = None,
    ) -> BacktestResult:
        """
        주어진 종목(symbol)과 지표 데이터(symbol_data)를 바탕으로 
        이벤트 기반 백테스트 시뮬레이션을 실행합니다.
        
        시뮬레이션 라이프사이클:
        1. **데이터 웜업**: 대상 종목, SPY, QQQ, 섹터 데이터의 공통 거래일을 찾아 무결성을 확보합니다.
        2. **루프 진입 (Daily Loop)**: 매 거래일마다 다음을 수행합니다.
           a. 전일 발생한 '미체결 주문(pending)' 체결 시도 (시가가 허용가 내에 있는지 확인)
           b. 일일 결산 (사이클 유지일 증가, MFE/MAE 갱신, 파산/청산 여부 점검)
           c. 시장 상황(Regime) 판별 및 타겟 종목 스코어링 (S, A, B, WATCH 등급 산출)
           d. 현재 포지션 상태(STATE)에 따른 전략 결정 및 다음날 주문(pending) 생성
        3. **결산 및 통계 산출**: 모든 거래일 종료 후 누적 수익률(CAGR), MDD 등 퍼포먼스 지표를 계산합니다.
        
        Args:
            symbol: 테스트 대상 종목 심볼 (예: TQQQ)
            symbol_data, spy_data, qqq_data: 정규화된 OHLCV 및 지표 데이터프레임
            start, end: 시뮬레이션 기간
            slippage: 슬리피지 비율 (예: 0.0005 = 0.05%)
            
        Returns:
            BacktestResult: 거래 내역, 누적 통계치, 미체결 사유 등이 포함된 결과 객체
        """
        symbol = symbol.upper()
        configured_slippage = (
            slippage if slippage is not None else self.config.backtest.default_slippage
        )
        slip = Decimal(str(configured_slippage))
        
        # 1. 지표 계산 (이미 precomputed 되었다면 생략)
        target = symbol_data if indicators_precomputed else calculate_indicators(symbol_data, self.config)
        spy = spy_data if indicators_precomputed else calculate_indicators(spy_data, self.config)
        qqq = qqq_data if indicators_precomputed else calculate_indicators(qqq_data, self.config)

        # 2. 섹터 가드 설정 (SOXL의 경우에만 활성화되며, SOXX와 SMH의 하락장 여부를 체크)
        sector_frames: dict[str, pd.DataFrame] = {}
        guard = self.config.market_regime.get("soxl_sector_guard", {})
        guard_candidates = tuple(
            str(value).upper() for value in guard.get("benchmark_candidates", ("SOXX", "SMH"))
        )
        guard_requested = symbol == "SOXL" and bool(guard.get("enabled", False))
        if guard_requested and sector_data:
            for benchmark in guard_candidates:
                frame = sector_data.get(benchmark)
                if frame is None:
                    continue
                sector_frames[benchmark] = (
                    frame if indicators_precomputed else calculate_indicators(frame, self.config)
                )
        
        # FINAL의 warn_and_allow 계약에 따라 사용 가능한 벤치마크가 하나라도
        # 있으면 그 데이터로 가드를 적용한다. 모두 누락된 경우에만 허용한다.
        sector_guard_applied = guard_requested and bool(sector_frames)

        # 3. 데이터 공통 인덱스 추출 (대상 종목 + SPY + QQQ + [섹터 가드 종목])
        common_index = target.index.intersection(spy.index).intersection(qqq.index)
        if sector_guard_applied:
            for frame in sector_frames.values():
                common_index = common_index.intersection(frame.index)
        if start:
            common_index = common_index[common_index >= pd.Timestamp(start)]
        if end:
            common_index = common_index[common_index <= pd.Timestamp(end)]

        required_columns = [
            "cci5",
            "cci10",
            "rsi5",
            "rsi14",
            "ema5",
            "ema20",
            "ema60",
            "bb_lower",
            "atr14",
            "atr_pct",
            "volume_ratio",
            "close_position",
            "previous_close",
        ]
        common_index = common_index[
            target.loc[common_index, required_columns].notna().all(axis=1)
            & spy.loc[common_index, required_columns].notna().all(axis=1)
            & qqq.loc[common_index, required_columns].notna().all(axis=1)
        ]
        if sector_guard_applied:
            for frame in sector_frames.values():
                common_index = common_index[
                    frame.loc[common_index, required_columns].notna().all(axis=1)
                ]
        if len(common_index) < 2:
            raise ValueError("백테스트 가능한 공통 거래일이 부족합니다")

        state = _SimulationState(
            symbol=symbol,
            capital=self.config.global_.capital_per_symbol,
            cash=self.config.global_.capital_per_symbol,
        )
        pending: _Pending | None = None
        trades: list[dict[str, Any]] = []
        signals: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        closed_cycles: list[dict[str, Any]] = []
        equity_values: list[float] = []
        equity_dates: list[pd.Timestamp] = []
        capital_utilization: list[float] = []
        executed_entries = 0
        tp1_hits = 0
        tp2_hits = 0
        tp1_reached_cycles: set[str] = set()
        tp2_reached_cycles: set[str] = set()
        rebuy_cycles = 0
        rebuy_profitable_cycles = 0
        sector_guard_blocks = 0

        for timestamp in common_index:
            row = target.loc[timestamp]
            if snapshots_precomputed is None:
                snapshot = snapshot_from_row(symbol, timestamp, row)
            else:
                snapshot = snapshots_precomputed[symbol][timestamp]

            if regimes_precomputed is None:
                spy_snapshot = snapshot_from_row("SPY", timestamp, spy.loc[timestamp])
                qqq_snapshot = snapshot_from_row("QQQ", timestamp, qqq.loc[timestamp])
                regime = evaluate_regime(spy_snapshot, qqq_snapshot)
            else:
                regime = regimes_precomputed[timestamp]
            score = calculate_score(snapshot, regime, self.config)

            sector_benchmarks: dict[str, IndicatorSnapshot] | None = None
            if sector_guard_applied:
                sector_benchmarks = {}
                for benchmark, frame in sector_frames.items():
                    if snapshots_precomputed and benchmark in snapshots_precomputed:
                        sector_benchmarks[benchmark] = snapshots_precomputed[benchmark][timestamp]
                    else:
                        sector_benchmarks[benchmark] = snapshot_from_row(
                            benchmark, timestamp, frame.loc[timestamp]
                        )

            if pending is not None:
                filled, reason = self._execute_pending(
                    pending, state, timestamp, Decimal(str(row["open"])), slip, trades
                )
                if filled:
                    if pending.decision.action == DecisionType.FIRST_ENTRY_CANDIDATE:
                        executed_entries += 1
                    if pending.decision.action == DecisionType.REBUY_CANDIDATE:
                        rebuy_cycles += 1
                else:
                    skipped.append(
                        {
                            "signal_date": pending.snapshot.trade_date.isoformat(),
                            "execution_date": timestamp.date().isoformat(),
                            "action": pending.decision.action.value,
                            "reason": reason,
                            "score": pending.score.total,
                        }
                    )
                pending = None

            if state.quantity > 0 and state.cycle_start_equity > 0:
                state.cycle_holding_days += 1
                low_equity = state.cash + (
                    Decimal(state.quantity)
                    * Decimal(str(row["low"]))
                    * (Decimal("1") - self.config.global_.sell_fee)
                )
                high_equity = state.cash + (
                    Decimal(state.quantity)
                    * Decimal(str(row["high"]))
                    * (Decimal("1") - self.config.global_.sell_fee)
                )
                low_return = float(low_equity / state.cycle_start_equity - Decimal("1"))
                high_return = float(high_equity / state.cycle_start_equity - Decimal("1"))
                state.cycle_mae = min(state.cycle_mae, low_return)
                state.cycle_mfe = max(state.cycle_mfe, high_return)

            cycle_id_before_take_profit = state.cycle_id
            day_tp1, day_tp2, profitable_rebuy = self._process_take_profit(
                state,
                timestamp,
                Decimal(str(row["high"])),
                trades,
                closed_cycles,
            )
            tp1_hits += day_tp1
            tp2_hits += day_tp2
            if day_tp1 and cycle_id_before_take_profit:
                tp1_reached_cycles.add(cycle_id_before_take_profit)
            if day_tp2 and cycle_id_before_take_profit:
                tp2_reached_cycles.add(cycle_id_before_take_profit)
            rebuy_profitable_cycles += profitable_rebuy

            if (
                state.state == PositionState.PARTIAL_TP_1
                and not state.rebuy_recovery_armed
                and evaluate_rebuy_recovery(snapshot, self.config)
            ):
                state.rebuy_recovery_armed = True

            decision = evaluate_strategy(
                snapshot,
                score,
                state.snapshot(),
                self.config,
                sector_benchmarks=sector_benchmarks,
            )
            if "SOXL_SECTOR_GUARD" in decision.reason_codes:
                sector_guard_blocks += 1
            if decision.allowed:
                pending = _Pending(snapshot=snapshot, score=score, decision=decision)
                signals.append(
                    {
                        "trade_date": snapshot.trade_date.isoformat(),
                        "action": decision.action.value,
                        "target_stage": decision.target_stage,
                        "score": score.total,
                        "grade": score.grade.value,
                        "regime": regime.value,
                        "reversal_score": score.reversal_score,
                        "signal_close": float(snapshot.close),
                        "planned_budget": float(decision.planned_budget),
                    }
                )

            mark_to_market = state.cash
            if state.quantity > 0:
                mark_to_market += (
                    Decimal(state.quantity)
                    * snapshot.close
                    * (Decimal("1") - self.config.global_.sell_fee)
                )
            equity_dates.append(timestamp)
            equity_values.append(float(mark_to_market))
            capital_utilization.append(
                float(state.current_cost_basis / state.capital) if state.capital > 0 else 0.0
            )

        if pending is not None:
            skipped.append(
                {
                    "signal_date": pending.snapshot.trade_date.isoformat(),
                    "execution_date": None,
                    "action": pending.decision.action.value,
                    "reason": "NO_NEXT_SESSION_IN_RANGE",
                }
            )

        equity = pd.Series(equity_values, index=pd.DatetimeIndex(equity_dates), name=symbol)
        metrics = summarize_performance(
            equity,
            closed_cycles,
            signal_count=len(signals),
            executed_entries=executed_entries,
            capital_utilization=capital_utilization,
            tp1_hits=tp1_hits,
            tp2_hits=tp2_hits,
            tp1_reached_cycles=len(tp1_reached_cycles),
            tp2_reached_cycles=len(tp2_reached_cycles),
            rebuy_cycles=rebuy_cycles,
            rebuy_profitable_cycles=rebuy_profitable_cycles,
            annualization_days=self.config.backtest.annualization_days,
        )
        metrics["sector_guard_requested"] = int(guard_requested)
        metrics["sector_guard_applied"] = int(sector_guard_applied)
        metrics["sector_guard_available_benchmarks"] = len(sector_frames)
        metrics["sector_guard_blocks"] = sector_guard_blocks

        open_position = {
            "state": state.state.value,
            "quantity": state.quantity,
            "average_price": float(state.average_price),
            "market_price": float(target.loc[common_index[-1], "close"]),
            "current_cost_basis": float(state.current_cost_basis),
            "holding_days": state.cycle_holding_days,
            "mae_pct": round(state.cycle_mae * 100, 2),
            "mfe_pct": round(state.cycle_mfe * 100, 2),
        }
        return BacktestResult(
            symbol=symbol,
            start_date=common_index[0].date(),
            end_date=common_index[-1].date(),
            strategy_version=self.config.version,
            config_version=self.config.config_version,
            slippage=float(slip),
            metrics=metrics,
            trades=tuple(trades),
            signals=tuple(signals),
            skipped_signals=tuple(skipped),
            closed_cycles=tuple(closed_cycles),
            open_position=open_position,
            equity_curve=equity,
        )

    def _execute_pending(
        self,
        pending: _Pending,
        state: _SimulationState,
        timestamp: pd.Timestamp,
        next_open: Decimal,
        slippage: Decimal,
        trades: list[dict[str, Any]],
    ) -> tuple[bool, str | None]:
        decision = pending.decision
        max_chase = pending.snapshot.close * (
            Decimal("1") + self.config.global_.entry_max_chase_pct
        )
        if next_open > max_chase:
            return False, "SKIPPED_BY_CHASE_RULE"
        if (
            decision.action
            in {DecisionType.ADD_ENTRY_CANDIDATE, DecisionType.REBUY_CANDIDATE}
            and decision.stage_trigger_price is not None
            and next_open > decision.stage_trigger_price
        ):
            return False, "STAGE_PRICE_RECOVERED"

        fill_price = next_open * (Decimal("1") + slippage)
        ceiling = calculate_execution_price_ceiling(
            decision.action,
            pending.snapshot.close,
            self.config,
            stage_trigger_price=decision.stage_trigger_price,
            average_price=state.average_price,
        )
        if fill_price > ceiling:
            return False, "SLIPPAGE_EXCEEDS_PRICE_CEILING"
        maximum_quantity = (
            state.tp1_filled_qty if decision.action == DecisionType.REBUY_CANDIDATE else None
        )
        quantity = calculate_order_quantity(
            min(decision.planned_budget, state.cash),
            fill_price,
            self.config.global_.buy_fee,
            maximum_quantity,
        )
        if quantity < 1:
            return False, "ZERO_QUANTITY"
        gross = Decimal(quantity) * fill_price * (Decimal("1") + self.config.global_.buy_fee)
        if gross > state.cash or gross > decision.planned_budget:
            return False, "EXPOSURE_BLOCK"

        cash_before = state.cash
        prior_qty = state.quantity
        prior_cost = state.current_cost_basis
        new_cost = prior_cost + Decimal(quantity) * fill_price
        state.quantity += quantity
        state.average_price = new_cost / Decimal(state.quantity)
        state.current_cost_basis = new_cost
        state.cash -= gross
        state.cycle_cashflows -= gross
        state.last_entry_price = fill_price
        state.last_entry_date = timestamp.date()
        purpose = decision.action.value

        if decision.action == DecisionType.FIRST_ENTRY_CANDIDATE:
            state.cycle_number += 1
            state.cycle_id = f"BT-{state.symbol}-{state.cycle_number:05d}"
            state.cycle_start_date = timestamp.date()
            state.cycle_start_equity = cash_before
            state.cycle_holding_days = 0
            state.cycle_mae = 0.0
            state.cycle_mfe = 0.0
            state.cycle_used_rebuy = False
            state.entry_count = 1
            state.anchor_price = fill_price
            state.cycle_exposure_cap = decision.cycle_exposure_cap
            state.staged_entry_capital = gross
            state.state = PositionState.HOLDING_1ST
            state.tp1_filled_qty = 0
            state.rebuy_count = 0
            state.rebuy_recovery_armed = False
        elif decision.action == DecisionType.ADD_ENTRY_CANDIDATE:
            target_stage = int(decision.target_stage or (state.entry_count + 1))
            state.entry_count = target_stage
            state.cycle_exposure_cap = decision.cycle_exposure_cap
            state.staged_entry_capital += gross
            state.state = expected_holding_state(target_stage)
        else:
            state.rebuy_count += 1
            state.cycle_used_rebuy = True
            state.rebuy_recovery_armed = False
            state.state = PositionState.HOLDING_REBUY

        state.tp_plan = calculate_take_profit(
            state.average_price,
            state.quantity,
            Decimal(str(pending.snapshot.atr_pct)),
            self.config,
        )
        state.tp1_done = False
        trades.append(
            {
                "date": timestamp.date().isoformat(),
                "cycle_id": state.cycle_id,
                "side": "BUY",
                "purpose": purpose,
                "price": round(float(fill_price), 4),
                "quantity": quantity,
                "fee": round(float(gross - Decimal(quantity) * fill_price), 4),
                "cash_after": round(float(state.cash), 2),
                "average_price": round(float(state.average_price), 4),
                "prior_quantity": prior_qty,
                "score": pending.score.total,
            }
        )
        return True, None

    def _process_take_profit(
        self,
        state: _SimulationState,
        timestamp: pd.Timestamp,
        high: Decimal,
        trades: list[dict[str, Any]],
        closed_cycles: list[dict[str, Any]],
    ) -> tuple[int, int, int]:
        if state.quantity <= 0 or state.tp_plan is None:
            return 0, 0, 0
        tp1_hits = tp2_hits = profitable_rebuy = 0
        if not state.tp1_done and high >= state.tp_plan.tp1_price:
            sell_qty = min(state.tp_plan.tp1_quantity, state.quantity)
            self._sell(state, timestamp, state.tp_plan.tp1_price, sell_qty, "TP1", trades)
            state.tp1_filled_qty = sell_qty
            state.tp1_done = True
            tp1_hits = 1
            if state.quantity > 0:
                state.state = PositionState.PARTIAL_TP_1
                state.rebuy_recovery_armed = False
        if state.quantity > 0 and high >= state.tp_plan.tp2_price:
            self._sell(state, timestamp, state.tp_plan.tp2_price, state.quantity, "TP2", trades)
            tp2_hits = 1
        if state.quantity == 0:
            cycle = {
                "cycle_id": state.cycle_id,
                "start_date": state.cycle_start_date.isoformat() if state.cycle_start_date else None,
                "end_date": timestamp.date().isoformat(),
                "holding_days": state.cycle_holding_days,
                "pnl": round(float(state.cycle_cashflows), 2),
                "mae": state.cycle_mae,
                "mfe": state.cycle_mfe,
                "entry_count": state.entry_count,
                "used_rebuy": state.cycle_used_rebuy,
            }
            closed_cycles.append(cycle)
            if state.cycle_used_rebuy and state.cycle_cashflows > 0:
                profitable_rebuy = 1
            self._reset_cycle(state)
        return tp1_hits, tp2_hits, profitable_rebuy

    def _sell(
        self,
        state: _SimulationState,
        timestamp: pd.Timestamp,
        price: Decimal,
        quantity: int,
        purpose: str,
        trades: list[dict[str, Any]],
    ) -> None:
        quantity = min(quantity, state.quantity)
        gross = Decimal(quantity) * price
        fee = gross * self.config.global_.sell_fee
        net = gross - fee
        state.cash += net
        state.cycle_cashflows += net
        state.quantity -= quantity
        state.current_cost_basis = state.average_price * Decimal(state.quantity)
        trades.append(
            {
                "date": timestamp.date().isoformat(),
                "cycle_id": state.cycle_id,
                "side": "SELL",
                "purpose": purpose,
                "price": round(float(price), 4),
                "quantity": quantity,
                "fee": round(float(fee), 4),
                "cash_after": round(float(state.cash), 2),
            }
        )

    @staticmethod
    def _reset_cycle(state: _SimulationState) -> None:
        state.state = PositionState.EMPTY
        state.cycle_id = None
        state.quantity = 0
        state.average_price = Decimal("0")
        state.current_cost_basis = Decimal("0")
        state.cycle_exposure_cap = Decimal("0")
        state.staged_entry_capital = Decimal("0")
        state.entry_count = 0
        state.anchor_price = Decimal("0")
        state.last_entry_price = Decimal("0")
        state.last_entry_date = None
        state.rebuy_count = 0
        state.rebuy_recovery_armed = False
        state.tp1_filled_qty = 0
        state.tp_plan = None
        state.tp1_done = False
        state.cycle_start_date = None
        state.cycle_start_equity = Decimal("0")
        state.cycle_cashflows = Decimal("0")
        state.cycle_holding_days = 0
        state.cycle_mae = 0.0
        state.cycle_mfe = 0.0
        state.cycle_used_rebuy = False
