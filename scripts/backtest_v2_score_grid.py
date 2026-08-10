#!/usr/bin/env python3
"""Isolate FINAL entry/additional-entry score thresholds."""
from __future__ import annotations
import argparse, json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from backtest_v2_focus import BENCHMARKS, SYMBOLS, _with_entry_score, _with_stage1_guard, _with_tp, combined_metrics
from backtest_v2_remainder_exit import RemainderExitEngine
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource
ROOT=Path(__file__).resolve().parent.parent

def config_for(base, entry, scores):
    c=_with_stage1_guard(_with_entry_score(_with_tp(base,"0.06"),entry))
    stages={s:replace(c.additional_entry.stages[s],min_score=v) for s,v in zip((2,3,4),scores,strict=True)}
    return replace(c,additional_entry=replace(c.additional_entry,stages=stages))

def run(c,frames,start,end):
    out={}
    for symbol in SYMBOLS:
        sector={"SOXX":frames["SOXX"],"SMH":frames["SMH"]} if symbol=="SOXL" else None
        out[symbol]=RemainderExitEngine(c,wait_days=20,target_pct=Decimal("0.02")).run(symbol,frames[symbol],frames["SPY"],frames["QQQ"],start=start,end=end,slippage=c.backtest.default_slippage,sector_data=sector)
    return out

def main():
    p=argparse.ArgumentParser(); p.add_argument("--end",default=datetime.now(UTC).date().isoformat()); p.add_argument("--output",type=Path,default=ROOT/"reports/v2_score_grid.json"); a=p.parse_args()
    base=load_config(ROOT/"strategy.yaml"); source=YFinanceDataSource(ROOT/"data/cache")
    warm=(datetime.fromisoformat("2011-01-01").date()-timedelta(days=400)).isoformat(); frames={s:source.daily(s,warm,a.end,refresh=True) for s in (*SYMBOLS,*BENCHMARKS)}
    specs={"E55_S525456":(55,(52,54,56)),"E55_S555555":(55,(55,55,55)),"E55_S555759":(55,(55,57,59)),"E55_S556065":(55,(55,60,65)),"E50_S525456":(50,(52,54,56)),"E50_S505254":(50,(50,52,54))}
    segs={"validation_2021_2024":("2021-01-01","2024-12-31"),"full_history":("2011-01-01",a.end)}; report={"generated_at":datetime.now(UTC).isoformat(),"candidates":{}}
    for name,(entry,scores) in specs.items():
        c=config_for(base,entry,scores); item={"entry":entry,"scores":scores,"segments":{}}
        for sn,(start,end) in segs.items():
            r=run(c,frames,start,end); item["segments"][sn]={"combined":combined_metrics(r,c.backtest.annualization_days),"symbols":{s:x.metrics for s,x in r.items()}}
        report["candidates"][name]=item
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# JDSS FINAL Score Grid","","| Candidate | Val CAGR | MDD | P95 MAE | >40d | Cycles | Full CAGR | Full MDD | Full Cycles |","|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for n,i in report["candidates"].items():
        v=i["segments"]["validation_2021_2024"]["combined"]; f=i["segments"]["full_history"]["combined"]
        lines.append(f"| {n} | {v['cagr_pct']:+.2f}% | {v['mdd_pct']:.2f}% | {v['mae_p95_worst_symbol_pct']:.2f}% | {v['lockup_over_40_days_worst_symbol_pct']:.2f}% | {v['closed_cycles']} | {f['cagr_pct']:+.2f}% | {f['mdd_pct']:.2f}% | {f['closed_cycles']} |")
    a.output.with_suffix(".md").write_text("\n".join(lines)+"\n",encoding="utf-8"); print("\n".join(lines)); return 0
if __name__=="__main__": raise SystemExit(main())
