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
| [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md) | JDSS-2.2.2-SGOV 전략·주문·자금관리 계약 | 현재 전략의 최종 기준 |
| [`TELEGRAM_BOT_GUIDE.md`](TELEGRAM_BOT_GUIDE.md) | Telegram 명령과 출력 해석 | 현재 UI 기준 |
| [`infra/DEPLOYMENT.md`](infra/DEPLOYMENT.md) | Oracle dry-run 배포·검증·롤백 | 현재 배포 기준 |
| [`infra/DEVELOPMENT_WORKFLOW.md`](infra/DEVELOPMENT_WORKFLOW.md) | Codex·ChatGPT·Antigravity 협업 절차 | 현재 개발 기준 |
| [`infra/SECURITY.md`](infra/SECURITY.md) | 비밀정보·인증·주문·DB·배포 보안 기준 | 현재 보안 기준 |
| [`infra/DECISIONS.md`](infra/DECISIONS.md) | 구현 결정과 변경 이유 | 누적 기록, 최신 결정 우선 |
| [`STRATEGY_GUIDE.md`](STRATEGY_GUIDE.md) | 현재 전략 요약과 과거 전략 발전사 | 첫 부분만 현재, Archive는 과거 |
| [`BACKTEST_REPORT.md`](BACKTEST_REPORT.md) | 현재 회귀 요약과 과거 백테스트 기록 | 첫 부분만 현재, Archive는 과거 |
| [`research/PR27_SIMPLE_STRATEGY_RESEARCH.md`](research/PR27_SIMPLE_STRATEGY_RESEARCH.md) | PR #27 대안 전략군과 최종 승격 보류 근거 | 연구 기록, 운영 설정 아님 |
| [`archive/spec_v1/`](archive/spec_v1/) | v1 계열 명세 보존 | 현재 구현에 사용 금지 |
| [`../configs/strategy_v1.1.2.yaml`](../configs/strategy_v1.1.2.yaml) | v1.1.2 연구 재현용 설정 | Archive 전용, 현재 구현에 사용 금지 |

## 변동 상태 기록 원칙

현재 브랜치, 최신 커밋, Oracle 배포 SHA, 테스트 개수, 미완료 작업은 자주 바뀌므로 이 문서에 복제하지 않는다. 해당 정보는 [`../CURRENT_WORK.md`](../CURRENT_WORK.md)만 기준으로 확인한다. 이 문서는 문서의 역할과 탐색 경로만 유지한다.

## 과거 문서 사용 규칙

`Archive`, `Legacy`, `v1.x`, `v2.0`으로 표시된 내용은 연구 재현과 의사결정 이력 보존용이다. 그 안의 50점 진입, -4% 추가매수, +8% TP2 같은 값은 현재 전략에 적용하지 않는다. 현재 수치가 충돌하면 `strategy.yaml`과 `JDSS_FINAL_SPEC.md`를 우선하고, 둘이 서로 다르면 코드를 변경하기 전에 불일치로 보고한다.
