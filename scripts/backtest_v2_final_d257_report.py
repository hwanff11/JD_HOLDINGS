#!/usr/bin/env python3
"""Audit the selected D257 FINAL candidate and report calendar-year returns."""
from __future__ import annotations
import argparse, json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from backtest_v2_gap_grid import BENCHMARKS, SYMBOLS, _candidate, _run
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource
ROOT=Path(__file__).resolve().parent.parent

def annual(results, years):
    out={}
    for y in years:
        vals={}
        for s,r in results.items():
            q=r.equity_curve[r.equity_curve.index.year==y]
            if not q.empty:
                vals[s]=(float(q.iloc[-1])/float(q.iloc[0])-1)*100
        out[str(y)]={"combined_pct":sum(vals.values())/len(vals) if vals else None,"symbols":vals}
    return out

def worst(results):
    rows=[]
    for s,r in results.items():
        cycles=list(r.closed_cycles)
        if r.metrics.get("open_cycle"): cycles.append(r.metrics["open_cycle"])
        for c in cycles: rows.append((int(c.get("holding_days",0)),s,c))
    days,s,c=max(rows,key=lambda x:x[0])
    r=results[s]; cid=c.get("cycle_id")
    return {"symbol":s,"holding_days":days,"cycle":c,"trades":[t for t in r.trades if t.get("cycle_id")==cid],"signals":[x for x in r.signals if x.get("cycle_id")==cid]}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--end",default=datetime.now(UTC).date().isoformat()); p.add_argument("--output",type=Path,default=ROOT/"reports/v2_final_d257_report.json"); a=p.parse_args()
    base=load_config(ROOT/"strategy.yaml"); config=_candidate(base,(0.02,0.05,0.07)); source=YFinanceDataSource(ROOT/"data/cache")
    warm=(datetime.fromisoformat("2011-01-01").date()-timedelta(days=400)).isoformat(); frames={s:source.daily(s,warm,a.end,refresh=True) for s in (*SYMBOLS,*BENCHMARKS)}
    results=_run(config,frames,"2011-01-01",a.end); years=range(2011,datetime.fromisoformat(a.end).year+1)
    report={"generated_at":datetime.now(UTC).isoformat(),"strategy":"Entry/additional scores 55; weights 40/30/20/10; drops -2/-5/-7; TP 4/6; stage1 sector guard; TP1 remainder 20d avg+2%","worst_cycle":worst(results),"annual_returns":annual(results,years),"symbols":{s:r.metrics for s,r in results.items()}}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    w=report["worst_cycle"]; lines=["# JDSS FINAL D257 Report","",f"Worst cycle: {w['symbol']} {w['cycle'].get('start_date')} -> {w['cycle'].get('end_date')} ({w['holding_days']} trading days), MAE={float(w['cycle'].get('mae',0))*100:.2f}%","","## Trades"]
    for t in w["trades"]: lines.append(f"- {t.get('date')} {t.get('side')} {t.get('purpose')} price={t.get('price')} qty={t.get('quantity')} avg={t.get('average_price','-')} score={t.get('score','-')}")
    lines += ["","## Calendar-year returns","","| Year | Combined | TQQQ | SOXL |","|---:|---:|---:|---:|"]
    for y in years:
        x=report["annual_returns"][str(y)]; c=x["combined_pct"]
        if c is not None: lines.append(f"| {y} | {c:+.2f}% | {x['symbols'].get('TQQQ',0):+.2f}% | {x['symbols'].get('SOXL',0):+.2f}% |")
    a.output.with_suffix('.md').write_text('\n'.join(lines)+'\n',encoding='utf-8'); print('\n'.join(lines)); return 0
if __name__=='__main__': raise SystemExit(main())
