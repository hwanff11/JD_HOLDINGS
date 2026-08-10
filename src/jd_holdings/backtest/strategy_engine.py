from __future__ import annotations

from jd_holdings.core.enums import PositionState
from jd_holdings.core.remainder_exit import remainder_exit_due, remainder_exit_price

from .engine import BacktestEngine


class StrategyBacktestEngine(BacktestEngine):
    """Backtest engine that applies the production JDSS exit rules.

    Production-facing backtests use the same configured post-TP1 due and price
    functions as live order management. The base engine remains available for
    legacy experiments that intentionally exclude the remainder-exit rule.
    """

    def __init__(self, config) -> None:
        super().__init__(config)
        self._tp1_holding_day: dict[str, int] = {}

    def _process_take_profit(self, state, timestamp, high, trades, closed_cycles):
        cycle_id = state.cycle_id
        tp1_hits, tp2_hits, profitable_rebuy = super()._process_take_profit(
            state,
            timestamp,
            high,
            trades,
            closed_cycles,
        )
        if cycle_id and state.quantity <= 0:
            self._tp1_holding_day.pop(cycle_id, None)
            return tp1_hits, tp2_hits, profitable_rebuy
        if tp1_hits and cycle_id:
            self._tp1_holding_day[cycle_id] = state.cycle_holding_days

        rule = self.config.take_profit.remainder_exit
        if (
            state.state != PositionState.PARTIAL_TP_1
            or not state.tp1_done
            or not state.cycle_id
        ):
            return tp1_hits, tp2_hits, profitable_rebuy

        tp1_day = self._tp1_holding_day.get(state.cycle_id)
        if tp1_day is None:
            return tp1_hits, tp2_hits, profitable_rebuy
        elapsed_sessions = state.cycle_holding_days - tp1_day
        if not remainder_exit_due(elapsed_sessions, rule):
            return tp1_hits, tp2_hits, profitable_rebuy

        exit_price = remainder_exit_price(state.average_price, rule)
        if high < exit_price:
            return tp1_hits, tp2_hits, profitable_rebuy

        used_rebuy = state.cycle_used_rebuy
        cycle_id = state.cycle_id
        self._sell(
            state,
            timestamp,
            exit_price,
            state.quantity,
            "REMAINDER_EXIT",
            trades,
        )
        closed_cycles.append(
            {
                "cycle_id": cycle_id,
                "start_date": (
                    state.cycle_start_date.isoformat() if state.cycle_start_date else None
                ),
                "end_date": timestamp.date().isoformat(),
                "holding_days": state.cycle_holding_days,
                "pnl": round(float(state.cycle_cashflows), 2),
                "mae": state.cycle_mae,
                "mfe": state.cycle_mfe,
                "entry_count": state.entry_count,
                "used_rebuy": used_rebuy,
            }
        )
        if used_rebuy and state.cycle_cashflows > 0:
            profitable_rebuy += 1
        self._tp1_holding_day.pop(cycle_id, None)
        self._reset_cycle(state)
        return tp1_hits, tp2_hits, profitable_rebuy
