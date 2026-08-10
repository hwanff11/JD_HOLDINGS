#!/usr/bin/env python3
"""Compare JDSS FINAL plus SGOV-era idle-cash yield against SPY and QQQ."""
from __future__ import annotations
import argparse, json
from datetime import UTC, datetime, timedelta
from pathlib import Path
import pandas as pd
from backtest_v2_gap_grid import BENCHMARKS, SYMBOLS, _candidate, _run
from jd_holdings.config import load_config
from jd_holdings.infrastructure.market_data import YFinanceDataSource
ROOT=Path(__file__).resolve().parent.parent
INITIAL_PER_SYMBOL=10_000.0

def _mdd_pct(e): return float((e/e.cummax()-1).min()*100)
def _cagr_pct(e):
    years=(e.index[-1]-e.index[0]).days/365.25
    return float(((e.iloc[-1]/e.iloc[0])**(1/years)-1)*100) if years>0 else 0.0
def _summary(e): return {"start_equity":float(e.iloc[0]),"end_equity":float(e.iloc[-1]),"cumulative_return_pct":float((e.iloc[-1]/e.iloc[0]-1)*100),"cagr_pct":_cagr_pct(e),"mdd_pct":_mdd_pct(e)}
def _calendar_returns(e): return {str(y):float((v.iloc[-1]/v.iloc[0]-1)*100) for y,v in e.groupby(e.index.year) if len(v)>=2}
def _sgov_returns(index,sgov):
    raw=sgov["close"].dropna(); start=raw.index.min(); close=raw.reindex(index).ffill(); r=close.pct_change().fillna(0.0); r.loc[r.index<start]=0.0; return r,start
def _cash_balance_series(result,index):
    tc={pd.Timestamp(t["date"]):float(t["cash_after"]) for t in result.trades}; cash=INITIAL_PER_SYMBOL; vals=[]
    for d in index:
        if d in tc: cash=tc[d]
        vals.append(max(cash,0.0))
    return pd.Series(vals,index=index)
def _overlay(base,idle,r):
    interest=0.0; vals=[]
    for i,d in enumerate(base.index):
        if i>0: interest+=(float(idle.loc[d])+interest)*float(r.get(d,0.0))
        vals.append(float(base.loc[d])+interest)
    return pd.Series(vals,index=base.index)
def _buy_hold(frame,start,end,initial):
    v=frame.loc[start:end,"close"].dropna(); return v/v.iloc[0]*initial

def main():
    p=argparse.ArgumentParser(); p.add_argument("--end",default=datetime.now(UTC).date().isoformat()); p.add_argument("--output",type=Path,default=ROOT/"reports/v2_cash_overlay.json"); a=p.parse_args()
    base=load_config(ROOT/"strategy.yaml"); config=_candidate(base,(0.02,0.05,0.07)); source=YFinanceDataSource(ROOT/"data/cache"); warm=(datetime.fromisoformat("2011-01-01").date()-timedelta(days=400)).isoformat()
    frames={s:source.daily(s,warm,a.end,refresh=True) for s in (*SYMBOLS,*BENCHMARKS,"SGOV")}; results=_run(config,frames,"2011-01-01",a.end); idx=results["TQQQ"].equity_curve.index.intersection(results["SOXL"].equity_curve.index); cash_r,sgov_start=_sgov_returns(idx,frames["SGOV"])
    plain={s:r.equity_curve.reindex(idx) for s,r in results.items()}; cash={s:_cash_balance_series(r,idx) for s,r in results.items()}; overlay={s:_overlay(plain[s],cash[s],cash_r) for s in SYMBOLS}
    series={"JDSS_FINAL":sum(plain.values()),"JDSS_FINAL_SGOV_CASH":sum(overlay.values()),"SPY_BUY_HOLD":_buy_hold(frames["SPY"],"2011-01-01",a.end,20000).reindex(idx).ffill(),"QQQ_BUY_HOLD":_buy_hold(frames["QQQ"],"2011-01-01",a.end,20000).reindex(idx).ffill()}
    report={"generated_at":datetime.now(UTC).isoformat(),"cash_method":"0% idle-cash return before SGOV inception; SGOV adjusted-close daily return on actual idle cash thereafter","sgov_start":sgov_start.isoformat(),"metrics":{n:_summary(v) for n,v in series.items()},"annual_returns":{n:_calendar_returns(v) for n,v in series.items()}}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(report,indent=2),encoding="utf-8")
    lines=["# JDSS FINAL SGOV Cash Overlay","",f"SGOV overlay starts: {sgov_start.date()}","","| Strategy | End Equity | Cum Return | CAGR | MDD |","|---|---:|---:|---:|---:|"]
    for n,m in report["metrics"].items(): lines.append(f"| {n} | ${m['end_equity']:,.0f} | {m['cumulative_return_pct']:+.2f}% | {m['cagr_pct']:+.2f}% | {m['mdd_pct']:.2f}% |")
    lines += ["","## Calendar-year returns","","| Year | JDSS | JDSS + SGOV cash | SPY | QQQ |","|---:|---:|---:|---:|---:|"]
    annual=report["annual_returns"]
    for y in sorted({y for x in annual.values() for y in x}): lines.append(f"| {y} | {annual['JDSS_FINAL'].get(y,0):+.2f}% | {annual['JDSS_FINAL_SGOV_CASH'].get(y,0):+.2f}% | {annual['SPY_BUY_HOLD'].get(y,0):+.2f}% | {annual['QQQ_BUY_HOLD'].get(y,0):+.2f}% |")
    a.output.with_suffix('.md').write_text('\n'.join(lines)+'\n',encoding='utf-8'); print('\n'.join(lines)); return 0
if __name__=='__main__': raise SystemExit(main())
