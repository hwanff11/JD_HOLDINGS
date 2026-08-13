# JDSS V3.2.2 백테스트 보고서

공식 전략은 **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**입니다. 정식 재현 기준은 `strategy.yaml`, production `PortfolioBacktestEngine`, GitHub Actions `JDSS V3 Backtest`입니다.

## 공통 조건

- 2011-01-01 ~ 최신 완결 거래일
- 초기 위험원금 $50,000
- buy fee 0.1%
- sell fee 0.1%
- 기본 slippage 0.1%
- next-session execution
- HWM75 controlled compounding
- QQQ/TQQQ/SOXL allocation
- SGOV OFF
- 기존 JDSS는 5% virtual overlay signal

## Canonical 결과

Promotion production-equivalent 재현값:

| 지표 | V3.2.2 | QQQ 연구 기준 |
|---|---:|---:|
| Total Return | 약 +2,235% | 약 +1,399% |
| CAGR | **22.37%** | 18.95% |
| MDD | **-30.93%** | -35.12% |
| Sharpe | **1.004** | 0.941 |
| 평균 노출 | 약 80% | 100% |

연구 frozen 후보는 CAGR 22.48%, MDD -30.93%, Sharpe 1.007이었습니다. 데이터 provider의 소폭 수정과 실행 시점 차이를 고려해 canonical CI는 CAGR 22.0~22.8%, MDD -31.6~-30.3%, Sharpe 0.94~1.07 범위를 통과조건으로 둡니다.

## 최근 연구 기준 연도

- 2023: V3.2.2 +58.84%, QQQ +54.85%
- 2024: V3.2.2 +32.47%, QQQ +25.58%
- 2025: V3.2.2 +18.96%, QQQ +20.77%
- 2026 YTD 연구 시점: V3.2.2 +29.14%, QQQ +18.08%

2023+는 이미 후보 선택 과정에서 반복 관찰된 데이터이므로 pristine OOS로 취급하지 않습니다.

## 강건성

- SOXL sleeve 비중 25/50/75%에서 CAGR 22%대, MDD 약 -30~-32%의 부드러운 trade-off가 나타났습니다.
- SOXX 대신 SMH를 relative-strength proxy로 사용해도 full-period 결과가 거의 동일했습니다.
- RS lookback 105/126/147일에서 MDD와 최근 CAGR은 유사했고 105일의 장기 rolling 안정성은 다소 약했습니다.
- 월 reset 시점을 1/6/11번째 거래일로 이동하면 성과가 점진적으로 낮아져 timing edge는 있으나 1일짜리 cliff는 아니었습니다.
- 높은 fee/slippage에서도 장기 CAGR 우위가 유지됐습니다.

## 과최적화 경고

전체 연구 과정의 CSCV-style PBO 추정값이 약 **64.29%**로 높습니다. 따라서 최종 전략은 이후 조건을 더 붙여 과거 성과를 개선하지 않고 frozen했습니다. 최근 데이터 오염과 PBO 경고를 인지한 상태에서 대표 승인으로 production 승격합니다.

## 해석

V3.2.2의 장점은 단순 1.25~1.5배 고정 레버리지보다 낙폭을 낮추면서 QQQ보다 높은 장기 CAGR을 노린다는 점입니다. 반대로 2025처럼 QQQ 상승을 완전히 따라가지 못하는 해가 존재하며, QQQ보다 매년 이기는 전략은 아닙니다.

과거 백테스트는 미래 수익을 보장하지 않습니다. 이번 릴리즈에서는 live를 켜지 않고 forced dry-run 안전계약을 유지합니다.
