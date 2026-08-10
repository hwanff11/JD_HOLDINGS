# JDSS 개발자 가이드(Developer Guide)

> **레거시 v1.1.2 기준선 문서.** 현재 FINAL 운영·배포 절차는
> [`../../infra/DEPLOYMENT.md`](../../infra/DEPLOYMENT.md), 전략과 설정은
> [`../../JDSS_FINAL_SPEC.md`](../../JDSS_FINAL_SPEC.md)와 [`../../../strategy.yaml`](../../../strategy.yaml)을
> 따른다.

**v1.1.2 — 공식 기준선(Official Baseline)**

---

## 1. 목적

JDSS를 장기간 안정적으로 유지보수하기 위한 개발 규칙이다.

핵심:

- 단순성(Simple)
- 명시성(Explicit)
- 테스트 가능성(Testable)
- 복구 가능성(Recoverable)
- 중복 실행 방지(Idempotent)
- 상태 관찰 가능성(Observable)

---

## 2. 주요 개발 용어

| 용어 | 설명 |
|---|---|
| Pure Function | 순수 함수. 같은 입력이면 항상 같은 출력이며 DB/API를 변경하지 않는 함수 |
| Side Effect | 외부 상태 변경. 주문, DB 저장, 메시지 전송 등 |
| Dataclass | 관련 데이터를 명시적 필드로 묶는 Python 자료구조 |
| Enum | 허용된 상태값을 고정하는 열거형 |
| Idempotency | 같은 요청을 여러 번 받아도 결과가 한 번만 발생하도록 보장하는 특성 |
| Optimistic Lock | 버전값을 이용해 동시에 같은 데이터를 수정하는 충돌을 방지하는 방식 |
| Reconciliation | 증권사 실제 상태와 내부 DB 상태를 비교·복구하는 정합성 점검 |
| Golden Test | 고정 입력에 대한 결과를 기준값으로 보관해 의도치 않은 변경을 찾는 테스트 |
| Parity Test | 백테스트와 실거래 엔진이 동일 입력에서 동일 판단을 내리는지 확인하는 테스트 |
| Mock | 실제 API 대신 테스트용으로 동작을 흉내 내는 객체 |

---

## 3. 최우선 개발원칙

```text
전략 계산
≠
주문 실행
≠
DB 저장
≠
Telegram UI
```

전략 함수 내부에서 금지:

- Toss API 호출
- Telegram 호출
- DB 저장
- 환경변수 직접 조회
- 시스템 현재시간 직접 조회

필요한 값은 함수 입력으로 전달한다.

---

## 4. 단일 기준원천(Single Source of Truth)

우선순위:

1. `JDSS.md`
2. `strategy.yaml`
3. 자동화 테스트
4. 실제 코드

문서와 코드가 충돌하면 코드를 임의로 정답으로 취급하지 않는다.

---

## 5. 순수 함수(Pure Function)

권장:

```text
calculate_indicators()
evaluate_regime()
calculate_score()
calculate_grade()
evaluate_eligibility()
score_to_exposure()
update_cycle_exposure_cap()
calculate_stage_budget()
evaluate_entry()
evaluate_additional_entry()
evaluate_rebuy()
calculate_execution_price_ceiling()
calculate_qty()
calculate_take_profit()
evaluate_risk_review()
```

---

## 6. 외부 상태 변경(Side Effect)

실제 주문:

```text
OrderManager만 가능
```

DB 쓰기:

```text
Repository / Application 계층만 가능
```

Telegram:

```text
Telegram Adapter 계층만 가능
```

---

## 7. 자료형

### 가격·자금

가능하면 `Decimal` 사용:

- 가격
- 예산
- 수수료
- 평단
- 실현손익
- 현재 원가

지표 계산은 `float` 허용.

### 상태

`Enum` 사용:

```text
PositionState
MarketRegime
SignalGrade
DecisionType
OrderPurpose
OrderStatus
RiskReviewLevel
ErrorSeverity
```

---

## 8. 설정값 하드코딩 금지

금지:

```python
if score >= 82:
```

권장:

```python
if score >= config.global_.entry_score:
```

전략 숫자는 `strategy.yaml`에서 관리한다.

---

## 9. 설정파일 검증(Config Validation)

서버 시작 시 필수 검증:

```text
entry_score는 0~100
분할비중 합계 = 1.0
누적비중 마지막값 = 1.0
2차 하락폭 < 3차 하락폭 < 4차 하락폭
2차 점수 <= 3차 점수 <= 4차 점수
TP1 < TP2
수수료 >= 0
종목 자금 > 0
승인 TTL > 0
추격매수 상한 >= 0
```

잘못되면 실거래 모드 시작 금지.

---

## 10. 사이클 자금 필드 구분

혼동 방지를 위해 다음 값을 구분한다.

### `cycle_exposure_cap`

현재 사이클에서 허용된 최대 포지션 원가.

### `staged_entry_capital`

1~4차 분할매수에 실제 사용한 누적 자금.

### `current_cost_basis`

현재 실제 보유주식의 원가.

TP 매도 후에는 감소할 수 있다.

### `cash_remaining`

종목 전략자금 중 현재 사용 가능한 금액.

재매수는 `staged_entry_capital`이 아니라 `current_cost_basis`와 `cycle_exposure_cap`을 기준으로 한도를 검증한다.

---

## 11. 사이클 투자한도 테스트

예:

```text
1차 Score 84
→ cap $6,000
→ 1차 누적목표 $900

2차 Score 89
→ cap $8,000
→ 2차 누적목표 $2,800
→ 신규예산 $1,900
```

반드시 이 예제가 자동 테스트로 통과해야 한다.

---

## 12. 중복 실행 방지(Idempotency)

같은 Telegram 버튼이 여러 번 눌려도 실제 주문은 한 번만 실행되어야 한다.

사용:

- `signal_id`
- `approval_id`
- `approval_token`
- `processed`
- `client_order_id`
- DB Unique Constraint

애플리케이션의 단순 `if`문만 믿지 않는다.

---

## 13. Client Order ID(클라이언트 주문 식별자)

권장:

```text
JDSS:{strategy}:{symbol}:{cycle}:{signal}:{purpose}:{stage}
```

증권사 길이제한이 있으면 해시(Hash)로 축약한다.

---

## 14. 상태 전이(State Transition)

모든 상태변경:

```text
expected_state 확인
→ 전이 가능여부 확인
→ DB transaction
→ 상태이력 저장
```

직접 SQL로 state만 바꾸는 코드 금지.

---

## 15. 낙관적 잠금(Optimistic Lock)

`positions.version` 필드 사용 권장.

예:

```sql
UPDATE positions
SET ...
WHERE symbol = ?
  AND version = ?
```

업데이트 성공 시 version 증가.

동시에 Telegram callback과 주문감시기가 상태를 변경하는 충돌을 방지한다.

---

## 16. DB 트랜잭션

가능하면 함께 처리:

- 주문처리 상태
- Position 반영
- State 변경
- State History 저장

증권사 주문은 외부 시스템이므로 DB 트랜잭션과 완전한 원자성을 만들 수 없다.

따라서:

> **중복 실행 방지 + 정합성 점검**

이 필수다.

---

## 17. 증권사 상태 우선(Broker First)

실제 포지션의 최종 기준은 증권사다.

우선순위:

1. 증권사 실제 잔고
2. 증권사 실제 체결
3. 증권사 미체결 주문
4. 내부 DB

DB와 불일치하면 DB 상태만 믿고 추가 주문을 보내지 않는다.

---

## 18. 테스트용 Toss Client(MockTossClient)

지원해야 할 상황:

- 전량체결
- 부분체결
- 주문거절
- API Timeout
- 중복 응답
- 지연체결
- 미체결 유지
- 주문취소
- 잔고 불일치
- 주문은 접수됐지만 응답이 끊긴 상황

---

## 19. 모의실행(Dry Run)

```yaml
dry_run: true
```

모의실행에서도 실제처럼 수행:

- 지표
- 점수
- 신호
- 승인
- 가격조건
- 사이클 투자한도
- 수량
- TP
- DB
- Telegram 메시지

실제 증권사 주문만 보내지 않는다.

---

## 20. 주문 API Timeout 처리

주문요청 후 Timeout이 발생했다고 바로 재주문하면 중복매수가 날 수 있다.

정상:

```text
1. 주문 요청
2. Timeout
3. 같은 client_order_id로 증권사 주문 조회
4. 주문 존재 → 재주문 금지
5. 주문 없음이 확실 → 재처리 검토
6. 판단 불가 → SAFE_MODE
```

---

## 21. 추격매수·단계가격 테스트

추가매수의 최종 지정가는 반드시:

```text
CurrentPrice
StageTriggerPrice
MaxChasePrice
BuyLimitBuffer
```

를 함께 검증한다.

필수 예:

```text
2차 Trigger = $97
현재가 = $96.90
Raw Limit = $97.38

최종 Limit은 $97 이하
```

버퍼 때문에 Trigger 위로 주문이 나가면 실패다.

---

## 22. 단위 테스트(Unit Test)

### 지표

- CCI5
- CCI10
- RSI5
- RSI14
- ATR
- Bollinger
- EMA
- VolumeRatio
- ClosePosition

### 전략

- Regime
- Score
- Grade
- Eligibility
- Exposure
- Cycle Exposure Cap
- Stage Budget
- Anchor
- Additional Entry
- Rebuy Recovery
- Re-Oversold
- Entry Chase
- Execution Price Ceiling
- Risk Review

### 주문·포지션

- Qty
- AvgPrice
- Fee
- Current Cost Basis
- TP
- Partial Fill
- Rebuy TP Reset

### 시스템

- State
- Approval
- TTL
- Duplicate Callback
- Duplicate Order
- Reconciliation
- SAFE_MODE

---

## 23. 경계값 테스트(Boundary Test)

점수:

```text
75 / 76
81 / 82
87 / 88
91 / 92
```

추가매수:

```text
2.99 / 3.00%
4.99 / 5.00%
7.99 / 8.00%
```

추격매수:

```text
1.99 / 2.00%
```

CCI:

```text
-99.99 / -100
-149.99 / -150
-199.99 / -200
```

RSI5:

```text
30.01 / 30
25.01 / 25
20.01 / 20
```

특수:

```text
High == Low
→ ClosePosition == 0.0
```

---

## 24. 기준결과 테스트(Golden Test)

고정 OHLCV 데이터를 저장하고 다음 결과를 기준값으로 관리한다.

- 지표
- 시장 국면
- JDSS 점수
- 등급
- 필수 통과 결과
- 매매 판단
- 투자한도
- 단계별 주문금액

전략 변경 시 의도하지 않은 결과변화를 확인한다.

---

## 25. 백테스트/실거래 일치 테스트(Parity Test)

동일 입력에서 반드시 동일해야 한다.

- Score
- Grade
- Regime
- Eligibility
- Cycle Exposure Cap
- Stage Budget
- Entry Decision
- Additional Entry Decision
- Rebuy Decision
- Execution Price Ceiling
- TP

백테스트 전용 조건문을 만들지 않는다.

---

## 26. 부분체결 테스트

예:

```text
10주 주문
→ 4주 체결
→ 120초 경과
→ 잔여 6주 취소
→ 실제 포지션 4주
→ 해당 단계 완료
→ TP 합계 4주
→ 잔여 6주 자동 재주문 없음
```

---

## 27. TP 부분체결 테스트

예:

```text
TP1 목표 10주
→ 6주 체결
→ 주문 취소
```

정상:

- `tp1_filled_qty = 6`
- 아직 `PARTIAL_TP_1` 전환 금지
- 남은 TP1 4주만 복구
- 10주 전체 완료 후 상태전환

---

## 28. 재매수 테스트

필수:

```text
첫 TP1 완료
→ recovery armed
→ re-oversold
→ rebuy 체결
→ 기존 TP2 취소
→ 새 평단
→ 새 TP1/TP2
→ rebuy_count = 1
→ 두 번째 TP1 후 재매수 금지
```

---

## 29. 승인 테스트

필수:

- 정상 2단계 승인
- 검토 토큰 만료
- 실행 토큰 만료
- 토큰 두 번 사용
- 허용되지 않은 Chat ID
- 이미 처리된 신호
- 상태가 바뀐 뒤 늦게 승인
- 추격매수 상한 초과
- 추가매수 Trigger 위로 가격회복
- 허용 세션(애프터마켓/프리마켓) 판정
- 시간외장 시장가 주문 차단

---

## 30. 정합성 점검 테스트(Reconciliation Test)

필수:

- DB 수량 < 증권사 수량
- DB 수량 > 증권사 수량
- DB TP 있음 / 증권사 없음
- 증권사 TP 있음 / DB 없음
- 주문 Filled / DB WAITING
- DB EMPTY / 증권사 Position 있음
- TP Plan 수량 합계 != 실제 보유수량

안전하게 자동복구 불가:

```text
SAFE_MODE
```

---

## 31. 로그(Logging)

필수:

- timestamp
- symbol
- cycle_id
- signal_id
- strategy_version
- config_version
- code_version
- score
- state_before
- state_after
- action
- reason_code
- broker_order_id
- client_order_id
- cycle_exposure_cap
- current_cost_basis

---

## 32. 사유코드(Reason Code)

예:

```text
ENTRY_SCORE_PASS
REGIME_RED_BLOCK
REVERSAL_GATE_FAIL
CHASE_LIMIT_EXCEEDED
STAGE_TRIGGER_RECOVERED
EXPOSURE_LIMIT
DUPLICATE_SIGNAL
APPROVAL_EXPIRED
BROKER_DB_MISMATCH
TP_MISSING_RECOVERED
PARTIAL_FILL_ACCEPTED
REBUY_ALREADY_USED
```

사용자 화면에는 한글 설명을 표시하고 내부 로그에는 코드값을 저장한다.

---

## 33. 시간대

DB timestamp:

```text
UTC
```

사용자 화면:

```text
Asia/Seoul
```

시장 거래시간 계산:

```text
America/New_York
```

서버 OS 시간대에 의존하지 않는다.

---

## 34. 의존성 버전 고정

핵심 Python 라이브러리 버전을 고정한다.

특히:

- pandas
- numpy
- 지표 계산 라이브러리
- 거래소 캘린더
- Telegram library
- HTTP client

지표 라이브러리 버전 변경 후 Golden Test를 다시 수행한다.

---

## 35. Git 원칙

전략 변경과 코드 리팩터링은 별도 Commit 권장.

예:

```text
feat(strategy): clarify JDSS cycle exposure cap
fix(order): enforce stage execution price ceiling
test(tp): add partial TP fill recovery
refactor(core): separate eligibility from scoring
```

---

## 36. 절대 금지

- 승인 없는 매수
- 중복 주문
- 종목 최대자금 초과
- Cycle Exposure Cap 초과
- State 확인 없는 주문
- 설정값 하드코딩
- 백테스트용 별도 전략
- Broker 오류 무시
- Timeout 직후 무조건 주문 재전송
- DB만 믿고 주문
- Signal만 보고 HOLDING 변경
- 부분체결 무시
- 가격조건이 회복됐는데 추가매수
- Raw Limit Price가 전략상 가격상한을 넘는 주문
- 시간외장 시장가 매수
- 수동 DB State 직접수정

---

## 37. 개발 완료 기준

모두 통과:

- Unit Test
- Boundary Test
- Golden Test
- Parity Test
- Dry Run
- Restart Recovery
- Partial Fill
- TP Partial Fill
- Duplicate Order
- Approval Guard
- Approval TTL
- Stage Price Revalidation
- Entry Chase
- TP Recovery
- Rebuy TP Reset
- SAFE_MODE

본 문서는 JDSS Developer Guide v1.1.2의 공식 기준이다.
