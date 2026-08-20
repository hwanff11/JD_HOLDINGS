# JDSS 전략 연구 검증 프로토콜

## 목적

전략 연구가 높은 백테스트 수치만 만들고 실제 production 설정과 다른 경로를 타는 일을 막는다. 모든 연구는 `main`의 production 엔진을 기준선으로 사용하고, 연구용 override가 실제로 적용됐음을 결과 계산 전에 증명한다.

## 기본 원칙

1. `main`의 production 전략은 연구 브랜치에서 직접 수정하지 않는다.
2. 한 번에 가능한 한 하나의 구조적 변수만 변경한다.
3. 후보와 baseline은 같은 데이터, 수수료, 슬리피지, 체결 엔진, 자금계약을 사용한다.
4. train / validation에서 후보를 고른 뒤 OOS를 연다. OOS를 보고 고른 후보는 `shadow/paper candidate`로만 분류한다.
5. Total Return/CAGR뿐 아니라 MDD, Sharpe/Sortino, 평균노출, 거래수, 단계 도달수, 연도·반기 민감도를 함께 본다.
6. 명확한 개선이 없으면 production을 변경하지 않는다.

## Research invariant gate

연구가 parameter/config override를 사용하면 계산 전에 다음을 반드시 검증한다.

### 1. Override 적용 확인

`StageThresholds.from_config()` 등 실제 dataclass/config 객체를 다시 읽어 요청한 값이 들어갔는지 assertion한다.

예: S1/S2/S3를 `55/60/50`으로 연구한다면 실제 config가 정확히 `55/60/50`인지 먼저 확인한다.

### 2. Signal contract 확인

연구 결과에 기록된 각 staged-entry signal은 해당 단계의 실제 score floor를 만족해야 한다.

- S1 signal score >= S1 floor
- S2 signal score >= S2 floor
- S3 signal score >= S3 floor

하나라도 위반하면 그 연구 run 전체를 무효 처리한다.

### 3. Binding-change sanity check

baseline에 후보의 더 엄격한 score floor가 차단해야 하는 신호가 실제 존재하는데 candidate의 signal/trade 결과가 baseline과 완전히 동일하면 연구 harness 오류로 간주하고 FAIL한다.

예: baseline S2=55에서 score 59 S2 신호가 있었고 candidate S2=60인데 결과가 완전히 같다면 정상 연구 결과가 아니다.

### 4. Production parity

후보별 backtest는 가능한 한 `StrategyBacktestEngine`과 `PortfolioBacktestEngine`을 직접 사용한다. 연구 스크립트가 production 주문·체결·자금회계를 재구현하지 않는다.

### 5. Baseline reproduction

연구를 시작하기 전에 연구 harness가 현행 production baseline의 핵심 결과를 재현하는지 확인한다. baseline 자체가 production reference와 다르면 후보 비교를 중단한다.

## 기간 분리

기본 장기 연구 기준:

- train: 2011~2018
- validation: 2019~2022
- OOS: 2023~현재 완료 거래일
- recent stress: 2022~현재 완료 거래일
- full: 2011~현재 완료 거래일

OOS를 본 뒤 선택한 후보는 더 이상 완전한 OOS 후보가 아니다. 새 시장 데이터가 쌓이는 동안 shadow/paper 비교 후 production 채택 여부를 결정한다.

## 비용·스트레스

기본 production 비교는 현재 계약의 수수료와 기본 슬리피지를 사용한다. 유망 후보는 최소한 기본 슬리피지 외에 완화/악화 시나리오를 추가해 방향성이 유지되는지 확인한다.

## GitHub 운영

- 일회성 연구 workflow는 연구 브랜치에만 둔다.
- 연구 완료 후 production에 필요 없는 workflow는 `main`에 병합하지 않는다.
- 공통 검증 규칙은 `src/jd_holdings/backtest/research_validation.py`와 CI 테스트에 둔다.
- 연구 PR은 결과와 결론을 기록한 뒤 미채택이면 병합 없이 닫는다.
- production 변경 후보만 별도의 정식 구현 PR로 만든다.

## 결과와 문서 수명주기

- 후보별 상세 JSON·Markdown·차트는 연구 PR과 Actions artifact에 보존하고 `main`의 새 보고서 문서로 복사하지 않는다.
- 미채택 후보는 [`../HISTORY.md`](../HISTORY.md)에 이름·기각 이유·근거 PR을 한 항목으로만 남긴다.
- 채택 후보는 별도 구현 PR에서 기존 `strategy.yaml`, 공식 사양, 전략 가이드와 한 장 보고서를 제자리 갱신한다. 버전명이 붙은 새 현행 문서를 만들지 않는다.
- 기준 백테스트의 승인된 인간용 결과는 `STRATEGY_GUIDE.md`, 최신 실행 ID와 상태는 `CURRENT_WORK.md`가 소유한다.
- 연구 중간값을 README·공식 사양·운영 가이드에 복제하지 않는다.

## 연구 결과 판정

### KEEP

현행 production이 더 안정적이거나 개선폭이 미미하다. 변경하지 않는다.

### SHADOW

위험조정 성과가 유망하지만 OOS 독립성이 훼손됐거나 표본이 부족하다. production은 유지하고 새 데이터에서 병렬 추적한다.

### ADOPT CANDIDATE

train/validation/OOS와 비용 스트레스에서 일관된 개선이 있고, production parity 및 invariant gate를 모두 통과했다. 사용자 승인 후 별도 구현 PR에서 정식 반영한다.
