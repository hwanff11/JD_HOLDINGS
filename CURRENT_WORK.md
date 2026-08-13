# JD_HOLDINGS Current Work

> 현재 전략·배포·검증 상태의 단일 기준입니다. 상세 전략은 `docs/JDSS_FINAL_SPEC.md`, 백테스트는 `docs/BACKTEST_REPORT.md`를 따릅니다.

## 현재 전략

- 전략: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- 시작 위험원금: **$50,000**
- HWM75 통제복리: 최고자산 증가분의 75%만 위험예산 확대에 반영
- QQQ 중심 동적 노출: **0.5 / 1.0 / 1.25 / 1.5x**
- 고변동 브레이크: QQQ 20일 연환산 변동성 30% 이상 → 0.5x
- RS6M: SOXX 126거래일 상대강도 우위 시 레버리지 슬리브의 50%를 SOXL로 분산
- RS 이탈: 월중 SOXL → TQQQ one-way exit
- JDSS H40-S3: 직접 자금전략이 아니라 최대 5% virtual overlay 신호엔진
- SGOV: OFF
- 모든 위험증가 BUY: Telegram 2단계 승인
- 위험축소 SELL: 자동
- live: **LOCKED OFF / forced dry-run**

## Canonical 백테스트 기준

2011-01-01~최신 완결 거래일, 매수/매도 수수료 각각 0.1%, 슬리피지 0.1% 기준으로 promotion 개발 중 재현된 값은 대략 다음과 같습니다.

- Total Return: 약 **+2,235%**
- CAGR: 약 **22.37%**
- MDD: 약 **-30.93%**
- Sharpe: 약 **1.004**
- 평균 노출: 약 **80%**

연구 기준값 22.48% / -30.93% / 1.007과 매우 근접하며, provider 데이터 수정 가능성을 고려해 CI canonical gate는 작은 허용범위를 사용합니다.

## 연구 경고

- 2023+는 후보 선택 과정에서 여러 번 확인되어 pristine OOS가 아닙니다.
- CSCV-style PBO 약 **64.29%** 경고가 남아 있습니다.
- V3.2.2 승격은 대표 승인에 따른 production 전략 계약 변경이며, 과거 성과가 미래 초과수익을 보장한다는 의미가 아닙니다.

## 운영/계좌 규칙

- V3.2.2는 QQQ/TQQQ/SOXL 수량을 자체 SQLite 원장과 Toss 보유수량으로 reconciliation합니다.
- 따라서 같은 Toss 계좌에 개인 **QQQ/TQQQ/SOXL**을 혼합 보유하지 않습니다. 개인 QQQM 등 별도 티커는 원장상 구분 가능합니다.
- V3.1.1 direct H40 포지션/TP plan이 남아 있으면 V3.2.2에서 정상상태로 인정하지 않고 SAFE_MODE 대상으로 봅니다.
- V3.1.1 → V3.2.2 최초 Oracle forced-dry-run 배포에서는 기존 SQLite를 `v322-migration` 이름으로 백업하고 새 원장을 초기화합니다.

## GitHub / Oracle

- 승격 PR: **#128**
- canonical workflows: CI / Deploy Oracle Dry Run / JDSS V3 Backtest / Security / Verify Oracle V3.2.2 Runtime
- PR이 CI·Security·Backtest를 모두 통과한 뒤 squash merge하고 `v3.2.2` release를 생성합니다.
- 병합 후 Oracle에는 최신 `main`을 forced dry-run으로 배포하고 `verify-oracle-v322-runtime.yml`로 runtime을 확인합니다.
- live 활성화는 이번 릴리즈 범위에 포함하지 않습니다.
