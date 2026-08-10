# JD_HOLDINGS Current Work

> 집의 Codex, 외부의 ChatGPT, IDE의 Antigravity가 작업을 이어받기 위한 공용 인수인계 파일이다.
> 세션 시작 시 이 파일을 먼저 읽고, 종료 시 다음 작업자가 바로 이어갈 수 있게 갱신한다.

## 현재 작업 구조

- 운영 안정 기준선: `main`
- FINAL 운영 통합 브랜치: `feature/jdss-2.1-final`
- 운영 통합 PR: **#4 `JDSS 2.1 FINAL: production trading integration`**
- 연구 기록 브랜치: `research/jdss-v2-swing-optimization`
- 연구 PR #3은 최적화 과정 보관용이며 운영 병합 대상이 아니다.

현재 PR #4는 기술 검증을 완료한 **Ready for review / mergeable** 상태다. 사용자가 이번 단계에서 요청한 범위는 `main` 적용 직전까지이므로, 이번 작업에서는 `main` 병합과 Oracle 운영 배포를 수행하지 않는다.

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

## FINAL 운영 Dry Run

`tests/test_final_dry_run.py`와 `.github/workflows/final-dry-run.yml`을 운영 병합 게이트로 추가했다.

검증 흐름:

`2단계 승인 → 1차 매수체결 → TP1/TP2 생성 → TP1 완전체결 → TP2 취소/자동복구 → TP1 기준 20거래일 유지 → REMAINDER_EXIT 전환 → 서비스 재시작 → Reconciliation → 잔여청산 완전체결 → EMPTY 복귀`

Dry Run은 실제 외부 주문을 전송하지 않는 `DryRunBroker`를 사용하지만, 운영 `TradingService`, `OrderMonitor`, `PositionManager`, `TakeProfitManager`, SQLite 상태/주문 저장소, Reconciliation 경로를 그대로 사용한다.

## 검증 상태

최신 코드/계보 동기화 head `271fc28118163b04034120a9efc80e43f8961d08` 기준:

- GitHub Actions CI #169: **SUCCESS**
- GitHub Actions `FINAL Dry Run` #3: **SUCCESS**
- Ruff: 성공
- 전체 pytest + coverage: 성공
- `jdss validate-config`: 성공
- FINAL E2E Dry Run: 성공
- PR #4: open / ready for review / mergeable
- 미해결 PR 리뷰 스레드: 0건
- 최신 `main` 대비: ahead 7 / behind 0

연구 회귀검증 참고:

- 전체기간 FINAL CAGR 약 +8.73%, MDD 약 -24.68%
- 2021~2024 후보 검증 CAGR 약 +12.4%, MDD 약 -19.3%
- 실제 전략상 주요 최장 고착: TQQQ 2022년 약 296거래일
- SOXL 717거래일 과거 기록은 분할조정 데이터 정합성 영향으로 전략 튜닝 근거에서 제외
- SGOV 유휴현금 연구는 핵심 전략과 분리하며 이번 운영 PR에는 포함하지 않는다.

## 다음 작업

1. 현재 상태는 `main` 병합 직전 게이트까지 완료된 상태다.
2. 사용자가 `main 병합` 또는 이에 준하는 명시 지시를 하면 PR #4의 최신 head와 CI를 다시 확인한 뒤 병합한다.
3. 병합 후 Oracle Cloud 운영 서버에 배포한다.
4. 운영 서버에서 `toss-smoke`로 주문 없이 인증·현재가·장상태를 확인한다.
5. 실제 브로커 잔고, 미체결 주문, SQLite 상태를 Reconciliation하고 이상이 있으면 신규매수를 중지한다.
6. 정상 확인 후 JDSS 2.1 FINAL 운영을 시작한다.

## 작업 환경 원칙

- GitHub 원격 저장소를 Source of Truth로 사용한다.
- 기능/전략 변경은 작업 브랜치에서 수행하고 검증 후 PR로 main에 반영한다.
- GitHub Actions는 테스트·연구·회귀검증에 사용한다.
- Oracle Cloud는 Telegram Bot과 실거래 운영 서비스에 사용한다.
- 환경 전환 전 commit/push, 새 환경에서는 최신 원격 상태를 먼저 확인한다.
- 강제 push/reset/rebase 등 파괴적 작업은 사용자 명시 승인 없이 수행하지 않는다.

## 마지막 인수인계

- 작성 주체: ChatGPT
- 상태: JDSS 2.1 FINAL 운영 PR #4의 코드·설정·문서·전체 CI·모의 브로커 E2E Dry Run·최신 main 동기화·최종 PR 리뷰 점검까지 완료. `main` 병합 직전에서 정지. Oracle 미배포.
- 다음 우선순위: 사용자 main 병합 지시 → PR #4 병합 → Oracle 배포 → `toss-smoke` → 브로커/DB Reconciliation

## 갱신 규칙

작업 종료 시 최소한 현재 브랜치/PR, 마지막 커밋, 검증 결과, 완료 작업, 다음 작업을 갱신한다. 장황한 작업일지가 아니라 다음 작업자가 즉시 이어갈 수 있는 상태 정보만 유지한다.
