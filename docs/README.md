# JD_HOLDINGS 문서 안내

현재 전략은 JDSS V3.2.2입니다. 과거 문서를 먼저 읽어 옛 전략을 현재 전략으로 오해하지 않도록 아래 순서를 사용합니다.

## 읽는 순서

1. [`CURRENT_WORK.md`](../CURRENT_WORK.md) — 현재 브랜치·배포·검증·다음 작업
2. [`ONE_PAGE_REPORT.md`](ONE_PAGE_REPORT.md) — 고등학생도 읽을 수 있는 한 장 요약
3. [`STRATEGY_GUIDE.md`](STRATEGY_GUIDE.md) — 상세 규칙·용어·백테스트·거래 흐름
4. [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md) — 전략·자금·주문의 공식 계약
5. [`strategy.yaml`](../strategy.yaml) — 코드가 읽는 정확한 수치

## 문서별 역할

| 문서 | 역할 |
|---|---|
| [`TELEGRAM_BOT_GUIDE.md`](TELEGRAM_BOT_GUIDE.md) | 명령·2단계 승인·SAFE_MODE |
| [`infra/DEPLOYMENT.md`](infra/DEPLOYMENT.md) | Oracle forced dry-run 배포·검증·롤백 |
| [`infra/DEVELOPMENT_WORKFLOW.md`](infra/DEVELOPMENT_WORKFLOW.md) | 브랜치·CI·인수인계 절차 |
| [`infra/SECURITY.md`](infra/SECURITY.md) | 비밀정보·주문·DB·배포 안전경계 |
| [`infra/DECISIONS.md`](infra/DECISIONS.md) | 현행 구현 결정 |
| [`research/RESEARCH_PROTOCOL.md`](research/RESEARCH_PROTOCOL.md) | 새 전략 연구 검증 규칙 |
| [`HISTORY.md`](HISTORY.md) | 대표 과거 버전·미채택 연구 요약 |

GitHub `main`이 소스의 기준입니다. 현재 브랜치, 최신 `main` SHA, Oracle 배포 SHA, 테스트 개수와 미완료 작업 같은 변동 정보는 [`CURRENT_WORK.md`](../CURRENT_WORK.md)에만 기록합니다.

## 변동 상태 기록 원칙

현재 전략 설명 문서에는 실행할 때마다 달라지는 SHA·테스트 개수·서버 상태를 복제하지 않습니다. 이 값은 [`CURRENT_WORK.md`](../CURRENT_WORK.md)만 갱신합니다.

과거 코드는 Git tag `v2.2.2`, `v3.0.0`, `v3.2.2`로 복구할 수 있습니다. v1 점수 회귀에 필요한 대표 설정 하나만 [`configs/strategy_v1.1.2.yaml`](../configs/strategy_v1.1.2.yaml)에 Archive 전용으로 남깁니다.
