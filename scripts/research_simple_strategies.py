"""Compare JDSS with simple, structurally different TQQQ/SOXL strategies.

This runner is research-only. It uses the production backtest engine unchanged for
the baseline and a small event-driven simulator for the alternative strategies.
Signals use completed daily bars and orders execute at the next session open.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from jd_holdings.backtest.engine import BacktestEngine, BacktestResult
from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import StrategyConfig, load_config
from jd_holdings.core.indicators import calculate_indicators
from jd_holdings.infrastructure.market_data import YFinanceDataSource

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ("TQQQ", "SOXL")
UNDERLYING = {"TQQQ": "QQQ", "SOXL": "SOXX"}
DOWNLOAD_SYMBOLS = ("TQQQ", "SOXL", "SPY", "QQQ", "SOXX", "SMH", "SGOV")
SEGMENTS = {
    "development_2011_2018": ("2011-01-01", "2018-12-31"),
    "validation_2019_2022": ("2019-01-01", "2022-12-31"),
    "test_2023_present": ("2023-01-01", None),
    "full_history": ("2011-01-01", None),
}


@dataclass(frozen=True)
class PendingOrder:
    action: str
    signal_close: float
    budget: float = 0.0
    price_ceiling: float | None = None


@dataclass(frozen=True)
class SimResult:
    equity: pd.Series
    metrics: dict[str, Any]
    trades: tuple[dict[str, Any], ...]
    open_position: dict[str, Any]


@dataclass
class Account:
    initial_capital: float
    cash: float
    quantity: int = 0
    entry_price: float = 0.0
    first_fill_price: float = 0.0
    stage: int = 0
    entry_date: pd.Timestamp | None = None
    idle_cash_income: float = 0.0


def _research_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    close = result["close"]
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=0.5, adjust=False, min_periods=2).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=0.5, adjust=False, min_periods=2).mean()
    rs = gain / loss.replace(0, float("nan"))
    result["rsi2"] = (100 - 100 / (1 + rs)).fillna(100.0)
    result["ema5_r"] = close.ewm(span=5, adjust=False).mean()
    result["sma50_r"] = close.rolling(50, min_periods=50).mean()
    result["sma200_r"] = close.rolling(200, min_periods=200).mean()
    result["previous_high20"] = close.shift(1).rolling(20, min_periods=20).max()
    result["previous_low10"] = close.shift(1).rolling(10, min_periods=10).min()
    result["return63"] = close.pct_change(63, fill_method=None)
    result["vol20"] = (
        close.pct_change(fill_method=None).rolling(20, min_periods=20).std()
        * math.sqrt(252)
    )
    return result


def _idle_return(idle_frame: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    close = idle_frame["close"].reindex(index).ffill()
    return close.pct_change(fill_method=None).fillna(0.0)


def _metrics(
    equity: pd.Series,
    *,
    trades: list[dict[str, Any]],
    exposure: list[float],
    idle_cash_income: float,
    annualization_days: int,
) -> dict[str, Any]:
    initial = float(equity.iloc[0])
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
    sharpe, sortino = risk_adjusted_metrics(equity, annualization_days)
    entries = sum(1 for trade in trades if trade["side"] == "BUY")
    exits = sum(1 for trade in trades if trade["side"] == "SELL")
    return {
        "initial_equity": round(initial, 2),
        "final_equity": round(final, 2),
        "total_return_pct": round((final / initial - 1) * 100, 2),
        "cagr_pct": round(((final / initial) ** (1 / years) - 1) * 100, 2),
        "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "buy_fills": entries,
        "sell_fills": exits,
        "average_exposure_pct": round(sum(exposure) / len(exposure) * 100, 2),
        "idle_cash_income": round(idle_cash_income, 2),
        "annual_returns_pct": {
            str(year): round((group.iloc[-1] / group.iloc[0] - 1) * 100, 2)
            for year, group in equity.groupby(equity.index.year)
        },
    }


def _buy(
    account: Account,
    *,
    timestamp: pd.Timestamp,
    symbol: str,
    open_price: float,
    budget: float,
    fee: float,
    slippage: float,
    trades: list[dict[str, Any]],
) -> None:
    price = open_price * (1 + slippage)
    available = min(budget, account.cash)
    quantity = math.floor(available / (price * (1 + fee)))
    if quantity <= 0:
        return
    cost = quantity * price
    commission = cost * fee
    previous_cost = account.quantity * account.entry_price
    account.cash -= cost + commission
    account.quantity += quantity
    account.entry_price = (previous_cost + cost) / account.quantity
    if account.stage == 0:
        account.first_fill_price = price
        account.entry_date = timestamp
    account.stage += 1
    trades.append(
        {
            "date": timestamp.date().isoformat(),
            "symbol": symbol,
            "side": "BUY",
            "quantity": quantity,
            "price": round(price, 4),
            "fee": round(commission, 2),
            "stage": account.stage,
        }
    )


def _sell_all(
    account: Account,
    *,
    timestamp: pd.Timestamp,
    symbol: str,
    open_price: float,
    fee: float,
    slippage: float,
    trades: list[dict[str, Any]],
) -> None:
    if account.quantity <= 0:
        return
    price = open_price * (1 - slippage)
    proceeds = account.quantity * price
    commission = proceeds * fee
    trades.append(
        {
            "date": timestamp.date().isoformat(),
            "symbol": symbol,
            "side": "SELL",
            "quantity": account.quantity,
            "price": round(price, 4),
            "fee": round(commission, 2),
            "stage": account.stage,
        }
    )
    account.cash += proceeds - commission
    account.quantity = 0
    account.entry_price = 0.0
    account.first_fill_price = 0.0
    account.stage = 0
    account.entry_date = None


def _simulate_symbol(
    name: str,
    symbol: str,
    target: pd.DataFrame,
    underlying: pd.DataFrame,
    idle_frame: pd.DataFrame,
    config: StrategyConfig,
    *,
    start: str,
    end: str,
    slippage: float,
) -> SimResult:
    index = target.index.intersection(underlying.index)
    index = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    required_target = ["open", "close", "rsi2", "ema5_r", "previous_high20", "previous_low10"]
    required_base = ["close", "sma50_r", "sma200_r"]
    valid = target.loc[index, required_target].notna().all(axis=1)
    valid &= underlying.loc[index, required_base].notna().all(axis=1)
    index = index[valid]
    if len(index) < 2:
        raise ValueError(f"{name}/{symbol}: insufficient data")

    account = Account(float(config.global_.capital_per_symbol), float(config.global_.capital_per_symbol))
    pending: PendingOrder | None = None
    trades: list[dict[str, Any]] = []
    equity_values: list[float] = []
    exposure: list[float] = []
    idle_returns = _idle_return(idle_frame, index)
    fee_buy = float(config.global_.buy_fee)
    fee_sell = float(config.global_.sell_fee)
    cash_buffer = float(config.idle_cash.cash_buffer) / len(config.enabled_symbols)

    for timestamp in index:
        target_row = target.loc[timestamp]
        base_row = underlying.loc[timestamp]
        yieldable_cash = max(0.0, account.cash - cash_buffer)
        income = yieldable_cash * float(idle_returns.loc[timestamp])
        account.cash += income
        account.idle_cash_income += income

        if pending is not None:
            if pending.action == "SELL":
                _sell_all(
                    account,
                    timestamp=timestamp,
                    symbol=symbol,
                    open_price=float(target_row["open"]),
                    fee=fee_sell,
                    slippage=slippage,
                    trades=trades,
                )
            elif (
                float(target_row["open"])
                <= pending.signal_close
                * (1 + float(config.global_.entry_max_chase_pct))
                and (
                    pending.price_ceiling is None
                    or float(target_row["open"]) * (1 + slippage)
                    <= pending.price_ceiling
                )
            ):
                _buy(
                    account,
                    timestamp=timestamp,
                    symbol=symbol,
                    open_price=float(target_row["open"]),
                    budget=pending.budget,
                    fee=fee_buy,
                    slippage=slippage,
                    trades=trades,
                )
            pending = None

        trend_ok = (
            float(base_row["close"]) > float(base_row["sma200_r"])
            and float(base_row["sma50_r"]) > float(base_row["sma200_r"])
        )
        if account.quantity == 0:
            if name == "G_RSI2_REVERSION" and trend_ok and float(target_row["rsi2"]) <= 10:
                pending = PendingOrder(
                    "BUY",
                    float(target_row["close"]),
                    account.initial_capital * 0.5,
                )
            elif (
                name == "H_BREAKOUT_20_10"
                and trend_ok
                and float(target_row["close"]) > float(target_row["previous_high20"])
            ):
                pending = PendingOrder(
                    "BUY", float(target_row["close"]), account.initial_capital
                )
        elif name == "G_RSI2_REVERSION":
            exit_signal = (
                float(target_row["close"]) > float(target_row["ema5_r"])
                or float(target_row["rsi2"]) >= 70
                or float(base_row["close"]) < float(base_row["sma200_r"])
            )
            if exit_signal:
                pending = PendingOrder("SELL", float(target_row["close"]))
            elif (
                account.stage == 1
                and float(target_row["close"]) <= account.first_fill_price * 0.97
                and float(target_row["rsi2"]) <= 10
            ):
                pending = PendingOrder(
                    "BUY",
                    float(target_row["close"]),
                    account.initial_capital * 0.5,
                    account.first_fill_price * 0.97,
                )
        else:
            exit_signal = (
                float(target_row["close"]) < float(target_row["previous_low10"])
                or float(base_row["close"]) < float(base_row["sma200_r"])
            )
            if exit_signal:
                pending = PendingOrder("SELL", float(target_row["close"]))

        liquidation_value = account.quantity * float(target_row["close"]) * (1 - fee_sell)
        equity = account.cash + liquidation_value
        equity_values.append(equity)
        exposure.append(liquidation_value / equity if equity > 0 else 0.0)

    equity = pd.Series(equity_values, index=index, name=symbol)
    metrics = _metrics(
        equity,
        trades=trades,
        exposure=exposure,
        idle_cash_income=account.idle_cash_income,
        annualization_days=config.backtest.annualization_days,
    )
    return SimResult(
        equity=equity,
        metrics=metrics,
        trades=tuple(trades),
        open_position={
            "symbol": symbol,
            "quantity": account.quantity,
            "average_price": round(account.entry_price, 4),
            "stage": account.stage,
        },
    )


def _last_session_of_week(index: pd.DatetimeIndex) -> set[pd.Timestamp]:
    series = pd.Series(index=index, data=index)
    return set(series.groupby(index.to_period("W-FRI")).last().tolist())


def _rotation_choice(
    timestamp: pd.Timestamp,
    underlying_frames: dict[str, pd.DataFrame],
    *,
    require_rising_long_trend: bool = False,
) -> str | None:
    ranked: list[tuple[float, str]] = []
    for symbol, underlying_symbol in UNDERLYING.items():
        row = underlying_frames[underlying_symbol].loc[timestamp]
        if (
            pd.isna(row["return63"])
            or pd.isna(row["vol20"])
            or pd.isna(row["sma200_r"])
            or (require_rising_long_trend and pd.isna(row["sma50_r"]))
            or float(row["close"]) <= float(row["sma200_r"])
            or (
                require_rising_long_trend
                and float(row["sma50_r"]) <= float(row["sma200_r"])
            )
            or float(row["return63"]) <= 0
            or float(row["vol20"]) <= 0
        ):
            continue
        ranked.append((float(row["return63"]) / float(row["vol20"]), symbol))
    return max(ranked)[1] if ranked else None


def _rotation_budget(
    name: str,
    selected: str,
    frames: dict[str, pd.DataFrame],
    timestamp: pd.Timestamp,
    initial_capital: float,
) -> float:
    if name in {"I_ROTATION_CAP50", "L_ROTATION_TREND_CAP50"}:
        return initial_capital * 0.5
    if name in {"J_ROTATION_FULL", "M_ROTATION_TREND_FULL"}:
        return initial_capital
    selected_vol = float(frames[selected].loc[timestamp, "vol20"])
    target_weight = min(1.0, 0.25 / selected_vol) if selected_vol > 0 else 0.0
    return initial_capital * target_weight


def _simulate_rotation(
    name: str,
    frames: dict[str, pd.DataFrame],
    idle_frame: pd.DataFrame,
    config: StrategyConfig,
    *,
    start: str,
    end: str,
    slippage: float,
) -> SimResult:
    index = frames["TQQQ"].index
    for symbol in ("SOXL", "QQQ", "SOXX"):
        index = index.intersection(frames[symbol].index)
    index = index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
    index = index[
        frames["QQQ"].loc[index, ["close", "sma200_r", "return63", "vol20"]].notna().all(axis=1)
        & frames["SOXX"].loc[index, ["close", "sma200_r", "return63", "vol20"]].notna().all(axis=1)
        & frames["TQQQ"].loc[index, ["open", "close", "vol20"]].notna().all(axis=1)
        & frames["SOXL"].loc[index, ["open", "close", "vol20"]].notna().all(axis=1)
    ]
    if len(index) < 2:
        raise ValueError(f"{name}: insufficient data")

    initial_capital = float(config.global_.capital_per_symbol) * len(SYMBOLS)
    account = Account(initial_capital, initial_capital)
    held_symbol: str | None = None
    pending_choice: tuple[str | None, float] | None = None
    trades: list[dict[str, Any]] = []
    equity_values: list[float] = []
    exposure: list[float] = []
    idle_returns = _idle_return(idle_frame, index)
    weekly_dates = _last_session_of_week(index)
    fee_buy = float(config.global_.buy_fee)
    fee_sell = float(config.global_.sell_fee)
    cash_buffer = float(config.idle_cash.cash_buffer)

    for timestamp in index:
        yieldable_cash = max(0.0, account.cash - cash_buffer)
        income = yieldable_cash * float(idle_returns.loc[timestamp])
        account.cash += income
        account.idle_cash_income += income

        if pending_choice is not None:
            selected, budget = pending_choice
            if account.quantity > 0 and held_symbol is not None:
                _sell_all(
                    account,
                    timestamp=timestamp,
                    symbol=held_symbol,
                    open_price=float(frames[held_symbol].loc[timestamp, "open"]),
                    fee=fee_sell,
                    slippage=slippage,
                    trades=trades,
                )
                held_symbol = None
            if selected is not None:
                _buy(
                    account,
                    timestamp=timestamp,
                    symbol=selected,
                    open_price=float(frames[selected].loc[timestamp, "open"]),
                    budget=budget,
                    fee=fee_buy,
                    slippage=slippage,
                    trades=trades,
                )
                if account.quantity > 0:
                    held_symbol = selected
            pending_choice = None

        if timestamp in weekly_dates:
            selected = _rotation_choice(
                timestamp,
                {"QQQ": frames["QQQ"], "SOXX": frames["SOXX"]},
                require_rising_long_trend=name
                in {"L_ROTATION_TREND_CAP50", "M_ROTATION_TREND_FULL"},
            )
            if selected != held_symbol:
                budget = (
                    _rotation_budget(name, selected, frames, timestamp, initial_capital)
                    if selected is not None
                    else 0.0
                )
                pending_choice = (selected, budget)

        position_value = 0.0
        if account.quantity > 0 and held_symbol is not None:
            position_value = (
                account.quantity
                * float(frames[held_symbol].loc[timestamp, "close"])
                * (1 - fee_sell)
            )
        equity = account.cash + position_value
        equity_values.append(equity)
        exposure.append(position_value / equity if equity > 0 else 0.0)

    equity = pd.Series(equity_values, index=index, name=name)
    metrics = _metrics(
        equity,
        trades=trades,
        exposure=exposure,
        idle_cash_income=account.idle_cash_income,
        annualization_days=config.backtest.annualization_days,
    )
    return SimResult(
        equity=equity,
        metrics=metrics,
        trades=tuple(trades),
        open_position={
            "symbol": held_symbol,
            "quantity": account.quantity,
            "average_price": round(account.entry_price, 4),
        },
    )


def _combined(
    results: dict[str, SimResult | BacktestResult], config: StrategyConfig
) -> dict[str, Any]:
    equity = pd.concat(
        [
            result.equity_curve.rename(symbol)
            if isinstance(result, BacktestResult)
            else result.equity.rename(symbol)
            for symbol, result in results.items()
        ],
        axis=1,
        join="inner",
    ).sum(axis=1)
    trades: list[dict[str, Any]] = []
    exposure = pd.Series(0.0, index=equity.index)
    idle_income = 0.0
    for result in results.values():
        if isinstance(result, BacktestResult):
            trades.extend(result.trades)
            idle_income += float(result.metrics.get("idle_cash_income", 0.0))
            exposure += 0.0
        else:
            trades.extend(result.trades)
            idle_income += float(result.metrics.get("idle_cash_income", 0.0))
            aligned = result.equity.reindex(equity.index)
            exposure += (aligned / equity).fillna(0.0) * (
                float(result.metrics["average_exposure_pct"]) / 100
            )
    combined_metrics = _metrics(
        equity,
        trades=trades,
        exposure=exposure.tolist(),
        idle_cash_income=idle_income,
        annualization_days=config.backtest.annualization_days,
    )
    if all(isinstance(result, BacktestResult) for result in results.values()):
        combined_metrics["average_exposure_pct"] = round(
            sum(
                float(result.metrics.get("average_capital_utilization_pct", 0.0))
                for result in results.values()
                if isinstance(result, BacktestResult)
            )
            / len(results),
            2,
        )
    else:
        combined_metrics["average_exposure_pct"] = round(
            sum(
                float(result.metrics.get("average_exposure_pct", 0.0))
                for result in results.values()
                if isinstance(result, SimResult)
            )
            / len(results),
            2,
        )
    return combined_metrics


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# JDSS 단순 대안 전략 연구",
        "",
        f"- 생성시각: {report['generated_at']}",
        f"- 데이터 종료일: {report['end_date']}",
        f"- 슬리피지: {report['slippage'] * 100:.2f}%",
        "- 비용: 매수 0.1%, 매도 0.1%",
        "- 신호: 완료 일봉, 다음 거래일 시가 체결",
        "- 유휴자금: SGOV, 총 현금 버퍼 $250",
        "",
        "| 후보 | 구간 | 누적수익률 | CAGR | MDD | Sharpe | 평균노출 | 매수체결 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for candidate, candidate_data in report["candidates"].items():
        for segment in ("full_history", "test_2023_present"):
            metrics = candidate_data["segments"][segment]
            lines.append(
                f"| {candidate} | {segment} | {metrics['total_return_pct']:+.2f}% | "
                f"{metrics['cagr_pct']:+.2f}% | {metrics['mdd_pct']:.2f}% | "
                f"{metrics['sharpe']:.3f} | {metrics['average_exposure_pct']:.2f}% | "
                f"{metrics['buy_fills']} |"
            )
    lines.extend(
        [
            "",
            "## 후보 정의",
            "",
            "- A_BASELINE: 운영 중인 JDSS-2.2.2-SGOV",
            "- G_RSI2_REVERSION: 기초자산 50/200일 상승추세에서 RSI2<=10, "
            "50%+3% 하락 시 50%, EMA5/RSI2/200일선 회복·이탈 청산",
            "- H_BREAKOUT_20_10: 기초자산 상승추세에서 20일 신고가 진입, "
            "10일 저가 또는 기초자산 200일선 이탈 청산",
            "- I_ROTATION_CAP50: 주간 QQQ/SOXX 위험조정 63일 모멘텀 1위에 총자금 50% 투자",
            "- J_ROTATION_FULL: I와 같되 총자금 100% 집중",
            "- K_ROTATION_VOL25: I와 같되 선택 ETF의 20일 변동성으로 포트폴리오 목표변동성 25% 비중 결정",
            "- L_ROTATION_TREND_CAP50: I에 기초자산 50일선>200일선 확인을 추가",
            "- M_ROTATION_TREND_FULL: L과 같되 총자금 100% 집중",
            "",
            "> 연구 전용 결과입니다. 모든 신호가 승인되었다고 가정하며 "
            "운영 설정과 주문 로직은 변경하지 않습니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-08-10")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "simple_strategies.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "reports" / "simple_strategies.md")
    args = parser.parse_args()

    config = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=500)).isoformat()
    raw = {symbol: source.daily(symbol, warmup, args.end) for symbol in DOWNLOAD_SYMBOLS}
    research_frames = {symbol: _research_indicators(frame) for symbol, frame in raw.items()}
    production_frames = {symbol: calculate_indicators(frame, config) for symbol, frame in raw.items()}

    candidate_factories: dict[str, Callable[[str, str], dict[str, SimResult | BacktestResult]]] = {
        "A_BASELINE": lambda start, end: {
            symbol: BacktestEngine(config).run(
                symbol,
                production_frames[symbol],
                production_frames["SPY"],
                production_frames["QQQ"],
                start=start,
                end=end,
                slippage=args.slippage,
                indicators_precomputed=True,
                sector_data={"SOXX": production_frames["SOXX"], "SMH": production_frames["SMH"]}
                if symbol == "SOXL"
                else None,
                idle_cash_data=raw[config.idle_cash.symbol],
            )
            for symbol in SYMBOLS
        },
        "G_RSI2_REVERSION": lambda start, end: {
            symbol: _simulate_symbol(
                "G_RSI2_REVERSION",
                symbol,
                research_frames[symbol],
                research_frames[UNDERLYING[symbol]],
                raw[config.idle_cash.symbol],
                config,
                start=start,
                end=end,
                slippage=args.slippage,
            )
            for symbol in SYMBOLS
        },
        "H_BREAKOUT_20_10": lambda start, end: {
            symbol: _simulate_symbol(
                "H_BREAKOUT_20_10",
                symbol,
                research_frames[symbol],
                research_frames[UNDERLYING[symbol]],
                raw[config.idle_cash.symbol],
                config,
                start=start,
                end=end,
                slippage=args.slippage,
            )
            for symbol in SYMBOLS
        },
        **{
            name: lambda start, end, name=name: {
                "portfolio": _simulate_rotation(
                    name,
                    research_frames,
                    raw[config.idle_cash.symbol],
                    config,
                    start=start,
                    end=end,
                    slippage=args.slippage,
                )
            }
            for name in (
                "I_ROTATION_CAP50",
                "J_ROTATION_FULL",
                "K_ROTATION_VOL25",
                "L_ROTATION_TREND_CAP50",
                "M_ROTATION_TREND_FULL",
            )
        },
    }

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "end_date": args.end,
        "slippage": args.slippage,
        "strategy_version": config.version,
        "candidates": {},
    }
    for candidate, factory in candidate_factories.items():
        candidate_data: dict[str, Any] = {"segments": {}}
        for segment, (start, configured_end) in SEGMENTS.items():
            end = configured_end or args.end
            results = factory(start, end)
            metrics = _combined(results, config)
            candidate_data["segments"][segment] = metrics
        report["candidates"][candidate] = candidate_data

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(_markdown(report), encoding="utf-8")
    print(_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
