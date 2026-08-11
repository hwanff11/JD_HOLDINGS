from __future__ import annotations
import argparse, json, tempfile
from pathlib import Path
import pandas as pd
from jd_holdings.config import load_strategy_config
from jd_holdings.backtest.engine import BacktestEngine
from jd_holdings.backtest.portfolio_engine import PortfolioBacktestEngine
from jd_holdings.data_loader import MarketDataLoader


def patch_engine(variant: str):
    if variant == 'baseline_6m':
        return
    def filtered(cls, frame, index, months):
        history=frame.loc[:index[-1], 'close'].dropna()
        monthly=history.groupby(history.index.to_period('M')).last()
        ma6=monthly.rolling(6,min_periods=6).mean()
        active=monthly > ma6
        if variant == 'confirm_10m':
            active &= monthly > monthly.rolling(10,min_periods=10).mean()
        elif variant == 'slope_3m':
            active &= ma6 > ma6.shift(3)
        result=pd.Series(False,index=index)
        for ts in cls._month_end_sessions(index):
            result.loc[ts]=bool(active.get(ts.to_period('M'),False))
        return result
    PortfolioBacktestEngine._monthly_trend=classmethod(filtered)


def run(variant, output):
    text=Path('strategy.yaml').read_text(encoding='utf-8').replace('trend_months: 10','trend_months: 6',1)
    with tempfile.NamedTemporaryFile('w',suffix='.yaml',delete=False,encoding='utf-8') as f:
        f.write(text); cfgpath=f.name
    cfg=load_strategy_config(cfgpath)
    loader=MarketDataLoader(cfg)
    symbols=set(cfg.enabled_symbols)|set(cfg.portfolio.core_underlyings.values())|{cfg.idle_cash.symbol}
    frames={s:loader.load(s,start='2011-01-01') for s in symbols}
    common=None
    for s in cfg.enabled_symbols:
        common=frames[s].index if common is None else common.intersection(frames[s].index)
    start=common[0].date(); end=common[-1].date()
    booster={s:BacktestEngine(cfg).run(s,frames[s],frames,start=start,end=end,slippage=0.001) for s in cfg.enabled_symbols}
    patch_engine(variant)
    r=PortfolioBacktestEngine(cfg).run(frames,booster,start=start,end=end,slippage=0.001)
    Path(output).write_text(json.dumps(r.to_dict(),ensure_ascii=False,indent=2),encoding='utf-8')


def summary(path):
    m=json.loads(Path(path).read_text(encoding='utf-8'))['metrics']
    print(f"## {Path(path).stem}")
    print(f"- Total Return: {m['total_return_pct']:+.2f}%")
    print(f"- CAGR: {m['cagr_pct']:+.2f}%")
    print(f"- MDD: {m['mdd_pct']:.2f}%")
    print(f"- Sharpe: {m['sharpe']:.3f}")
    print('| 반기 | 수익률 |'); print('|---|---:|')
    for p,v in m.get('half_year_returns_pct',{}).items():
        if p >= '2022-H1': print(f'| {p} | {v:+.2f}% |')

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--variant'); p.add_argument('--output'); p.add_argument('--summarize')
    a=p.parse_args()
    if a.summarize: summary(a.summarize)
    else: run(a.variant,a.output)
