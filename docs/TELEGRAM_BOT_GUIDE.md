# JDSS V3.2.2 Telegram Bot 운영 가이드

Telegram 봇은 허용된 관리자 Chat ID만 사용합니다. V3.2.2는 **forced dry-run 전용**이며 live는 별도 승인 전까지 잠겨 있습니다.

전략 설명은 `STRATEGY_GUIDE.md`, 정확한 계약은 `JDSS_FINAL_SPEC.md`를 따릅니다.

## 주요 명령

| 명령 | 설명 |
|---|---|
| `/dashboard` | V3.2.2 통합 대시보드 |
| `/portfolio` | QQQ/TQQQ/SOXL 목표비중, HWM, 위험예산 |
| `/account` | Toss 계좌 조회 |
| `/status [종목]` | allocation 보유수량/목표상태 |
| `/score [TQQQ|SOXL]` | 기존 JDSS 5% virtual overlay 분석 |
| `/history [종목]` | 최근 JDSS 점수 이력 |
| `/signal` | 현재 위험증가 BUY 승인 대기 신호 |
| `/backtest` | V3.2.2 production-equivalent 백테스트 |
| `/order` | 열린 주문 확인 |
| `/errors` | 최근 이벤트/SAFE_MODE 기록 |
| `/guide` | V3.2.2 전략 설명 |
| `/help` | 전체 명령 안내 |

## 매수 승인

V3.2.2에서 목표비중이 증가해 BUY가 필요하면 기존 2단계 승인 흐름을 유지합니다.

1. 최신 가격/목표수량 검토
2. 짧은 유효시간의 최종 실행 승인
3. dry-run broker에 주문 제출
4. 체결 결과를 allocation 원장에 반영

위험예산은 HWM75, JDSS 보유현금, 브로커 주문가능금액 중 가장 제한적인 값으로 계산됩니다.

## 자동으로 처리되는 것

- QQQ 변동성 30% 브레이크에 따른 위험축소 SELL
- 월간 레짐/RS6M 변화에 따른 목표비중 감소 SELL
- 월중 SOXL 상대강도 이탈 시 SOXL→TQQQ one-way 전환의 위험축소 부분
- 주문/원장 정합성 점검

위험을 늘리는 BUY는 자동실행하지 않습니다.

## `/score`의 의미

V3.1.1과 달리 `/score`의 TQQQ/SOXL JDSS 점수는 독립 $20k 직접 매수전략이 아닙니다. 기존 H40-S3 가상 사이클이 활성화되는지를 계산해 **QQQ 최대 5%를 TQQQ/SOXL로 교체하는 overlay 입력**으로 사용합니다.

## SAFE_MODE

다음 상황에서는 신규 위험증가를 막습니다.

- Toss 수량과 SQLite allocation 원장 불일치
- 주문 결과 UNKNOWN
- 위험축소 SELL 불완전
- V3.1.1 direct H40 position 또는 TP plan 잔존
- 같은 계좌에서 JDSS가 관리하지 않는 개인 QQQ/TQQQ/SOXL 수량 발견

QQQ 문제는 portfolio SAFE_MODE로, TQQQ/SOXL 문제는 종목 및 portfolio reconciliation에 반영됩니다.

## 계좌 주의사항

V3.2.2가 직접 관리하는 종목은 QQQ, TQQQ, SOXL입니다. 같은 Toss 계좌에 개인 QQQ/TQQQ/SOXL을 혼합 보유하지 않습니다. QQQM처럼 별도 티커는 수량을 분리할 수 있습니다.

## live 잠금

- `strategy.yaml`: `live_enabled: false`
- 애플리케이션: V3.2.2 live hard-lock
- Oracle `.env`: `JDSS_TRADING_MODE=dry_run`, `JDSS_LIVE_CONFIRMATION=`

이번 V3.2.2 릴리즈는 전략 승격과 dry-run 배포까지이며 live 활성화는 포함하지 않습니다.
