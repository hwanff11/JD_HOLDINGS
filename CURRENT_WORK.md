# JD_HOLDINGS Current Work

> 집의 Codex, 외부의 ChatGPT, IDE의 Antigravity가 작업을 이어받기 위한 공용 인수인계 파일이다.
> 세션 시작 시 이 파일을 먼저 읽고, 종료 시 다음 작업자가 바로 이어갈 수 있게 갱신한다.

## 현재 작업 구조

- 운영 안정 기준선: `main`
- FINAL 운영 통합 브랜치: `feature/jdss-2.1-final`
- 운영 통합 Draft PR: **#4 `JDSS 2.1 FINAL: production trading integration`**
- 연구 기록 브랜치: `research/jdss-v2-swing-optimization`
- 연구 PR #3은 최적화 과정 보관용이며 운영 병합 대상이 아니다.

사용자 명시 승인 전에는 PR #4를 `main`에 병합하거나 운영 서버에 배포하지 않는다.

## 현재 전략 버전

`JDSS-2.1.0-FINAL` / config `2.1.0`

기준문서: `docs/JDSS_FINAL_SPEC.md`

## FINAL 전략 계약

- 대상: TQQQ, SOXL
- 종목당 전략자금: $10,000
- 모든 매수 단계 최소 Score: 55
- 최소 Reversal Score: 5
- 모든 신규·추가매수: `Regime != RED`
- 매수 비중: 40% / 30% / 20% / 10%
- 추가매수: 최초 실제 체결가 대비 -2% / -5% / -7%
- 하루 최대 한 단계 추가매수
- TP1: 평단 +4%, 약 50% 매도
- TP2: 평단 +6%, 잔량 매도
- SOXL 섹터 가드: 1차·3차·4차, SOXX/SMH EMA60 기준
- TP1 완전체결 후 20개 완결 미국 거래일 경과 시 미체결 잔량을 평단 +2% `REMAINDER_EXIT`로 전환
- 자동 손절 없음
- 재매수 없음
- 모든 매수는 2단계 사용자 승인 필수

## 실거래 반영 완료 내용

- TP1 완료시점을 사이클별 `system_state`에 영속 저장한다.
- TP2 취소·거절·자동복구·재발행이 발생해도 20거래일 시계를 리셋하지 않는다.
- XNYS 거래소 캘린더로 완결 거래일을 계산한다.
- TP2 취소 과정에서 추가 체결이 발생하면 체결수량을 먼저 반영하고 실제 남은 수량만 잔여청산한다.
- `REMAINDER_EXIT` 취소/거절 시 실제 포지션을 재확인해 자동 복구한다.
- 잔여청산 완전체결은 기존 TP2 최종 leg로 회계·상태 반영한다.
- 재시작/Reconciliation 시 열린 `REMAINDER_EXIT`도 정상 전략 주문으로 인식한다.
- FINAL에서는 `rebuy.enabled=false`이므로 재매수 recovery 상태를 arm하지 않는다.
- SOXL 섹터 벤치마크 조회 실패/완결일 누락은 경고 이벤트를 기록하고 `warn_and_allow` 정책을 따른다.
- 실거래와 운영 백테스트는 `remainder_exit_due()` / `remainder_exit_price()` 공용 함수를 사용한다.
- 기존 2단계 매수승인, 현재가 재조회, 수량 재계산, 가격상한, 주문 멱등성, SAFE_MODE는 유지한다.

## 검증 상태

운영 PR #4 최신 검증 기준:

- head: `987a1f98e2d26b3a42c6d0d571edbbea012f949f`
- GitHub Actions CI #161: 성공
- Ruff: 성공
- 전체 pytest + coverage: 성공
- `jdss validate-config`: 성공
- PR #4: open / draft / mergeable

연구 회귀검증 참고:

- 전체기간 FINAL CAGR 약 +8.73%, MDD 약 -24.68%
- 2021~2024 후보 검증 CAGR 약 +12.4%, MDD 약 -19.3%
- 실제 전략상 주요 최장 고착: TQQQ 2022년 약 296거래일
- SOXL 717거래일 과거 기록은 분할조정 데이터 정합성 영향으로 전략 튜닝 근거에서 제외
- SGOV 유휴현금 연구는 핵심 전략과 분리하며 이번 운영 PR에는 포함하지 않는다.

## 다음 작업

1. 실제/모의 브로커 환경에서 Dry Run으로 `승인 → 매수체결 → TP 생성 → TP1 → TP2 복구 → 20거래일 잔여청산 → 재시작 Reconciliation` 흐름을 확인한다.
2. Dry Run에서 주문 API 응답 형식, 부분체결, 취소 직전 체결, 서버 재시작을 확인한다.
3. 문제가 없으면 사용자에게 PR #4 병합 준비 완료 상태를 보고한다.
4. **사용자가 명시적으로 승인한 뒤에만** PR #4를 `main`에 병합한다.
5. 병합 후 Oracle Cloud에 배포하고 실제 브로커 잔고/미체결주문/SQLite Reconciliation을 확인한다.

## 작업 환경 원칙

- GitHub 원격 저장소를 Source of Truth로 사용한다.
- 기능/전략 변경은 작업 브랜치에서 수행하고 검증 후 PR로 main에 반영한다.
- GitHub Actions는 테스트·연구·회귀검증에 사용한다.
- Oracle Cloud는 Telegram Bot과 실거래 운영 서비스에 사용한다.
- 환경 전환 전 commit/push, 새 환경에서는 최신 원격 상태를 먼저 확인한다.
- 강제 push/reset/rebase 등 파괴적 작업은 사용자 명시 승인 없이 수행하지 않는다.

## 마지막 인수인계

- 작성 주체: ChatGPT
- 상태: JDSS 2.1 FINAL 사양과 실거래 핵심 로직을 운영 PR #4에 반영하고 CI 검증 완료. 아직 main 미병합/운영 미배포.
- 다음 우선순위: 브로커 Dry Run → 사용자 병합 승인 → main 병합 → Oracle 배포/Reconciliation

## 갱신 규칙

작업 종료 시 최소한 현재 브랜치/PR, 마지막 커밋, 검증 결과, 완료 작업, 다음 작업을 갱신한다. 장황한 작업일지가 아니라 다음 작업자가 즉시 이어갈 수 있는 상태 정보만 유지한다.
