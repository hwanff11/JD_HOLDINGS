# JD_HOLDINGS 문서 안내

이 디렉터리는 JDSS 2.2 SGOV의 개발·운영 문서와 과거 연구 기록을 함께 보관한다. 다른 환경의 Codex, ChatGPT, Antigravity는 아래 순서로 읽어야 한다.

## 작업 시작 순서

1. [`../CURRENT_WORK.md`](../CURRENT_WORK.md): 현재 브랜치, 배포 상태, 완료 작업, 다음 작업
2. [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md): 현재 전략의 공식 계약
3. [`../strategy.yaml`](../strategy.yaml): 코드가 실제로 읽는 수치 설정
4. 작업 종류에 맞는 운영 문서
5. [`infra/DEVELOPMENT_WORKFLOW.md`](infra/DEVELOPMENT_WORKFLOW.md): 브랜치·검증·인수인계 절차

GitHub `main`을 소스의 기준으로 사용한다. 작업 시작 전에 로컬 변경 여부를 확인하고 `main`을 최신화한 다음 별도 작업 브랜치를 만든다. 동일 브랜치를 여러 환경에서 동시에 수정하지 않는다.

## 문서별 역할

| 문서 | 역할 | 현재 기준 여부 |
|---|---|---|
| [`../CURRENT_WORK.md`](../CURRENT_WORK.md) | 가장 최근 인수인계와 Oracle 운영 상태 | 현재 상태의 최종 기준 |
| [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md) | JDSS-2.2.1-SGOV 전략·주문·자금관리 계약 | 현재 전략의 최종 기준 |
| [`TELEGRAM_BOT_GUIDE.md`](TELEGRAM_BOT_GUIDE.md) | Telegram 명령과 출력 해석 | 현재 UI 기준 |
| [`infra/DEPLOYMENT.md`](infra/DEPLOYMENT.md) | Oracle dry-run 배포·검증·롤백 | 현재 배포 기준 |
| [`infra/DEVELOPMENT_WORKFLOW.md`](infra/DEVELOPMENT_WORKFLOW.md) | Codex·ChatGPT·Antigravity 협업 절차 | 현재 개발 기준 |
| [`infra/DECISIONS.md`](infra/DECISIONS.md) | 구현 결정과 변경 이유 | 누적 기록, 최신 결정 우선 |
| [`STRATEGY_GUIDE.md`](STRATEGY_GUIDE.md) | 현재 전략 요약과 과거 전략 발전사 | 첫 부분만 현재, Archive는 과거 |
| [`BACKTEST_REPORT.md`](BACKTEST_REPORT.md) | 현재 회귀 요약과 과거 백테스트 기록 | 첫 부분만 현재, Archive는 과거 |
| [`archive/spec_v1/`](archive/spec_v1/) | v1 계열 명세 보존 | 현재 구현에 사용 금지 |

## 현재 운영 기준

- 개발 전략: `JDSS-2.2.1-SGOV`, 설정·패키지: `2.2.1`
- 운영 배포: 이전 `JDSS-2.1.0-FINAL`, Oracle `dry_run`
- 유휴자금: SGOV 자동 예치·선현금화 구현, 운영 배포 전 검증 중
- 운영 브랜치: `main`
- Oracle: `dry_run`, Telegram 백테스트 중심 운영
- JDSS 내부 TQQQ/SOXL 포지션: `qty=0`, `EMPTY`
- JDSS 미체결 주문: 0건
- 실거래 승격: 별도 사용자 승인과 Reconciliation 전까지 금지

정확한 배포 커밋과 최근 검증 결과는 변경될 수 있으므로 항상 [`../CURRENT_WORK.md`](../CURRENT_WORK.md)를 확인한다.

## 과거 문서 사용 규칙

`Archive`, `Legacy`, `v1.x`, `v2.0`으로 표시된 내용은 연구 재현과 의사결정 이력 보존용이다. 그 안의 50점 진입, -4% 추가매수, +8% TP2 같은 값은 현재 전략에 적용하지 않는다. 현재 수치가 충돌하면 `strategy.yaml`과 `JDSS_FINAL_SPEC.md`를 우선하고, 둘이 서로 다르면 코드를 변경하기 전에 불일치로 보고한다.
