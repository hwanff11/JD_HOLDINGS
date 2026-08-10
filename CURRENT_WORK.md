# JD_HOLDINGS Current Work

> 집의 Codex, 외부의 ChatGPT, IDE의 Antigravity가 작업을 이어받기 위한 공용 인수인계 파일이다. GitHub 원격 저장소를 Source of Truth로 사용한다.

## 현재 작업 구조

- 운영 안정 기준선: `main`
- 현재 준비 브랜치: `codex/final-deploy-readiness`
- FINAL 기능 PR #4: `main` 병합 완료 (`b871413`)
- Oracle dry-run 워크플로 원본: `ops/oracle-dry-run-deploy`
- 연구 기록: `research/jdss-v2-swing-optimization` (운영 병합 대상 아님)

## 현재 전략 버전

`JDSS-2.1.0-FINAL` / config `2.1.0`

기준 문서: `docs/JDSS_FINAL_SPEC.md`

## FINAL 전략 계약

- TQQQ, SOXL / 종목당 전략자금 $10,000
- 모든 매수 단계 Score 55 이상, Reversal Score 5 이상, `Regime != RED`
- 매수 비중 40% / 30% / 20% / 10%
- 최초 실제 체결가 대비 추가매수 -2% / -5% / -7%, 하루 최대 한 단계
- TP1 평단 +4% 약 50%, TP2 평단 +6% 잔량
- TP1 완전체결 후 20개 완결 거래일 경과 시 미체결 잔량을 평단 +2% `REMAINDER_EXIT`로 전환
- SOXL 섹터 가드: SOXX/SMH EMA60 기준 1·3·4차
- 자동손절·재매수 없음, 모든 매수는 2단계 사용자 승인 필수

## 완료 상태

- FINAL 코드·설정·명세와 운영 E2E Dry Run이 `main`에 병합됐다.
- TP2 자동복구, TP1 완료시점 영속화, 20거래일 판정, 잔여청산, 재시작 Reconciliation 및 SAFE_MODE 경로가 구현됐다.
- GitHub Actions FINAL Dry Run 게이트가 추가됐고 병합 전 CI를 통과했다.
- Oracle 배포용 수동 워크플로는 `main` 계보 확인, Ruff·pytest·설정 검증, 강제 `dry_run`, 실주문 확인값 제거, Toss 조회 전용 smoke를 수행한다.
- README, 전략·백테스트·Telegram·배포·개발 워크플로 문서를 FINAL 기준으로 정합화했다.
- 로컬 검증: pytest 72개 통과, Ruff 통과, `jdss validate-config` 통과, FINAL E2E Dry Run 통과.

## 배포 전 남은 게이트

1. 검증된 변경을 commit/push하고 PR 또는 안전한 fast-forward 방식으로 `main`에 반영한다.
2. GitHub `oracle-dry-run` Environment의 SSH secret과 서버 variable을 확인한다.
3. 사용자의 배포 지시에 따라 `Deploy Oracle Dry Run`을 실행한다.
4. 서버에서 `toss-smoke`, 서비스 상태, 브로커 잔고·미체결 주문·SQLite 상태를 Reconciliation한다.
5. 이상이 있으면 신규매수를 중지하고 `dry_run`을 유지한다.

## 실거래 잠금

배포 준비 및 최초 Oracle 반영은 `JDSS_TRADING_MODE=dry_run`, 빈 `JDSS_LIVE_CONFIRMATION`을 유지한다. 실거래 활성화와 리스크 한도 변경은 별도 사용자 승인과 운영 점검 없이는 수행하지 않는다.

## 마지막 인수인계

- 작성 주체: Codex
- 상태: JDSS 2.1 FINAL의 `main` 병합을 확인하고 문서·Oracle 강제 dry-run 배포 워크플로를 배포 직전 기준으로 정합화 중. 실제 Oracle 배포는 미실행.
- 다음 우선순위: 로컬 전체 검증 → 준비 브랜치 commit/push → `main` 반영 → 사용자 승인 후 Oracle dry-run 배포

## 갱신 규칙

작업 종료 시 현재 브랜치, 마지막 커밋, 검증 결과, 완료 작업, 다음 작업을 갱신한다. 다음 작업자가 즉시 이어갈 수 있는 상태만 간결하게 유지한다.
