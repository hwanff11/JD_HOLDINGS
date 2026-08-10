from __future__ import annotations

from decimal import Decimal

import pandas as pd
from conftest import make_score, make_snapshot

from jd_holdings.backtest.engine import BacktestEngine, _Pending, _SimulationState
from jd_holdings.core.enums import DecisionType, PositionState
from jd_holdings.core.models import TradeDecision


def test_backtest_buy_and_same_day_tp_apply_both_fees(config):
    engine = BacktestEngine(config)
    state = _SimulationState(
        symbol="TQQQ",
        capital=Decimal("10000"),
        cash=Decimal("10000"),
    )
    snapshot = make_snapshot(close=Decimal("100"), atr_pct=0.05)
    pending = _Pending(
        snapshot=snapshot,
        score=make_score(80),
        decision=TradeDecision(
            action=DecisionType.FIRST_ENTRY_CANDIDATE,
            allowed=True,
            reason_codes=("TEST",),
            target_stage=1,
            cycle_exposure_cap=Decimal("10000"),
            target_cumulative_capital=Decimal("4000"),
            planned_budget=Decimal("4000"),
        ),
    )
    trades: list[dict] = []

    filled, reason = engine._execute_pending(
        pending,
        state,
        pd.Timestamp("2024-08-05"),
        Decimal("100"),
        Decimal("0.001"),
        trades,
    )

    assert filled
    assert reason is None
    assert state.state == PositionState.HOLDING_1ST
    assert state.tp_plan is not None
    assert len(trades) == 1

    buy = trades[0]
    quantity = int(buy["quantity"])
    fill_price = Decimal(str(buy["price"]))
    expected_buy_fee = Decimal(quantity) * fill_price * config.global_.buy_fee
    assert Decimal(str(buy["fee"])) == expected_buy_fee.quantize(Decimal("0.0001"))

    cash_after_buy = state.cash
    tp_plan = state.tp_plan
    closed_cycles: list[dict] = []
    tp1_hits, tp2_hits, _ = engine._process_take_profit(
        state,
        pd.Timestamp("2024-08-05"),
        tp_plan.tp2_price,
        trades,
        closed_cycles,
    )

    assert tp1_hits == 1
    assert tp2_hits == 1
    assert state.state == PositionState.EMPTY
    assert state.quantity == 0
    assert len(closed_cycles) == 1

    sell_trades = [trade for trade in trades if trade["side"] == "SELL"]
    assert [trade["purpose"] for trade in sell_trades] == ["TP1", "TP2"]

    expected_sell_net = sum(
        Decimal(str(trade["price"]))
        * Decimal(int(trade["quantity"]))
        * (Decimal("1") - config.global_.sell_fee)
        for trade in sell_trades
    )
    expected_final_cash = cash_after_buy + expected_sell_net
    assert abs(state.cash - expected_final_cash) < Decimal("0.01")

    realized_pnl = Decimal(str(closed_cycles[0]["pnl"]))
    assert abs(realized_pnl - (state.cash - Decimal("10000"))) < Decimal("0.02")


def test_partial_tp_without_rebuy_keeps_remaining_position_open(config):
    engine = BacktestEngine(config)
    state = _SimulationState(
        symbol="SOXL",
        capital=Decimal("10000"),
        cash=Decimal("10000"),
    )
    snapshot = make_snapshot(symbol="SOXL", close=Decimal("100"), atr_pct=0.05)
    pending = _Pending(
        snapshot=snapshot,
        score=make_score(80),
        decision=TradeDecision(
            action=DecisionType.FIRST_ENTRY_CANDIDATE,
            allowed=True,
            reason_codes=("TEST",),
            target_stage=1,
            cycle_exposure_cap=Decimal("10000"),
            target_cumulative_capital=Decimal("4000"),
            planned_budget=Decimal("4000"),
        ),
    )
    trades: list[dict] = []

    filled, _ = engine._execute_pending(
        pending,
        state,
        pd.Timestamp("2024-08-05"),
        Decimal("100"),
        Decimal("0"),
        trades,
    )
    assert filled
    assert state.tp_plan is not None

    tp1_price = state.tp_plan.tp1_price
    closed_cycles: list[dict] = []
    tp1_hits, tp2_hits, _ = engine._process_take_profit(
        state,
        pd.Timestamp("2024-08-06"),
        tp1_price,
        trades,
        closed_cycles,
    )

    assert tp1_hits == 1
    assert tp2_hits == 0
    assert state.state == PositionState.PARTIAL_TP_1
    assert state.quantity > 0
    assert not closed_cycles
    assert not config.rebuy.enabled
