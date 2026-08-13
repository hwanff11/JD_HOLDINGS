from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from jd_holdings.config import StrategyConfig

from .engine import BacktestResult
from .performance import maximum_drawdown, risk_adjusted_metrics


@dataclass(frozen=True)
class PortfolioBacktestResult:
    start_date: date
    end_date: date
    strategy_version: str
    config_version: str
    slippage: float
    metrics: dict[str, Any]
    trades: tuple[dict[str, Any], ...]
    equity_curve: pd.Series = field(repr=False, compare=False)

    def to_dict(self, *, include_equity: bool = False) -> dict[str, Any]:
        result = {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "strategy_version": self.strategy_version,
            "config_version": self.config_version,
            "slippage": self.slippage,
            "metrics": self.metrics,
            "trades": list(self.trades),
        }
        if include_equity:
            result["equity_curve"] = {
                timestamp.date().isoformat(): round(float(value), 2)
                for timestamp, value in self.equity_curve.items()
            }
        return result


class PortfolioBacktestEngine:
    """Production-equivalent shared-account simulation for the active V3 contract."""

    def __init__(self, config: StrategyConfig) -> None:
        if not config.portfolio.enabled:
            raise ValueError("포트폴리오 백테스트는 portfolio.enabled가 필요합니다")
        self.config = config

    def run(
        self,
        frames: dict[str, pd.DataFrame],
        booster_results: dict[str, BacktestResult],
        *,
        start: str | date,
        end: str | date,
        slippage: float | None = None,
    ) -> PortfolioBacktestResult:
        market_symbols = {
            *self.config.enabled_symbols,
            *self.config.portfolio.core_underlyings.values(),
        }
        required = set(market_symbols)
        if self.config.idle_cash.enabled:
            required.add(self.config.idle_cash.symbol)
        missing = required - set(frames)
        if missing:
            raise ValueError("포트폴리오 데이터 누락: " + ", ".join(sorted(missing)))

        index: pd.DatetimeIndex | None = None
        for symbol in market_symbols:
            index = frames[symbol].index if index is None else index.intersection(frames[symbol].index)
        if index is None:
            raise ValueError("공통 거래일이 없습니다")
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        index = index[(index >= start_ts) & (index <= end_ts)]
        if len(index) < 2:
            raise ValueError("포트폴리오 백테스트 기간이 너무 짧습니다")

        slip = float(self.config.backtest.default_slippage if slippage is None else slippage)
        buy_fee = float(self.config.global_.buy_fee)
        sell_fee = float(self.config.global_.sell_fee)
        capital_ceiling = float(self.config.portfolio.total_capital)
        cash = capital_ceiling
        quantities = {
            "core": {symbol: 0 for symbol in self.config.enabled_symbols},
            "booster": {symbol: 0 for symbol in self.config.enabled_symbols},
        }
        cost_basis = {
            "core": {symbol: 0.0 for symbol in self.config.enabled_symbols},
            "booster": {symbol: 0.0 for symbol in self.config.enabled_symbols},
        }
        month_ends = self._month_end_sessions(index)
        trends = {
            symbol: self._monthly_trend(
                frames[underlying], index, self.config.portfolio.trend_months
            )
            for symbol, underlying in self.config.portfolio.core_underlyings.items()
        }
        booster_events: dict[pd.Timestamp, list[dict[str, Any]]] = defaultdict(list)
        for symbol, result in booster_results.items():
            for trade in result.trades:
                event = dict(trade)
                event["symbol"] = symbol
                booster_events[pd.Timestamp(event["date"])].append(event)

        pending_core: dict[str, float] | None = None
        core_active_previous = {symbol: False for symbol in self.config.enabled_symbols}
        trades: list[dict[str, Any]] = []
        equity_values: list[float] = []
        exposures: list[float] = []
        invested_cost_values: list[float] = []
        idle_income = 0.0
        if self.config.idle_cash.enabled:
            idle_close = frames[self.config.idle_cash.symbol]["close"].reindex(index).ffill()
            idle_returns = idle_close.pct_change(fill_method=None).fillna(0.0)
        else:
            idle_returns = pd.Series(0.0, index=index)

        for timestamp in index:
            opens = {
                symbol: float(frames[symbol].loc[timestamp, "open"])
                for symbol in self.config.enabled_symbols
            }
            closes = {
                symbol: float(frames[symbol].loc[timestamp, "close"])
                for symbol in self.config.enabled_symbols
            }
            if self.config.idle_cash.enabled:
                income = max(0.0, cash - float(self.config.idle_cash.cash_buffer)) * float(
                    idle_returns.loc[timestamp]
                )
                cash += income
                idle_income += income

            if pending_core is not None:
                cash = self._rebalance_core(
                    pending_core,
                    quantities["core"],
                    cost_basis,
                    opens,
                    cash=cash,
                    sizing_equity=capital_ceiling,
                    capital_ceiling=capital_ceiling,
                    timestamp=timestamp,
                    buy_fee=buy_fee,
                    sell_fee=sell_fee,
                    slippage=slip,
                    trades=trades,
                )
                pending_core = None

            for event in booster_events.get(timestamp, []):
                symbol = str(event["symbol"])
                quantity = int(event["quantity"])
                price = float(event["price"])
                side = str(event["side"])
                if side == "BUY":
                    principal_available = max(
                        0.0, capital_ceiling - self._total_cost_basis(cost_basis)
                    )
                    affordable_cash = math.floor(cash / (price * (1 + buy_fee)))
                    affordable_principal = math.floor(principal_available / price)
                    quantity = min(quantity, affordable_cash, affordable_principal)
                    if quantity <= 0:
                        continue
                    fee = quantity * price * buy_fee
                    cash -= quantity * price + fee
                    quantities["booster"][symbol] += quantity
                    cost_basis["booster"][symbol] += quantity * price
                else:
                    current_qty = quantities["booster"][symbol]
                    quantity = min(quantity, current_qty)
                    if quantity <= 0:
                        continue
                    released = self._released_cost_basis(
                        cost_basis["booster"][symbol], current_qty, quantity
                    )
                    fee = quantity * price * sell_fee
                    cash += quantity * price - fee
                    quantities["booster"][symbol] -= quantity
                    cost_basis["booster"][symbol] = max(
                        0.0, cost_basis["booster"][symbol] - released
                    )
                trades.append(
                    {
                        "date": timestamp.date().isoformat(),
                        "component": "booster",
                        "symbol": symbol,
                        "side": side,
                        "purpose": event.get("purpose", "JDSS"),
                        "quantity": quantity,
                        "price": round(price, 6),
                        "fee": round(fee, 6),
                    }
                )

            if timestamp in month_ends:
                pending_core = {}
                for symbol in self.config.enabled_symbols:
                    active = bool(trends[symbol].loc[timestamp])
                    if not active:
                        weight = 0.0
                    elif core_active_previous[symbol]:
                        weight = float(self.config.portfolio.core_target_weight)
                    else:
                        weight = float(self.config.portfolio.core_initial_weight)
                    pending_core[symbol] = weight
                    core_active_previous[symbol] = active

            liquidation = sum(
                quantities[component][symbol] * closes[symbol] * (1 - sell_fee)
                for component in quantities
                for symbol in self.config.enabled_symbols
            )
            equity = cash + liquidation
            equity_values.append(equity)
            exposures.append(liquidation / equity if equity > 0 else 0.0)
            invested_cost_values.append(self._total_cost_basis(cost_basis))

        equity_curve = pd.Series(equity_values, index=index)
        metrics = self._metrics(equity_curve, exposures, trades, idle_income)
        metrics["component_fills"] = {
            component: sum(trade["component"] == component for trade in trades)
            for component in quantities
        }
        metrics["capital_ceiling"] = round(capital_ceiling, 2)
        metrics["maximum_invested_cost"] = round(max(invested_cost_values, default=0.0), 2)
        metrics["profit_reinvestment"] = False
        metrics["idle_cash_enabled"] = self.config.idle_cash.enabled
        return PortfolioBacktestResult(
            index[0].date(),
            index[-1].date(),
            self.config.version,
            self.config.config_version,
            slip,
            metrics,
            tuple(trades),
            equity_curve,
        )

    @staticmethod
    def _month_end_sessions(index: pd.DatetimeIndex) -> set[pd.Timestamp]:
        values = pd.Series(index=index, data=index)
        return set(values.groupby(index.to_period("M")).last().tolist())

    @classmethod
    def _monthly_trend(
        cls, frame: pd.DataFrame, index: pd.DatetimeIndex, months: int
    ) -> pd.Series:
        history = frame.loc[: index[-1], "close"].dropna()
        monthly = history.groupby(history.index.to_period("M")).last()
        active = monthly > monthly.rolling(months, min_periods=months).mean()
        result = pd.Series(False, index=index)
        for timestamp in cls._month_end_sessions(index):
            result.loc[timestamp] = bool(active.get(timestamp.to_period("M"), False))
        return result

    @staticmethod
    def _total_cost_basis(cost_basis: dict[str, dict[str, float]]) -> float:
        return sum(value for component in cost_basis.values() for value in component.values())

    @staticmethod
    def _released_cost_basis(cost: float, quantity: int, sold: int) -> float:
        if quantity <= 0 or sold <= 0:
            return 0.0
        if sold >= quantity:
            return cost
        return cost * sold / quantity

    def _rebalance_core(
        self,
        targets,
        quantities,
        cost_basis,
        prices,
        *,
        cash,
        sizing_equity,
        capital_ceiling,
        timestamp,
        buy_fee,
        sell_fee,
        slippage,
        trades,
    ):
        changes = {}
        for symbol, weight in targets.items():
            buy_price = prices[symbol] * (1 + slippage)
            sell_price = prices[symbol] * (1 - slippage)
            target_qty = math.floor(
                weight * sizing_equity / (buy_price * (1 + buy_fee))
            )
            changes[symbol] = (target_qty - quantities[symbol], buy_price, sell_price)
        for symbol, (difference, _, sell_price) in changes.items():
            if difference >= 0:
                continue
            current_qty = quantities[symbol]
            quantity = -difference
            released = self._released_cost_basis(
                cost_basis["core"][symbol], current_qty, quantity
            )
            fee = quantity * sell_price * sell_fee
            cash += quantity * sell_price - fee
            quantities[symbol] -= quantity
            cost_basis["core"][symbol] = max(
                0.0, cost_basis["core"][symbol] - released
            )
            trades.append(self._core_trade(timestamp, symbol, "SELL", quantity, sell_price, fee))
        for symbol, (difference, buy_price, _) in changes.items():
            if difference <= 0:
                continue
            principal_available = max(
                0.0, capital_ceiling - self._total_cost_basis(cost_basis)
            )
            affordable_cash = math.floor(cash / (buy_price * (1 + buy_fee)))
            affordable_principal = math.floor(principal_available / buy_price)
            quantity = min(difference, affordable_cash, affordable_principal)
            if quantity <= 0:
                continue
            fee = quantity * buy_price * buy_fee
            cash -= quantity * buy_price + fee
            quantities[symbol] += quantity
            cost_basis["core"][symbol] += quantity * buy_price
            trades.append(self._core_trade(timestamp, symbol, "BUY", quantity, buy_price, fee))
        return cash

    @staticmethod
    def _core_trade(timestamp, symbol, side, quantity, price, fee):
        return {
            "date": timestamp.date().isoformat(),
            "component": "core",
            "symbol": symbol,
            "side": side,
            "purpose": f"CORE_REBALANCE_{side}",
            "quantity": quantity,
            "price": round(price, 6),
            "fee": round(fee, 6),
        }

    def _metrics(
        self,
        equity: pd.Series,
        exposures: list[float],
        trades: list[dict[str, Any]],
        idle_income: float,
    ) -> dict[str, Any]:
        initial, final = float(equity.iloc[0]), float(equity.iloc[-1])
        years = max((equity.index[-1] - equity.index[0]).days / 365.2425, 1 / 365.2425)
        sharpe, sortino = risk_adjusted_metrics(equity, self.config.backtest.annualization_days)
        half_year = pd.Index([f"{ts.year}-H{1 if ts.month <= 6 else 2}" for ts in equity.index])
        return {
            "initial_equity": round(initial, 2),
            "final_equity": round(final, 2),
            "total_return_pct": round((final / initial - 1) * 100, 2),
            "cagr_pct": round(((final / initial) ** (1 / years) - 1) * 100, 2),
            "mdd_pct": round(maximum_drawdown(equity) * 100, 2),
            "sharpe": round(sharpe, 3),
            "sortino": round(sortino, 3),
            "trade_fills": len(trades),
            "average_exposure_pct": round(sum(exposures) / len(exposures) * 100, 2),
            "idle_cash_income": round(idle_income, 2),
            "annual_returns_pct": {
                str(year): round((group.iloc[-1] / group.iloc[0] - 1) * 100, 2)
                for year, group in equity.groupby(equity.index.year)
            },
            "half_year_returns_pct": {
                str(period): round((group.iloc[-1] / group.iloc[0] - 1) * 100, 2)
                for period, group in equity.groupby(half_year)
            },
        }
