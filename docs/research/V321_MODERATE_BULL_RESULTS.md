# JDSS V3.2.1 Moderate-Bull Research Results

## Status

- Research only.
- Production V3.1.1 is unchanged.
- V3.2-SHADOW remains historical reference only.
- No historical parameter tuning should be performed after this result.
- The selected candidate is a **SHADOW**, not an ADOPT candidate, because 2023+ data was observed during research.

## Goal

Improve V3.2-SHADOW participation in moderate bull / low-volatility correction regimes without giving up the QQQ-beating objective or the high-volatility crash brake.

Baseline frozen V3.2-SHADOW:

- low-vol bear below QQQ SMA200: 0.5x
- normal: 1.0x
- strong: 1.5x
- QQQ 20-day annualized volatility >= 30%: hard cap 0.5x
- production JDSS booster: 5% TQQQ/SOXL overlay, one-session delayed
- monthly normal regime decisions, daily risk downshift

## Phase 1 — Moderate bull 1.25x state

A structural 1.25x state was tested using multi-horizon trend/momentum signals. No fine parameter grid was used.

Best structure: `TREND_VOTE`.

Trend vote requires at least 3 of 5:

1. QQQ 1-month return > 0
2. QQQ 3-month return > 0
3. QQQ 6-month return > 0
4. SMA50 > SMA200
5. SMA200 21-session slope > 0

Results:

| Strategy | CAGR | MDD | Sharpe | Calmar | 3Y rolling wins vs QQQ |
|---|---:|---:|---:|---:|---:|
| V3.2 frozen | 21.10% | -31.49% | 0.922 | 0.670 | 12/14 |
| Trend Vote 1.25x | 21.86% | -31.49% | 0.935 | 0.694 | 13/14 |

The extra state improved overall participation, especially 2019, but did not fully solve 2015/2016/2025/2026.

Research run: `31677007560`.

## Phase 2 — Asymmetric high-volatility brake

Hypothesis: the >=30% volatility hard brake might be too conservative during positive high-volatility recoveries.

Tested keeping 0.75x or 1.0x when volatility was 30–40% but QQQ was above SMA200 with positive 1-month and 3-month momentum. Volatility >=40% or negative trend still forced 0.5x.

Result: rejected.

The asymmetric brake slightly reduced CAGR/Sharpe and did not materially improve 2025/2026. The 30% hard volatility brake was therefore retained.

Research run: `31677284680`.

## Phase 3 — Low-volatility soft-bear floor

The important discovery was to separate two different risks:

- **trend weakness**: QQQ below SMA200 but volatility still below 30%
- **crisis/high volatility**: 20-day annualized volatility >=30%

Instead of forcing both cases to 0.5x, the low-volatility bear floor was raised while the high-volatility crash brake remained 0.5x.

Two candidates were frozen before robustness testing:

### V3.2.1-BALANCED

- low-vol bear below SMA200: **0.75x**
- normal: 1.0x
- moderate bull Trend Vote: 1.25x
- strong: 1.5x
- volatility >=30%: hard cap 0.5x
- JDSS overlay: 5%

### V3.2.1-RETURN

Same as BALANCED except:

- low-vol bear below SMA200: **1.0x**

Full historical result before robustness:

| Strategy | CAGR | MDD | Sharpe | Calmar | 3Y rolling wins |
|---|---:|---:|---:|---:|---:|
| QQQ B&H | 18.95% | -35.12% | 0.941 | 0.540 | benchmark |
| V3.2 frozen | 21.10% | -31.49% | 0.922 | 0.670 | 12/14 |
| V3.2.1 BALANCED | 23.20% | -32.97% | 0.973 | 0.704 | 13/14 |
| **V3.2.1 RETURN** | **24.22%** | **-34.41%** | **1.000** | **0.704** | **14/14** |

Research run containing phase 3: `31677498673`.

## Final frozen robustness validation

Final validation run: **`31677848962`**.

The two candidates were not retuned during this validation.

### Full-period metrics

| Strategy | CAGR | MDD | Sharpe | Sortino | Calmar | Avg exposure |
|---|---:|---:|---:|---:|---:|---:|
| QQQ B&H | 18.95% | -35.12% | 0.941 | 1.216 | 0.540 | 99.99% |
| V3.2 frozen | 21.10% | -31.49% | 0.922 | 1.187 | 0.670 | 89.03% |
| V3.2.1 BALANCED | 23.20% | -32.97% | 0.973 | 1.263 | 0.704 | 90.35% |
| **V3.2.1 RETURN** | **24.22%** | **-34.41%** | **1.000** | **1.299** | **0.704** | 91.55% |

Starting from the same $50,000 in the historical simulation:

- QQQ final equity: about **$749,745**
- V3.2 frozen: about **$991,789**
- BALANCED: about **$1,296,785**
- RETURN: about **$1,474,633**

### Period decomposition

#### Train 2011–2018

| Strategy | CAGR | MDD | Sharpe |
|---|---:|---:|---:|
| QQQ | 14.98% | -22.79% | 0.901 |
| BALANCED | 18.91% | -27.71% | 0.878 |
| RETURN | **19.75%** | -27.71% | **0.905** |

#### Validation 2019–2022

| Strategy | CAGR | MDD | Sharpe |
|---|---:|---:|---:|
| QQQ | 15.93% | -35.11% | 0.663 |
| BALANCED | 20.71% | **-32.94%** | 0.836 |
| RETURN | **21.22%** | -34.06% | **0.845** |

#### Recent 2022+

| Strategy | CAGR | MDD | Sharpe |
|---|---:|---:|---:|
| QQQ | 14.36% | -34.68% | 0.691 |
| BALANCED | 18.12% | **-32.04%** | 0.770 |
| RETURN | **19.11%** | -33.51% | **0.792** |

#### Observed 2023+

This is **not pristine OOS** because these dates were observed during research.

| Strategy | CAGR | MDD | Sharpe |
|---|---:|---:|---:|
| QQQ | 32.19% | **-22.74%** | **1.512** |
| BALANCED | 36.48% | -27.38% | 1.321 |
| RETURN | **38.58%** | -28.61% | 1.368 |

The candidates earn higher raw returns in the recent bull period but QQQ remains superior on recent-period MDD and Sharpe. This is an important limitation and should not be hidden.

## Transaction-cost stress

Base assumption: 0.10% fee and 0.10% slippage.

| Cost case | QQQ CAGR | BALANCED CAGR / MDD / Sharpe | RETURN CAGR / MDD / Sharpe |
|---|---:|---:|---:|
| Fee 0.10%, slip 0.10% | 18.95% | 23.20% / -32.97% / 0.973 | **24.22% / -34.41% / 1.000** |
| Fee 0.10%, slip 0.20% | 18.94% | 22.85% / -33.07% / 0.963 | **23.85% / -34.57% / 0.989** |
| Fee 0.10%, slip 0.30% | 18.93% | 22.51% / -33.17% / 0.952 | **23.49% / -34.74% / 0.978** |
| Harsh: fee 0.20%, slip 0.20% | 18.92% | 22.49% / -33.13% / 0.953 | **23.46% / -34.71% / 0.978** |

Both candidates retain a substantial CAGR edge under deliberately harsher costs.

## Rolling robustness

3-year rolling windows:

- V3.2 frozen: **12/14** beat QQQ; worst `2014–2016`: -1.60pp CAGR vs QQQ
- BALANCED: **13/14** beat QQQ; worst `2014–2016`: -0.12pp
- RETURN: **14/14** beat QQQ; worst `2014–2016`: **+0.89pp**

This materially reduces the concern that the full-period result is driven by one isolated regime.

## Combined-family PBO

The complete V3.2.1 structural family was evaluated with 8-slice CSCV.

- combinations: 70
- **PBO: 11.43%**
- median logit rank: 1.7124
- RETURN was selected as the in-sample leader in **41/70** CSCV selections

Selection counts of major winners:

- V3.2.1 RETURN: 41
- soft-bear 1.0x base: 12
- soft-bear 0.75x dual-momentum daily: 10
- soft-bear 0.75x fast-momentum: 4
- moderate golden: 2
- asymmetric dual daily: 1

This is favorable but is not proof of future alpha. The candidate family itself was designed after inspecting history.

## Moving-block bootstrap versus QQQ

187 observed monthly returns, 6-month blocks, 60-month horizon, 5,000 simulations.

### BALANCED

- estimated 5-year QQQ outperformance probability: **83.02%**
- median 5-year excess return: **+19.13%**
- p10: **-6.15%**
- p90: **+48.59%**

### RETURN

- estimated 5-year QQQ outperformance probability: **88.32%**
- median 5-year excess return: **+24.32%**
- p10: **-1.72%**
- p90: **+53.75%**

These are resampling estimates from the historical sample, not guarantees or pristine out-of-sample evidence.

## Annual notes

RETURN materially fixes several V3.2 weak years:

| Year | QQQ | V3.2 frozen | V3.2.1 RETURN |
|---|---:|---:|---:|
| 2015 | 9.76% | 2.58% | **9.33%** |
| 2016 | 9.40% | 2.95% | 3.26% |
| 2019 | 38.40% | 23.62% | **37.30%** |
| 2022 | -33.22% | -29.72% | **-32.23%** |
| 2025 | 21.01% | 18.97% | 15.44% |
| 2026 YTD | 18.31% | 13.31% | **21.29%** |

2016 and 2025 remain weak. **Do not tune specifically to these years.** Chasing those isolated misses would create a high risk of historical overfitting.

## Research conclusion

### Preferred shadow: V3.2.1-RETURN

Frozen rule:

1. QQQ 20-day annualized volatility >=30% -> **0.5x hard risk brake**
2. Otherwise QQQ below SMA200 -> **1.0x** (low-volatility weakness is not treated as a crash)
3. Above SMA200, ordinary regime -> **1.0x**
4. Moderate bull Trend Vote >=3/5 -> **1.25x**
5. Strong regime (SMA50>SMA200, 3m>0, 6m>0, vol<30%) -> **1.5x**
6. Existing production JDSS booster remains a **5% TQQQ/SOXL overlay**, conservatively one session delayed
7. Normal regime decisions monthly; risk downshift daily
8. USD cash; no SGOV

Why RETURN over BALANCED:

- higher CAGR
- higher Sharpe
- same Calmar to three decimals
- QQQ-beating MDD still retained on the full sample
- 14/14 rolling 3-year CAGR wins
- stronger cost-stress survival
- stronger moving-block bootstrap statistics

### Important limitation

This is still **SHADOW**, not production.

Historical 2023+ data has already been observed. Therefore the next trustworthy evidence must come from **future untouched paper/dry-run data**. No further historical parameter tuning is recommended.

## Audit trail

- Phase 1 run: `31677007560`
- Phase 2 run: `31677284680`
- Phase 3 run: `31677498673`
- Final frozen robustness run: **`31677848962`**
- Final production V3.1.1 backtest job in the same final run: success
- Final Security run for the research head: success

The transient research scripts/workflow modifications used to generate these runs were intentionally removed from the final research branch. This document and the GitHub Actions audit trail preserve the result without polluting production source code.
