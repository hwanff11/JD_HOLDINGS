# JDSS 공식 사양 — 현재 운용 계약

현재 공식 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**. 숫자 기준은 루트 [`strategy.yaml`](../strategy.yaml), 구현은 [`src/jd_holdings/`](../src/jd_holdings/), 현재 배포·운영 상태는 [`CURRENT_WORK.md`](../CURRENT_WORK.md)가 기준입니다.

이 파일은 이름의 `FINAL` 때문에 버전별 복사본을 만든다는 뜻이 아니라, **현재 production이 따라야 하는 단 하나의 규범 계약**이라는 뜻입니다. 새 릴리즈는 이 파일을 제자리에서 갱신하고 과거 계약은 Git tag와 [`HISTORY.md`](HISTORY.md)에서 찾습니다.

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

- 목표비중 증가는 Telegram 검토 승인과 최종 실행 승인을 모두 통과한 뒤 BUY한다. 즉 BUY는 반자동이다.
- 위험축소 SELL은 승인을 기다리지 않고 자동 실행 대상으로 처리한다.
- 같은 리밸런싱에서 SELL과 BUY가 함께 필요하면 위험축소 SELL의 완료·정합성을 먼저 확인하고 위험증가 BUY는 별도 2단계 승인을 기다린다.
- 목표수량은 HWM75 위험예산, 실제 JDSS 현금, 브로커 주문가능금액 중 가장 제한적인 금액으로 계산
- 완결봉에서는 목표비중과 전략 generation만 저장하고, `target_qty`는 다음 거래세션이 시작된 후 최신 가격으로 한 번만 고정
- 목표주수 고정·자동 위험축소 SELL·BUY 승인 실행은 설정상 허용된 주문 세션에서만 수행하고 토스 08:50~08:59 KST 점검시간에는 주문하지 않음
- 열린 코어 BUY와 아직 allocation 원장에 반영되지 않은 체결수량을 잔여 목표수량에서 차감
- 목표 변경 전 기존 allocation BUY·SELL을 취소·정산하고, 유효시간이 끝난 코어 BUY를 감시기에서 취소
- 주문예약 트랜잭션에서 HWM75 예산과 종목 잔여 목표수량을 함께 재검사
- 동일 client order ID 재시도는 브로커 최신 상태를 DB에 먼저 저장한 뒤 체결 delta만 반영
- 브로커 응답의 client order ID·order ID·종목·매매방향·주문/체결수량을 예약 주문과 대조하고, 불일치를 `UNKNOWN`으로 처리
- 누적 체결수량은 감소할 수 없고 종료 주문은 열린 상태로 되돌릴 수 없음
- 누적 체결수량과 누적 체결금액은 각각 이전 원장 적용값과의 delta만 반영
- `PENDING_CANCEL`·`PENDING_REPLACE`를 포함해 종료 상태가 아닌 주문은 모두 열린 주문으로 예약·감시
- 재시작 때 같은 전략 generation의 저장 `target_qty`와 현재 보유·열린 주문의 차이를 사용해 미완료 BUY gap만 복구
- 위험축소 SELL은 종료 상태·체결 원장 반영·정합성 확인을 모두 끝내기 전까지 신규 BUY를 허용하지 않음
- 과거 H40-S3 직접 BUY 신호는 V3.2.2 실행 계층에서 무효화
- 주문 UNKNOWN, 불완전 위험축소, 브로커/DB 수량 불일치 시 SAFE_MODE
- QQQ 문제는 portfolio SAFE_MODE, TQQQ/SOXL 문제는 종목 SAFE_MODE와 portfolio reconciliation에 반영

## 7. forced dry-run과 실제 Toss 계좌 경계

- 현재 forced dry-run의 주문·보유수량·열린 주문은 `MarketDataDryRunBroker`와 SQLite JDSS 원장에서 관리한다.
- dry-run reconciliation은 SQLite와 이 모의 브로커 상태를 대조한다. 이것을 실제 Toss 보유수량과의 reconciliation으로 표현하지 않는다.
- 실제 Toss 계좌는 `/account`, 계좌 요약과 `toss-smoke` 같은 read-only 경로로 별도 조회할 수 있다.
- Toss 조회 결과는 dry-run 원장에 자동 채택하지 않고, dry-run 주문을 Toss 주문으로 변환하지 않는다.
- 실제 계좌 조회 실패나 값 불명확을 임의의 0 또는 성공으로 해석하지 않는다.

## 8. 최초 계좌 적용 preflight 계약

전략 선택·production 승격과 실제 계좌 적용은 서로 다른 변경이다. live 잠금을 검토하기 전 최소한 다음을 증명해야 한다.

1. 배포 SHA, package/config/전략 ID와 이 사양의 일치
2. 설정 검증·단위/통합 테스트·no-lookahead 기준 백테스트 통과
3. 기존 DB의 전략 세대·스키마 호환성, 열린 주문, 부분체결, UNKNOWN, legacy position/TP 상태와 복구 가능한 백업
4. 실제 Toss의 관리 티커 보유수량, 열린 주문, 주문가능금액 및 개인 동일 티커 혼합 여부
5. forced dry-run의 목표 산출, 자동 위험축소 SELL, 반자동 BUY, 주문 감시, 재시작과 reconciliation 한 사이클
6. SAFE_MODE 사유가 없고 가격·수량·세션 변경 시 기존 승인이 폐기되는지 확인
7. 실제 주문 경계와 preflight 구현에 대한 해당 릴리즈의 검증 증거 및 대표의 명시적 승인

현재 자동화 범위는 구현과 [`CURRENT_WORK.md`](../CURRENT_WORK.md)의 검증 상태를 기준으로 판단한다. 이 체크리스트의 존재만으로 live 준비가 완료됐다고 간주하지 않는다.

## 9. 동일 Toss 계좌

V3.2.2가 직접 관리하는 티커는 **QQQ, TQQQ, SOXL**입니다. 브로커가 동일 티커를 합산하므로 개인물량을 같은 계좌에 섞으면 JDSS 원장과 분리할 수 없습니다. 따라서 개인 QQQ/TQQQ/SOXL 혼합보유를 금지합니다. QQQM 등 별도 티커는 별도 수량으로 구분 가능합니다.

## 10. 백테스트 계약

- 시작: 2011-01-01
- 초기자금: $50,000
- buy fee: 0.1%
- sell fee: 0.1%
- 기본 slippage: 0.1%
- next-session execution
- HWM75 적용
- SGOV OFF
- 동일 allocation/JDSS virtual-state 함수를 production/backtest에서 공유

승인된 기준 결과와 QQQ 비교는 [`STRATEGY_GUIDE.md`](STRATEGY_GUIDE.md), 최신 실행 ID와 검증 상태는 [`CURRENT_WORK.md`](../CURRENT_WORK.md)에서 관리한다. 이 규범 문서에 실행할 때마다 바뀌는 결과값을 복제하지 않는다.

## 11. 연구 한계

전략 승격은 미래 QQQ 초과성과를 보장하지 않습니다. 현재 연구의 OOS 오염·과최적화 경고와 기간별 한계는 [`STRATEGY_GUIDE.md`](STRATEGY_GUIDE.md), 당시 채택 근거는 [`HISTORY.md`](HISTORY.md)가 소유합니다. 이 사양은 그런 한계를 숨기거나 실거래 안전장치를 완화하는 근거로 사용할 수 없습니다.

## 12. 실거래 잠금

현재 계약은 **live 주문 활성화를 포함하지 않습니다**. `portfolio.live_enabled=false`, 애플리케이션 hard lock, Oracle forced dry-run을 동시에 유지합니다. 미래 릴리즈가 이를 바꾸려면 제8절 preflight와 별도의 명시적 승인을 충족하고 설정·코드·문서·테스트를 같은 변경에서 갱신해야 합니다.
