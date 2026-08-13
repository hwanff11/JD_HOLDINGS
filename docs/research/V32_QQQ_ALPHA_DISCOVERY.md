# JDSS V3.2 QQQ Alpha Discovery

## Goal
Find a simple, reproducible V3.2 structure that beats QQQ buy-and-hold on compounded return while controlling drawdown.

## Production safety
- `main` / V3.1.1 is unchanged.
- Oracle and Telegram are unchanged.
- This branch is research only and must not be merged as production.

## Benchmark
- QQQ buy-and-hold
- Same initial capital: $50,000
- Adjusted OHLCV data
- Strategy costs: 0.10% buy fee, 0.10% sell fee, 0.10% slippage
- Benchmark receives the same execution-cost treatment.

## Fixed research windows
- train: 2011-01-01 ~ 2018-12-31
- validation: 2019-01-01 ~ 2022-12-31
- locked OOS: 2023-01-01 ~ latest completed session
- recent stress: 2022-01-01 ~ latest completed session
- full: 2011-01-01 ~ latest completed session

Candidate selection uses train + validation only. OOS is evaluated only after candidate names are locked.

## Literature-inspired families
1. Trend-gated TQQQ
2. QQQ/TQQQ trend ladder
3. Volatility-targeted QQQ/TQQQ exposure
4. Relative-momentum rotation between QQQ/TQQQ and SOXX/SOXL
5. Crash-reclaim leverage after large drawdowns

All signals use completed bars and execute on the next session open. No look-ahead.

## V3.2 target
A candidate is interesting only if it can plausibly satisfy all of:
- CAGR > QQQ
- MDD < QQQ, or materially better return/MDD tradeoff
- Sharpe > QQQ
- 2022 drawdown defense remains meaningful
- 2023+ participation is materially better than V3.1.1
- no single isolated period explains most of the excess return

## Decision labels
- KEEP: QQQ or V3.1.1 remains preferable
- SHADOW: promising, but OOS or robustness is insufficient
- ADOPT CANDIDATE: train/validation/OOS and cost stress support a production implementation PR
