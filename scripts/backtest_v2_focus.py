#!/usr/bin/env python3
"""JDSS 2.0 dual-track TP 4/6 vs 4/8 first-entry exploration."""
from __future__ import annotations
import argparse, json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
import pandas as pd
from jd_holdings.backtest.engine import BacktestEngine, BacktestResult
from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource
ROOT=Path(__file__).resolve().parent.parent
SYMBOLS=("TQQQ","SOXL"); BENCHMARKS=("SPY","QQQ","SOXX","SMH")
def _with_tp(base,tp2): return replace(base,take_profit=replace(base.take_profit,tp1_base=Decimal("0.04"),tp2_base=Decimal(tp2)))
def _with_entry_score(base,score): return replace(base,global_=replace(base.global_,entry_score=score))
def _with_stage1_guard(base,rule="any_benchmark_below_ema60"):
    guard=dict(base.market_regime.get("soxl_sector_guard",{})); blocked={int(x) for x in guard.get("blocked_stages",(3,4))}; blocked.add(1); guard["blocked_stages"]=sorted(blocked); guard["rule"]=rule
    return replace(base,market_regime={**base.market_regime,"soxl_sector_guard":guard})
def build_candidates(base):
    out={}
    for label,tp2 in (("TP46","0.06"),("TP48","0.08")):
        t=_with_tp(base,tp2)
        out[f"{label}_F0_baseline"]=t
        out[f"{label}_F1_entry55"]=_with_entry_score(t,55)
        out[f"{label}_F2_entry60"]=_with_entry_score(t,60)
        out[f"{label}_F3_soxl_stage1_guard"]=_with_stage1_guard(t)
        out[f"{label}_F4_entry55_guard"]=_with_stage1_guard(_with_entry_score(t,55))
    return out
def _days(r):
    d=[int(c["holding_days"]) for c in r.closed_cycles]
    if int(r.open_position["quantity"])>0:d.append(int(r.open_position["holding_days"]))
    return d
def _open_dd(r):
    if int(r.open_position["quantity"])<=0:return 0.0
    a=float(r.open_position["average_price"]); m=float(r.open_position["market_price"]); return (m/a-1)*100 if a>0 else 0.0
def combined_metrics(results,annualization_days):
    eq=pd.concat([r.equity_curve.rename(s) for s,r in results.items()],axis=1,join="inner").sum(axis=1); initial,final=float(eq.iloc[0]),float(eq.iloc[-1]); years=max((eq.index[-1]-eq.index[0]).days/365.2425,1/365.2425); sh,so=risk_adjusted_metrics(eq,annualization_days); all_days=[d for r in results.values() for d in _days(r)]
    return {"total_return_pct":round((final/initial-1)*100,2),"cagr_pct":round(((final/initial)**(1/years)-1)*100,2),"mdd_pct":round(maximum_drawdown(eq)*100,2),"sharpe":round(sh,3),"sortino":round(so,3),"closed_cycles":sum(int(r.metrics["closed_cycles"]) for r in results.values()),"signals":sum(int(r.metrics["signals"]) for r in results.values()),"avg_holding_days_including_open":round(sum(all_days)/len(all_days),2) if all_days else 0.0,"max_holding_days_worst_symbol_including_open":max((max(_days(r),default=0) for r in results.values()),default=0),"mae_p95_worst_symbol_pct":min(float(r.metrics["mae_p95_pct"]) for r in results.values()),"worst_mae_pct":min(float(r.metrics["worst_mae_pct"]) for r in results.values()),"lockup_over_40_days_worst_symbol_pct":round(max((sum(d>40 for d in _days(r))/len(_days(r))*100 if _days(r) else 0) for r in results.values()),2),"open_price_drawdown_worst_symbol_pct":round(min(_open_dd(r) for r in results.values()),2)}
def settings(c):
    g=c.market_regime.get("soxl_sector_guard",{}); return {"entry_score":c.global_.entry_score,"tp":[float(c.take_profit.tp1_base),float(c.take_profit.tp2_base)],"soxl_guard_stages":list(g.get("blocked_stages",[])),"soxl_guard_rule":g.get("rule")}
def markdown_summary(report):
    lines=["# JDSS 2.0 Dual-Track Filter Search","","TP 4/6 and TP 4/8 are evaluated independently with the same confirmation/guard candidates.","","| Candidate | CAGR | MDD | P95 MAE | >40d lockup | Max hold | Open DD | Cycles | Avg hold |","|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name,c in report["candidates"].items():
        m=c["segments"]["validation_2021_2024"]["combined"]; lines.append(f"| {name} | {m['cagr_pct']:+.2f}% | {m['mdd_pct']:.2f}% | {m['mae_p95_worst_symbol_pct']:.2f}% | {m['lockup_over_40_days_worst_symbol_pct']:.2f}% | {m['max_holding_days_worst_symbol_including_open']}d | {m['open_price_drawdown_worst_symbol_pct']:.2f}% | {m['closed_cycles']} | {m['avg_holding_days_including_open']:.1f}d |")
    lines += ["","## Selection rule","","Pick the best filter inside each TP track first; then compare the two track winners. Reject candidates that leave the multi-year lockup unresolved unless they materially improve the risk/return profile."]
    return "\n".join(lines)+"\n"
def main():
    p=argparse.ArgumentParser(); p.add_argument("--end",default=datetime.now(UTC).date().isoformat()); p.add_argument("--output",type=Path,default=ROOT/"reports"/"v2_focused_backtest.json"); a=p.parse_args(); base=load_config(ROOT/"strategy.yaml"); src=YFinanceDataSource(ROOT/"data"/"cache"); warm=(datetime.fromisoformat("2011-01-01").date()-timedelta(days=400)).isoformat(); frames={s:src.daily(s,warm,a.end,refresh=True) for s in (*SYMBOLS,*BENCHMARKS)}; segs={"development_2011_2020":("2011-01-01","2020-12-31"),"validation_2021_2024":("2021-01-01","2024-12-31"),"recent_2025_present":("2025-01-01",a.end),"full_history":("2011-01-01",a.end)}; report={"generated_at":datetime.now(UTC).isoformat(),"data_end":a.end,"candidates":{}}
    for name,cfg in build_candidates(base).items():
        cand={"settings":settings(cfg),"segments":{}}
        for sn,(start,end) in segs.items():
            rs={s:BacktestEngine(cfg).run(s,frames[s],frames["SPY"],frames["QQQ"],start=start,end=end,slippage=base.backtest.default_slippage,sector_data={"SOXX":frames["SOXX"],"SMH":frames["SMH"]} if s=="SOXL" else None) for s in SYMBOLS}; cand["segments"][sn]={"combined":combined_metrics(rs,cfg.backtest.annualization_days),"symbols":{s:r.metrics for s,r in rs.items()}}
        report["candidates"][name]=cand
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"); md=a.output.with_suffix(".md"); md.write_text(markdown_summary(report),encoding="utf-8"); print(md.read_text(encoding="utf-8")); return 0
if __name__=="__main__": raise SystemExit(main())
