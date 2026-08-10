# JD_HOLDINGS Current Work

> 이 파일은 집의 Codex, 외부의 ChatGPT, 그리고 IDE의 Antigravity(안티그라비티)가 작업을 이어받기 위한 공용 인수인계 상태 파일이다.
> `AGENTS.md`의 `작업 시작` / `작업 종료` 규칙과 함께 사용한다.
>
> 원칙: 작업자는 세션 시작 시 이 파일을 먼저 읽고, 세션 종료 시 필요한 항목을 최신 상태로 갱신한다.

## 현재 활성 개발 브랜치

`research/jdss-v2-swing-optimization`

## 기준 브랜치

`main`

- `main`: 현재 운영 안정 기준선. FINAL 검증이 끝날 때까지 수정/병합하지 않는다.
- `research/jdss-v2-swing-optimization`: JDSS 2.1 FINAL 사양 및 실거래 반영 검증 브랜치.
- Draft PR #3에서 검토 중이며 사용자 승인 전에는 `main`에 병합하지 않는다.

## 현재 전략 버전

`JDSS-2.1.0-FINAL` / config `2.1.0`

FINAL 기준문서: `docs/JDSS_FINAL_SPEC.md`

## FINAL 전략 계약

- 대상 종목: TQQQ, SOXL
- 종목당 전략 자금: $10,000
- 모든 매수 단계 최소 Score: 55
- 최소 Reversal Score: 5
- 비중: 40% / 30% / 20% / 10%
- 추가매수: 최초 실제 체결가 대비 -2% / -5% / -7%
- TP1: 평단 +4%, 약 50% 매도
- TP2: 평단 +6%, 잔량 매도
- SOXL 섹터 가드: 1차·3차·4차
- TP1 완료 후 20개의 완결 미국 거래일이 지나면 기존 TP2를 취소하고 평단 +2% 잔여청산 지정가 주문으로 전환
- 자동 손절 없음
- 재매수 없음
- 모든 매수는 2단계 사용자 승인 필수

## 마지막 완료 작업

- `strategy.yaml`을 `JDSS-2.1.0-FINAL`로 고정했다.
- `take_profit.remainder_exit` 설정을 정식 config 모델과 검증 규칙에 추가했다.
- TP1 완료 후 20거래일 경과 판정을 XNYS 거래소 캘린더 기준으로 구현했다.
- TP2를 평단 +2% `REMAINDER_EXIT` 주문으로 안전하게 교체하고 취소/거절 시 자동 복구하도록 실거래 주문 감시에 반영했다.
- TP1/TP2/잔여청산의 공통 가격 및 기한 판단을 `core/remainder_exit.py`로 분리하여 백테스트와 실거래가 같은 규칙을 사용하도록 했다.
- 잔여청산 완전체결을 기존 TP2 최종 leg로 회계·상태 반영하도록 했다.
- 재시작/reconciliation 상황에서 열린 `REMAINDER_EXIT` 주문이 정상 상태로 인정되는 테스트를 추가했다.
- 일반 CLI 백테스트와 FINAL 연구 백테스트가 `StrategyBacktestEngine`을 사용하도록 맞췄다.
- FINAL 계약, 추가매수 경계, SOXL 가드, TP 가격, TP1→20거래일→잔여청산, SAFE_MODE/주문복구를 단위테스트로 고정했다.
- GitHub Actions CI에서 Ruff, 전체 pytest, `jdss validate-config`가 통과했다.
- FINAL 회귀 백테스트에서 2021~2024 검증 성과를 재확인했다: CAGR 약 +12.41%, MDD 약 -19.30%, P95 MAE 약 -20.69%, 40일 초과 고착 약 7.69%, 최대 296거래일, 완료 사이클 65.
- SGOV 유휴현금 연구는 핵심 매매엔진과 분리했다. SGOV 상장 이후 유휴현금을 적용한 연구 결과는 장기 CAGR 약 9.69%, MDD 약 -24.68%였다.

## 알려진 연구 데이터 주의사항

- 전체기간 SOXL 2011년 시작 717거래일 사이클은 오래된 분할조정 가격 데이터의 정합성 영향이 있는 기록으로 전략 튜닝 근거에서 제외한다.
- 실제 전략상 확인된 주요 장기 고착 사례는 TQQQ 2022년 약 296거래일 사이클이다.
- 백테스트는 T일 신호→T+1 체결 가정이며 실제 승인형 프리/애프터장 주문과 체결시점이 완전히 동일하지 않다.

## 다음 작업

1. 최신 research 브랜치의 CI와 FINAL Research Backtest가 모두 green인지 다시 확인한다.
2. Draft PR #3 변경사항을 최종 검토한다.
3. 필요 시 텔레그램 사용자용 `/backtest` 경로도 FINAL `StrategyBacktestEngine`으로 통일한다. 현재 실거래 주문·포지션 관리 로직과 CLI/연구 백테스트는 FINAL 규칙을 사용한다.
4. 로컬/Oracle 배포 전 Dry Run으로 1차 승인→체결→TP 생성→TP1→20거래일 잔여청산 흐름을 재확인한다.
5. 사용자가 명시적으로 승인한 뒤에만 PR #3을 `main`에 병합한다.
6. 병합 후 Oracle Cloud 운영 서버에 배포하고 브로커 잔고/주문/SQLite reconciliation을 확인한다.

## 실행 환경 역할

### 집 (Codex + 로컬 PC)

- 본격 기능 개발, 디버깅, 장시간/대규모 백테스트
- 시작: `작업 시작` → status/fetch/pull 후 개발
- 종료: `작업 종료` → test/commit/push/CURRENT_WORK 갱신

### 외부 (ChatGPT + GitHub)

- 최신 GitHub 소스를 기준으로 전략 검토, 코드 수정, 테스트 추가, 연구 브랜치/PR 관리
- 가능한 자동 검증과 전략 비교는 GitHub Actions를 우선 사용
- 작업 결과는 원격 브랜치에 push하여 Codex가 그대로 이어받도록 한다.

### IDE (Antigravity)

- 로컬 IDE workspace + GitHub remote를 사용한 개발 및 실시간 코드 검증
- 환경 전환 전 commit/push, 시작 시 최신 원격 상태 동기화

### GitHub Actions

- pytest, Ruff, 설정 검증, 회귀 백테스트 등 반복 가능한 연구/검증 작업
- JSON/Markdown artifact를 남겨 결과를 재검토한다.

### Oracle Cloud

- Telegram Bot, 정규장 종료 후 분석, 승인/주문/포지션 감시 등 24시간 JDSS 운영 서비스
- 연구용 대규모 백테스트와 분리한다.
- 검증되어 `main`에 반영된 코드만 운영 배포 대상으로 삼는다.

## 마지막 인수인계

- 작성 주체: ChatGPT
- 상태: JDSS 2.1 FINAL 전략 채택 및 research 브랜치 실거래 로직 반영 완료. FINAL 회귀 백테스트와 CI green 확인 단계. 아직 `main` 미병합/운영 미배포.
- 활성 브랜치: `research/jdss-v2-swing-optimization`
- 기준 브랜치: `main`
- 기준문서: `docs/JDSS_FINAL_SPEC.md`
- 다음 우선순위: PR #3 최종 검토 → Dry Run → 사용자 병합 승인 → main/운영 배포

## 갱신 규칙

작업 종료 시 최소 다음 항목을 확인/갱신한다.

- 현재 활성 개발 브랜치
- 현재 전략 버전
- 현재 개발 목표(변경된 경우)
- 마지막 완료 작업
- 다음 작업
- 마지막 인수인계의 작성 주체/상태/커밋

장황한 작업일지로 만들지 않는다. 다음 작업자가 어디서 무엇을 이어서 해야 하는지 판단할 수 있을 정도로만 유지한다.
