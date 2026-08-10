# JDSS 구현 명세서(Implementation Specification)

> **레거시 v1.1.2 기준선 문서.** 현재 JDSS 2.2 실행·SGOV 규칙은
> [`../../JDSS_FINAL_SPEC.md`](../../JDSS_FINAL_SPEC.md), Telegram 동작은
> [`../../TELEGRAM_BOT_GUIDE.md`](../../TELEGRAM_BOT_GUIDE.md)를 우선한다.

**v1.1.2 — 공식 기준선(Official Baseline)**

---

## 1. 목적

기존 Telegram 기반 반자동 매매 시스템의 전략 엔진을 JDSS로 교체한다.

재사용 대상:

- Telegram Bot(텔레그램 사용자 인터페이스)
- Toss Securities API(토스증권 연동 API)
- Oracle Cloud(서버)
- SQLite(로컬 데이터베이스)
- 자동 익절
- 미체결 주문 감시
- Dashboard(대시보드)
- GitHub 배포 구조

핵심 목표:

> **전략 판단과 실제 주문 실행을 완전히 분리한다.**

---

## 2. 주요 영문 용어

| 영문 | 한글 설명 |
|---|---|
| Pure Strategy Core | 외부 API나 DB를 건드리지 않고 계산만 수행하는 순수 전략 영역 |
| Eligibility Gate | 매수가 가능한지 확인하는 필수 통과 조건 |
| Execution | 실제 주문 실행 |
| Reconciliation | 증권사 실제 상태와 DB 상태가 맞는지 확인하는 정합성 점검 |
| State Machine | 현재 매매 단계를 명확히 관리하는 상태 전이 구조 |
| Idempotency | 같은 요청이 여러 번 들어와도 주문은 한 번만 실행되게 하는 특성 |
| Partial Fill | 주문한 수량 중 일부만 체결된 상태 |
| Safe Mode | 상태 불일치 시 신규 매수를 차단하는 안전모드 |

---

## 3. 전체 아키텍처

```text
시장 데이터(Market Data)
    ↓
지표 계산(Indicator Engine)
    ↓
시장 국면 판단(Market Regime Engine)
    ↓
점수 계산(Score Engine)
    ↓
필수조건 검증(Eligibility Gate)
    ↓
전략 판단(Strategy Engine)
    ↓
매매 의사결정(Trade Decision)
    ↓
텔레그램 승인(Telegram Approval)
    ↓
최종 실행조건 재검증(Execution Validation)
    ↓
주문 관리자(Order Manager)
    ↓
Toss API
    ↓
포지션 / 상태관리
    ↓
자동 익절 관리
    ↓
상태 정합성 점검(Reconciliation)
```

---

## 4. 계층 분리

### 4.1 순수 전략 영역(Pure Strategy Core)

외부 API/DB 호출 금지.

```text
core/
  models.py
  enums.py
  indicator_engine.py
  market_regime.py
  score_engine.py
  eligibility.py
  strategy_engine.py
  risk_rules.py
  take_profit.py
```

### 4.2 업무 처리 영역(Application Layer)

```text
application/
  signal_engine.py
  approval_manager.py
  order_manager.py
  position_manager.py
  state_manager.py
  tp_manager.py
  reconciliation.py
```

### 4.3 외부 연동 영역(Infrastructure Layer)

```text
infrastructure/
  toss_client.py
  data_loader.py
  logger.py
  repositories/
  telegram/
```

### 4.4 백테스트·연구 영역(Test / Research)

```text
backtest/
  backtest_engine.py
  fill_model.py
  performance.py
  experiment_runner.py
```

---

## 5. 핵심 모듈 역할

### IndicatorEngine(지표 계산기)

입력:

- OHLCV 일봉 데이터

출력:

- `IndicatorSnapshot`

주문, DB, Telegram 접근 금지.

### MarketRegimeEngine(시장 국면 계산기)

SPY/QQQ 데이터를 받아:

- GREEN
- YELLOW
- RED

중 하나를 반환한다.

### ScoreEngine(점수 계산기)

시장 국면과 대상 ETF 지표를 받아:

- 총점
- 세부 점수
- 등급

을 반환한다.

### EligibilityGate(필수 통과 조건 검사기)

다음만 판단한다.

- 데이터 정상 여부
- 시스템 정상 여부
- 시장 국면 허용 여부
- 현재 상태 허용 여부
- 자금 한도 허용 여부
- 반등 최소점수 충족 여부

점수를 직접 계산하지 않는다.

### StrategyEngine(전략 판단기)

반환값 예:

- `NO_ACTION` : 행동 없음
- `FIRST_ENTRY_CANDIDATE` : 1차 매수 후보
- `ADD_ENTRY_CANDIDATE` : 추가매수 후보
- `REBUY_CANDIDATE` : 재매수 후보
- `RISK_REVIEW` : 장기보유 위험검토

실제 주문은 절대 실행하지 않는다.

### OrderManager(주문 관리자)

실제 Toss 주문을 수행할 수 있는 유일한 계층.

### Reconciliation(정합성 점검)

다음을 비교한다.

- 증권사 실제 잔고
- 증권사 실제 체결
- 증권사 미체결 주문
- SQLite 상태

불확실하면 `SAFE_MODE`.

---

## 6. 핵심 데이터 모델

권장:

```python
@dataclass(frozen=True)
class IndicatorSnapshot:
    symbol: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    cci5: float
    cci10: float
    rsi5: float
    rsi14: float
    ema5: float
    ema20: float
    ema60: float
    bb_lower: float
    atr14: float
    atr_pct: float
    volume_ratio: float
    close_position: float
```

```python
@dataclass(frozen=True)
class ScoreResult:
    total: int
    grade: str
    regime_score: int
    oversold_score: int
    reversal_score: int
    volume_score: int
    atr_score: int
```

```python
@dataclass(frozen=True)
class TradeDecision:
    action: DecisionType
    allowed: bool
    reason_codes: tuple[str, ...]
    target_stage: int | None
    cycle_exposure_cap: Decimal
    target_cumulative_capital: Decimal
    planned_budget: Decimal
```

---

## 7. 상태 머신(State Machine)

```text
EMPTY

WAITING_1ST_APPROVAL
WAITING_1ST_FILL
HOLDING_1ST

WAITING_2ND_APPROVAL
WAITING_2ND_FILL
HOLDING_2ND

WAITING_3RD_APPROVAL
WAITING_3RD_FILL
HOLDING_3RD

WAITING_4TH_APPROVAL
WAITING_4TH_FILL
HOLDING_4TH

PARTIAL_TP_1

WAITING_REBUY_APPROVAL
WAITING_REBUY_FILL
HOLDING_REBUY

COOLDOWN
SAFE_MODE
```

### COOLDOWN(재진입 대기)

정상 TP2 종료 후에는 바로 `EMPTY`로 간다.

`COOLDOWN`은 다음과 같은 수동·예외 종료 후에만 선택적으로 사용한다.

- `/panic`
- 관리자 수동청산
- 비정상 상태 복구 직후

기본적으로 다음 완결 일봉이 생성될 때까지 신규 진입을 차단한다.

---

## 8. 상태 변경 원칙

금지:

```text
Signal → HOLDING
Approval → HOLDING
Order Submitted → HOLDING
```

정상:

```text
신호
→ 사용자 승인
→ 주문 접수
→ 실제 체결 확인
→ 포지션 재계산
→ HOLDING
```

---

## 9. 사이클 투자한도 계산

### 9.1 최초 한도

1차 매수 점수에 따라:

```text
82~87 → $6,000
88~91 → $8,000
92~100 → $10,000
```

### 9.2 추가매수 시 확대

유효한 추가매수 신호가 발생하면:

```text
new_cap = score_to_exposure(current_score)

cycle_exposure_cap =
max(previous_cycle_exposure_cap, new_cap)
```

한도는 사이클 중 감소하지 않는다.

### 9.3 단계별 신규 예산

누적비중:

```text
1차 15%
2차 35%
3차 60%
4차 100%
```

```text
target_cumulative_capital =
cycle_exposure_cap × cumulative_weight[target_stage]

planned_budget =
max(0, target_cumulative_capital - staged_entry_capital)
```

`staged_entry_capital`은 1~4차 매수에 실제 사용된 누적 자금이며 재매수 금액은 별도 관리한다.

---

## 10. 일일 실행 일정

미국 정규장 종료 후 실행.

거래소 캘린더(Exchange Calendar, 실제 거래일·휴장·DST를 반영하는 달력)를 사용한다.

```text
1. 거래일 여부 확인
2. 데이터 공급상태 확인
3. SPY / QQQ 수집
4. TQQQ / SOXL 수집
5. 일봉 완결 확인
6. 지표 계산
7. 시장 국면 계산
8. 점수 계산
9. 증권사/DB 상태 확인
10. 필수 통과 조건 검증
11. 전략 판단
12. 신호 DB 저장
13. Telegram 알림
```

동일 키:

```text
symbol
+ trade_date
+ strategy_version
+ config_version
+ action
```

에 대해 중복 신호를 만들지 않는다.

---

## 11. 데이터 완결성

신규 매수 금지:

- 당일 일봉 미완결
- OHLC 중 Null
- High < Low
- Close <= 0
- Volume < 0
- 필요한 과거 데이터 부족
- SPY/QQQ/대상 ETF의 거래일 불일치
- 20일 평균거래량 계산 불가
- ATR/EMA/RSI 등 핵심 지표 계산 불가

`High == Low`이면 `ClosePosition = 0.0`.

---

## 12. 신호 유효기간

일봉 신호는 원칙적으로:

> **다음 미국 정규 거래일 종료 전까지**

유효하다.

새로운 완결 일봉 신호가 생성되면 이전 미처리 신호는 만료한다.

---

## 13. 매수 가능 시간

JDSS 전략 신호는 미국 **정규장 종가**를 기준으로 확정한다.

실제 매수 승인·주문은 다음 세션(Session, 거래 시간대)에서 허용한다.

- 같은 거래일 애프터마켓(After-Hours)
- 다음 거래일 프리마켓(Pre-Market)

기본 설정:

```yaml
trading_sessions:
  regular: false
  after_hours: true
  pre_market: true
```

시간외장에서는 다음을 강제한다.

- 시장가 매수 금지
- 지정가 주문만 사용
- 실시간 현재가 재조회
- Execution Price Ceiling(전략상 매수 허용가격 상한) 적용
- 미체결 시 가격을 자동으로 올리는 재주문 금지

JDSS Score와 지표는 시간외 가격으로 다시 계산하지 않는다.
항상 마지막 완결 정규장 일봉을 사용한다.

---

## 14. 2단계 승인

```text
신호 발생
↓
[매수 검토]
↓
신호/상태 유효성 검사
↓
실시간 현재가 조회
↓
가격조건 재검증
↓
잔고/미체결 주문 재조회
↓
사이클 투자한도 재검증
↓
수량 계산
↓
최종 주문 화면
↓
[최종 매수 실행]
↓
가격·상태·승인토큰 재검증
↓
주문
```

---

## 15. 승인 유효시간

### 매수 검토 토큰(Review Token)

```yaml
review_token_ttl_minutes: 30
```

### 최종 실행 토큰(Execution Token)

```yaml
execution_token_ttl_seconds: 60
```

유효시간이 지나면:

- 현재가 재조회
- 가격조건 재검증
- 수량 재계산
- 새로운 최종 확인 필요

---

## 16. 최종 가격조건

### 16.1 추격매수 상한

```text
max_chase_price =
signal_close × (1 + entry_max_chase_pct)
```

### 16.2 단계별 실행 상한

1차:

```text
execution_price_ceiling = max_chase_price
```

2~4차:

```text
execution_price_ceiling =
min(max_chase_price, stage_trigger_price)
```

재매수:

```text
execution_price_ceiling =
min(max_chase_price, avg_price × 0.98)
```

### 16.3 지정가

```text
raw_limit_price =
current_price × (1 + buy_limit_buffer)

limit_price =
min(raw_limit_price, execution_price_ceiling)
```

호가단위 반올림 후 가격이 상한을 초과하면 아래 호가로 내린다.

---

## 17. 주문 수량

```text
qty = floor(
    planned_budget /
    (limit_price × (1 + buy_fee))
)
```

반드시 확인:

- qty >= 1
- 계획예산 초과 금지
- 단계 누적목표 초과 금지
- `cycle_exposure_cap` 초과 금지
- 종목 최대자금 초과 금지
- 실제 매수가능금액 충분
- 동일 단계 주문 없음

---

## 18. 추가매수 재검증

신호 생성은 전일 종가 기준이지만 실제 주문 시 다음을 다시 확인한다.

예: 3차

```text
Signal 단계:
Close <= Anchor × 0.95
Score >= 86
Regime != RED
ReversalScore >= 5
```

실행 단계:

```text
CurrentPrice <= Anchor × 0.95
CurrentPrice <= MaxChasePrice
State == HOLDING_2ND
Open additional-entry order 없음
Exposure 정상
```

점수와 지표는 장중 재계산하지 않는다.

JDSS는 일봉 전략이므로 현재 거래일의 미완성 지표를 섞지 않는다.

---

## 19. 매수 주문 부분체결(Partial Fill)

TQQQ/SOXL은 유동성이 높지만 부분체결 상황은 처리해야 한다.

기본:

```yaml
buy_fill_timeout_seconds: 120
```

동작:

1. 주문 접수
2. 1분 단위보다 짧은 주문상태 조회는 Toss API 제한에 맞게 구현
3. 120초 내 전량체결되면 정상 처리
4. 시간이 지나도 미체결 잔량이 있으면 잔량 취소
5. 실제 체결수량이 1주 이상이면 해당 단계 완료로 인정
6. 미체결 잔량 자동 재주문 금지
7. 실제 체결분만 평단·자금·TP에 반영
8. 0주 체결이면 이전 보유상태로 복귀

---

## 20. 자동 익절(TP) 생성

매수 단계가 확정되면:

```text
1. 기존 TP 주문 취소
2. 실제 잔고 재조회
3. 실제 평균단가 확인
4. 가장 최근 완결 일봉 ATR 확인
5. TP1 / TP2 계산
6. TP 수량 계산
7. 지정가 매도주문 생성
8. 주문번호 저장
9. 상태 정합성 점검
```

---

## 21. TP 계획(TP Plan)

TP 주문을 단순히 주문번호 두 개로만 관리하지 않는다.

저장 권장:

```text
tp_plan_id
tp1_target_qty
tp1_filled_qty
tp1_price
tp2_target_qty
tp2_filled_qty
tp2_price
```

### TP1 부분체결

TP1 일부만 체결된 경우:

- `PARTIAL_TP_1` 상태로 즉시 전환하지 않는다.
- 목표 TP1 수량이 모두 체결될 때까지 기존 보유상태 유지
- TP1 주문이 취소/실패하면 미체결 TP1 수량만 복구
- 실제 체결수량은 DB에 누적 기록

TP1 목표수량 전체 체결 시:

```text
State = PARTIAL_TP_1
```

---

## 22. 재매수(Rebuy) 후 처리

재매수 체결 후:

```text
1. 기존 남은 TP2 취소
2. 실제 잔고 재조회
3. 새 평단 계산
4. rebuy_count = 1
5. 새 TP Plan 생성
6. 전체 현재 수량을 새 TP1/TP2로 50:50 분할
7. State = HOLDING_REBUY
```

이후 새 TP1이 체결되어도 재매수는 다시 시작하지 않는다.

```text
rebuy_count >= 1
→ 추가 Rebuy 금지
```

---

## 23. TP 주문 감시

기본:

```yaml
order_monitor_interval_seconds: 60
```

확인:

- 실제 보유수량
- TP1 미체결수량
- TP2 미체결수량
- DB 기대수량
- Broker 미체결수량

TP 누락 시 실제 잔고를 다시 확인한 뒤 복구한다.

---

## 24. DB 주요 테이블

### positions

```text
symbol
state
cycle_id
qty
avg_price
current_cost_basis
cycle_exposure_cap
staged_entry_capital
cash_remaining
entry_count
anchor_price
last_entry_price
last_entry_date
rebuy_count
rebuy_recovery_armed
risk_review_level
tp_plan_id
version
updated_at
```

### signals

```text
signal_id
cycle_id
symbol
trade_date
score
grade
regime
score_detail_json
action
target_stage
signal_close
stage_trigger_price
max_chase_price
valid_until
approved
processed
expired_reason
strategy_version
config_version
code_version
created_at
updated_at
```

### approvals

```text
approval_id
signal_id
approval_token_hash
approval_stage
status
expires_at
created_at
used_at
```

### orders

```text
broker_order_id
client_order_id
signal_id
cycle_id
symbol
side
order_type
price
qty
filled_qty
status
purpose
created_at
updated_at
```

`purpose`:

```text
ENTRY_1
ENTRY_2
ENTRY_3
ENTRY_4
REBUY
TP1
TP2
PANIC
```

### trades

```text
cycle_id
symbol
side
price
qty
fee
realized_pnl
fill_time
broker_order_id
```

### tp_plans

```text
tp_plan_id
cycle_id
symbol
source_event
avg_price
atr_pct
tp1_price
tp1_target_qty
tp1_filled_qty
tp2_price
tp2_target_qty
tp2_filled_qty
active
created_at
updated_at
```

### state_history

```text
cycle_id
symbol
previous_state
new_state
reason_code
created_at
```

---

## 25. 서버 시작 정합성 점검(Reconciliation)

시작 시:

```text
1. Toss 실제 잔고
2. Toss 실제 체결내역
3. Toss 미체결 주문
4. SQLite Position
5. SQLite Orders
6. SQLite State
7. TP Plan
```

비교.

신뢰 우선순위:

1. 증권사 실제 잔고
2. 증권사 실제 체결내역
3. 증권사 미체결 주문
4. 로컬 DB

불확실하면 `SAFE_MODE`.

---

## 26. SAFE_MODE(안전모드)

금지:

- 신규 1차 매수
- 추가매수
- 재매수

허용 가능:

- 상태 조회
- 오류 조회
- 실제 잔고 확인
- 안전성이 확인된 기존 TP 복구
- 관리자 수동조치

---

## 27. strategy.yaml

```yaml
version: JDSS-1.1.2

global:
  capital_per_symbol: 10000
  buy_fee: 0.001
  sell_fee: 0.001
  approval_required: true
  stop_loss_enabled: false

  entry_score: 82
  minimum_reversal_score: 5

  entry_max_chase_pct: 0.02
  buy_limit_buffer: 0.005

  trading_sessions:
    regular: false
    after_hours: true
    pre_market: true
  review_token_ttl_minutes: 30
  execution_token_ttl_seconds: 60
  buy_fill_timeout_seconds: 120

symbols:
  TQQQ:
    enabled: true
  SOXL:
    enabled: true

position:
  stage_weights: [0.15, 0.20, 0.25, 0.40]
  cumulative_weights: [0.15, 0.35, 0.60, 1.00]

exposure:
  score_82_87: 0.60
  score_88_91: 0.80
  score_92_100: 1.00
  allow_cap_increase: true
  allow_cap_decrease_during_cycle: false

additional_entry:
  anchor: first_entry_fill_price
  max_stage_per_day: 1

  stage2:
    min_drop_from_anchor: 0.03
    min_score: 84

  stage3:
    min_drop_from_anchor: 0.05
    min_score: 86

  stage4:
    min_drop_from_anchor: 0.08
    min_score: 88

take_profit:
  tp1_base: 0.04
  tp2_base: 0.08
  use_atr: true
  tp1_atr_multiplier: 0.8
  tp2_atr_multiplier: 1.6

rebuy:
  enabled: true
  minimum_score: 82
  minimum_reversal_score: 5
  min_drop_from_avg: 0.02
  max_rebuy_per_cycle: 1

  recovery:
    cci5_gt: -100
    rsi5_gte: 35
    close_above_ema5: true
    mode: any

  reoversold:
    cci5_lte: -150
    rsi5_lte: 25
    close_below_lower_band: true
    mode: any

risk_review:
  info_days: 10
  review_days: 20
  high_days: 40

scheduler:
  order_monitor_interval_seconds: 60
```

---

## 28. 실거래·백테스트 공용 함수

반드시 공유:

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
calculate_order_qty()
calculate_take_profit()
evaluate_risk_review()
```

---

## 29. 구현 우선순위

1. `strategy.yaml`
2. 데이터 모델 / Enum
3. 지표 계산
4. 시장 국면
5. 점수 계산
6. 필수 통과 조건
7. 사이클 투자한도 계산
8. 전략 판단
9. State Manager
10. Position Manager
11. Backtest Engine
12. 체결모델(Fill Model)
13. Order Manager
14. Approval Manager
15. TP Manager
16. Telegram UI
17. Reconciliation
18. Dry Run(모의실행)
19. 실거래

---

## 30. 구현 완료 조건

필수 통과:

- 단위 테스트(Unit Test)
- 경계값 테스트(Boundary Test)
- 백테스트/실거래 로직 일치(Parity Test)
- 모의실행(Dry Run)
- 서버 재시작 복구
- 부분체결
- 중복주문
- 승인 만료
- 가격조건 재검증
- 추격매수 방지
- TP 복구
- 재매수 후 TP 재생성
- SAFE_MODE

본 문서는 JDSS v1.1.2 구현의 공식 기준이다.
