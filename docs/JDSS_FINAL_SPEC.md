# JDSS V3.2.2 최종 사양

공식 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**. 숫자 기준은 루트 `strategy.yaml`, 구현은 `src/jd_holdings/`, 현재 상태는 `CURRENT_WORK.md`가 기준입니다.

## 1. 자금 계약

- 시작 위험원금: **$50,000**
- HWM75 위험예산: `min(현재 평가액, 50,000 + 0.75 × max(0, 최고 평가액 - 50,000))`
- 새 최고자산 이익의 75%만 위험예산 증가에 반영
- 나머지 25% 이익은 JDSS 소유 현금으로 남지만 위험예산 증가에는 사용하지 않음
- 손실 시 외부 현금 자동보충 없음
- SGOV OFF
- live OFF

HWM은 완결 거래일 종가 기준 JDSS 평가액으로만 갱신합니다.

## 2. QQQ 동적 노출

사용 지표:
- SMA50, SMA200
- 21/63/126 거래일 수익률
- SMA200의 21거래일 기울기
- QQQ 20거래일 연환산 변동성

노출 규칙:
1. 필요한 warmup이 없으면 1.0x
2. 변동성 >= 30%면 0.5x
3. QQQ 종가 <= SMA200이면 1.0x
4. SMA50>SMA200, 63d>0, 126d>0이면 1.5x
5. 그 외 `21d>0 / 63d>0 / 126d>0 / SMA50>SMA200 / SMA200 slope>0` 중 3개 이상이면 1.25x
6. 나머지는 1.0x

새 달 첫 거래일 종가에서 전체 레짐을 reset하고 다음 거래일에 목표를 반영합니다. 월중 변동성 30% 브레이크는 0.5x로 감속만 하며 같은 달 안에서 다시 상향하지 않습니다.

## 3. 실제 ETF 배분

1.0x 이하에서는 QQQ만 사용합니다. 1.0x 초과분은 3배 ETF를 이용해 합성합니다.

- `leveraged sleeve weight = (target leverage - 1) / 2`
- 나머지는 QQQ

예: 1.5x에서 leveraged sleeve는 25%, QQQ는 75%입니다.

## 4. RS6M 반도체 상대강도

- 기준 ETF: **SOXX**
- lookback: **126 거래일**
- 조건: SOXX 126d return > 0 그리고 SOXX 126d return > QQQ 126d return
- 월 reset 시 조건 충족: leveraged sleeve의 **50% TQQQ / 50% SOXL**
- 조건 미충족: leveraged sleeve 100% TQQQ
- 월중 조건이 깨지면 SOXL portion을 TQQQ로 전환
- 한 번 이탈한 달에는 SOXL 재진입 금지, 다음 달 reset에서만 다시 판단

SOXX 대신 SMH로 proxy를 바꾼 강건성 테스트에서도 핵심 결과가 유사했지만 production frozen proxy는 SOXX입니다.

## 5. JDSS 5% virtual overlay

기존 H40-S3 과매도/반등 로직은 계속 계산하지만 독립 자금으로 주문하지 않습니다.

- TQQQ virtual JDSS cycle 활성: QQQ 최대 5%를 TQQQ로 이동
- SOXL virtual cycle 활성: QQQ 최대 5%를 SOXL로 이동
- 둘 다 활성: 총 5%를 2.5%씩 분배
- virtual active state는 연구 parity를 위해 한 거래일 지연 사용

기존 직접 H40 포지션, TP plan, direct booster 주문은 V3.2.2 정상상태가 아니며 reconciliation 오류입니다.

## 6. 주문·승인

- 목표비중 증가는 Telegram 2단계 승인 후 BUY
- 위험축소 SELL은 자동
- 목표수량은 HWM75 위험예산, 실제 JDSS 현금, 브로커 주문가능금액 중 가장 제한적인 금액으로 계산
- 주문 UNKNOWN, 불완전 위험축소, 브로커/DB 수량 불일치 시 SAFE_MODE
- QQQ 문제는 portfolio SAFE_MODE, TQQQ/SOXL 문제는 종목 SAFE_MODE와 portfolio reconciliation에 반영

## 7. 동일 Toss 계좌

V3.2.2가 직접 관리하는 티커는 **QQQ, TQQQ, SOXL**입니다. 브로커가 동일 티커를 합산하므로 개인물량을 같은 계좌에 섞으면 JDSS 원장과 분리할 수 없습니다. 따라서 개인 QQQ/TQQQ/SOXL 혼합보유를 금지합니다. QQQM 등 별도 티커는 별도 수량으로 구분 가능합니다.

## 8. 백테스트 계약

- 시작: 2011-01-01
- 초기자금: $50,000
- buy fee: 0.1%
- sell fee: 0.1%
- 기본 slippage: 0.1%
- next-session execution
- HWM75 적용
- SGOV OFF
- 동일 allocation/JDSS virtual-state 함수를 production/backtest에서 공유

Promotion 개발 중 canonical 재현값은 대략 CAGR **22.37%**, MDD **-30.93%**, Sharpe **1.004**입니다. 연구 frozen 기준 22.48/-30.93/1.007과 근접합니다.

## 9. 연구 한계

2023+는 후보 선택 과정에서 반복 관찰됐으므로 pristine OOS가 아닙니다. CSCV-style PBO 약 64.29% 경고도 유지합니다. 대표 승인으로 production 승격하지만 이 수치는 미래 QQQ 초과성과를 보장하지 않습니다.

## 10. 실거래 잠금

이번 릴리즈는 production 전략·서버·Telegram을 V3.2.2로 승격하지만 **live 주문 활성화는 포함하지 않습니다**. `portfolio.live_enabled=false`, 애플리케이션 hard lock, Oracle forced dry-run을 동시에 유지합니다.
