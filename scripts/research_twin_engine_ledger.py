"""Generate an auditable fill ledger for the monthly twin-engine candidate."""

# ruff: noqa: E501, I001

from __future__ import annotations

import argparse
import json
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from research_simple_strategies import ROOT
from research_twin_engine_robustness import _simulate
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource


def _analyze(
    trades: list[dict[str, Any]],
    raw: dict[str, pd.DataFrame],
    *,
    end: str,
    slippage: float,
    buy_fee: float,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    total_notional = 0.0
    total_fees = 0.0
    total_slippage = 0.0
    for symbol in ("TQQQ", "SOXL"):
        lots: deque[list[Any]] = deque()
        realized = 0.0
        holding_days: list[int] = []
        buys = sells = 0
        symbol_notional = symbol_fees = 0.0
        for trade in (item for item in trades if item["symbol"] == symbol):
            date = pd.Timestamp(trade["date"])
            quantity = int(trade["quantity"])
            price = float(trade["price"])
            fee = float(trade["fee"])
            notional = quantity * price
            symbol_notional += notional
            symbol_fees += fee
            raw_open = float(raw[symbol].loc[date, "open"])
            total_slippage += quantity * raw_open * slippage
            if trade["side"] == "BUY":
                buys += 1
                unit_cost = (notional + fee) / quantity
                lots.append([quantity, unit_cost, date])
            else:
                sells += 1
                remaining = quantity
                net_unit = (notional - fee) / quantity
                while remaining > 0 and lots:
                    lot_qty, unit_cost, buy_date = lots[0]
                    matched = min(remaining, lot_qty)
                    realized += matched * (net_unit - unit_cost)
                    holding_days.extend([(date - buy_date).days] * matched)
                    remaining -= matched
                    lot_qty -= matched
                    if lot_qty == 0:
                        lots.popleft()
                    else:
                        lots[0][0] = lot_qty
        close = float(raw[symbol].loc[pd.Timestamp(end), "close"])
        open_qty = sum(int(lot[0]) for lot in lots)
        open_cost = sum(int(lot[0]) * float(lot[1]) for lot in lots)
        liquidation = open_qty * close * (1 - buy_fee)
        report[symbol] = {
            "buy_fills": buys,
            "sell_fills": sells,
            "realized_pnl": round(realized, 2),
            "open_quantity": open_qty,
            "open_cost": round(open_cost, 2),
            "open_market_value": round(open_qty * close, 2),
            "open_unrealized_pnl": round(liquidation - open_cost, 2),
            "average_holding_days": round(sum(holding_days) / len(holding_days), 1) if holding_days else None,
            "max_holding_days": max(holding_days) if holding_days else None,
            "gross_notional": round(symbol_notional, 2),
            "fees": round(symbol_fees, 2),
        }
        total_notional += symbol_notional
        total_fees += symbol_fees
    report["portfolio_costs"] = {
        "gross_notional": round(total_notional, 2),
        "fees": round(total_fees, 2),
        "estimated_slippage": round(total_slippage, 2),
        "fees_plus_slippage": round(total_fees + total_slippage, 2),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end", default="2026-08-10")
    parser.add_argument("--slippage", type=float, default=0.001)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "twin_engine_ledger.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "reports" / "twin_engine_ledger.md")
    args = parser.parse_args()
    config = load_config(ROOT / "strategy.yaml")
    source = YFinanceDataSource(ROOT / "data" / "cache")
    warmup = (datetime.fromisoformat("2011-01-01").date() - timedelta(days=800)).isoformat()
    raw = {
        symbol: source.daily(symbol, warmup, args.end)
        for symbol in ("TQQQ", "SOXL", "QQQ", "SOXX", config.idle_cash.symbol)
    }
    _, metrics = _simulate(
        raw, config, end=args.end, ma_months=10, delay=0,
        slippage=args.slippage, monthly_rebalance=True, include_details=True,
    )
    trades = metrics.pop("_trades")
    open_positions = metrics.pop("_open_positions")
    final_cash = metrics.pop("_final_cash")
    analysis = _analyze(
        trades, raw, end=args.end, slippage=args.slippage,
        buy_fee=float(config.global_.buy_fee),
    )
    years = (pd.Timestamp(args.end) - pd.Timestamp("2011-01-01")).days / 365.2425
    average_equity = (metrics["initial_equity"] + metrics["final_equity"]) / 2
    analysis["portfolio_costs"]["annualized_turnover_multiple"] = round(
        analysis["portfolio_costs"]["gross_notional"] / average_equity / years, 3
    )
    report = {
        "end_date": args.end,
        "metrics": metrics,
        "analysis": analysis,
        "open_positions": open_positions,
        "final_cash": final_cash,
        "recent_fills": trades[-20:],
        "all_fills": trades,
    }
    lines = [
        "# 월간 쌍발엔진 거래 원장", "",
        "| 종목 | 매수 | 매도 | 실현손익 | 미실현손익 | 평균 보유일 | 최장 보유일 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for symbol in ("TQQQ", "SOXL"):
        item = analysis[symbol]
        lines.append(
            f"| {symbol} | {item['buy_fills']} | {item['sell_fills']} | "
            f"${item['realized_pnl']:+,.2f} | ${item['open_unrealized_pnl']:+,.2f} | "
            f"{item['average_holding_days']} | {item['max_holding_days']} |"
        )
    costs = analysis["portfolio_costs"]
    lines.extend([
        "",
        f"- 전체 체결: {len(trades)}회",
        f"- 전체 거래대금: ${costs['gross_notional']:,.2f}",
        f"- 수수료: ${costs['fees']:,.2f}",
        f"- 추정 슬리피지 비용: ${costs['estimated_slippage']:,.2f}",
        f"- 수수료+슬리피지: ${costs['fees_plus_slippage']:,.2f}",
        f"- 연환산 회전율 배수: {costs['annualized_turnover_multiple']:.3f}",
        f"- 종료 현금: ${final_cash:,.2f}",
        "",
        "## 최근 20개 체결", "",
        "| 일자 | 종목 | 구분 | 수량 | 체결가 | 목표비중 |",
        "|---|---|---|---:|---:|---:|",
    ])
    for trade in trades[-20:]:
        lines.append(
            f"| {trade['date']} | {trade['symbol']} | {trade['side']} | "
            f"{trade['quantity']} | ${trade['price']:.4f} | {trade['target_weight']:.0%} |"
        )
    lines.extend(["", "> 연구 전용이며 운영 코드·설정·Oracle·실주문을 변경하지 않습니다."])
    markdown = "\n".join(lines) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
