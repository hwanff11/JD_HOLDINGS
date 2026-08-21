# JH_HOLDINGS 문서 안내

이 문서는 JH_HOLDINGS 문서의 **역할과 수명주기**를 정하는 기준입니다. 현재 버전과 운영 상태는 여기 복제하지 않고 [`CURRENT_WORK.md`](../CURRENT_WORK.md)에서 확인합니다.

## 읽는 순서

1. [`CURRENT_WORK.md`](../CURRENT_WORK.md) — 현재 릴리즈·배포·검증·활성 작업
2. [`ONE_PAGE_REPORT.md`](ONE_PAGE_REPORT.md) — 고등학생도 읽을 수 있는 현재 전략 한 장 요약
3. [`STRATEGY_GUIDE.md`](STRATEGY_GUIDE.md) — 현재 전략의 쉬운 상세 설명·기준 백테스트·거래 흐름
4. [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md) — 전략·자금·주문 구현이 따라야 하는 규범 계약
5. [`strategy.yaml`](../strategy.yaml) — 애플리케이션이 읽는 정확한 실행 수치

같은 내용이 다르면 `CURRENT_WORK.md`의 상태, `strategy.yaml`의 수치, 공식 사양의 계약, 실제 구현 순서로 확인하되 임의로 한쪽에 맞추지 않고 **불일치로 보고**합니다.

## 문서별 소유권

| 문서 | 이 문서만 소유하는 내용 | 갱신 방식 |
|---|---|---|
| [`CURRENT_WORK.md`](../CURRENT_WORK.md) | 현재 릴리즈·SHA·배포·검증·활성 목표·다음 작업 | 이전 상태를 교체하는 롤링 상태판 |
| [`ONE_PAGE_REPORT.md`](ONE_PAGE_REPORT.md) | 비전문가용 핵심 요약·QQQ 비교·용어 | 현재판으로 제자리 갱신 |
| [`STRATEGY_GUIDE.md`](STRATEGY_GUIDE.md) | 쉬운 규칙·예시·흐름도·승인된 기준 백테스트 결과 | 현재판으로 제자리 갱신 |
| [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md) | 전략·자금·주문·백테스트 방법의 규범 계약 | 현재판으로 제자리 갱신 |
| [`TELEGRAM_BOT_GUIDE.md`](TELEGRAM_BOT_GUIDE.md) | 명령 문법·메시지 의미·버튼·2단계 승인·오류 대응 | 코드·도움말·포맷 테스트와 동시 갱신 |
| [`HISTORY.md`](HISTORY.md) | 대표 릴리즈·채택/기각 결정·완료된 일회성 전환 | 기존 항목을 보존하고 한 항목씩 추가 |
| [`infra/DEPLOYMENT.md`](infra/DEPLOYMENT.md) | Oracle 배포·검증·백업·롤백 절차 | 버전 중립적인 절차만 유지 |
| [`infra/DEVELOPMENT_WORKFLOW.md`](infra/DEVELOPMENT_WORKFLOW.md) | 사람을 위한 브랜치·PR·CI·인수인계 흐름 | 절차가 바뀔 때만 갱신 |
| [`infra/SECURITY.md`](infra/SECURITY.md) | 인증·주문·DB·네트워크·배포의 기술적 안전 경계 | 보안 경계가 바뀔 때 갱신 |
| [`research/RESEARCH_PROTOCOL.md`](research/RESEARCH_PROTOCOL.md) | 전략 후보의 검증·OOS·비용·artifact 규칙 | 연구 방법이 바뀔 때만 갱신 |
| [`../AGENTS.md`](../AGENTS.md) | 에이전트가 반드시 지킬 작업·안전 규칙 | 실행 규칙 변경 시 갱신 |
| [`../SECURITY.md`](../SECURITY.md) | GitHub에서 찾기 쉬운 보안 신고 진입점 | 신고 방법 변경 시 갱신 |

중요한 결정의 결과와 이유는 [`HISTORY.md`](HISTORY.md), 정확한 현재 계약은 공식 사양·보안·배포 문서에 기록합니다. 별도의 현행 결정 요약 문서를 만들어 계약을 다시 복제하지 않습니다.

## 문서 수명주기

1. 현행 파일명은 고정합니다. 버전이나 날짜가 붙은 새 전략 문서, `FINAL` 복사본, 백테스트 결과 Markdown을 만들지 않습니다.
2. 새 릴리즈에서는 `strategy.yaml`, 공식 사양, 전략 가이드와 한 장 보고서를 **같은 PR에서 제자리 갱신**합니다.
3. `CURRENT_WORK.md`는 append-only 일지가 아닙니다. 현재 상태와 바로 다음 작업만 남기고, 지난 SHA·Actions·배포 기록은 GitHub와 Git 이력으로 확인합니다.
4. `HISTORY.md`에는 릴리즈당 한 항목, 대표 미채택 연구당 한 항목만 추가합니다. 과거 전체 코드·설정·문서는 Git tag에서 복구합니다.
5. 미채택 연구의 일회성 스크립트와 상세 결과는 `main`에 쌓지 않고 연구 PR과 Actions artifact에 보관합니다.
6. 완료된 일회성 마이그레이션은 역사에 요약하고 상시 배포 가이드에서 제거합니다.
7. 상세 내용의 복제를 피합니다. 단, 안전 경고와 한 장 보고서의 핵심 지표처럼 독자가 즉시 알아야 하는 파생 설명에는 항상 소유 문서 링크를 붙입니다.

## 변동 상태 기록 원칙

활성 개발 상태, 최신 `main`과 Oracle 배포의 동기화 여부, 검증 결과, 서버 상태와 미완료 작업은 [`CURRENT_WORK.md`](../CURRENT_WORK.md)에만 기록합니다. 공개 문서에는 서버 절대경로·서비스 실명·backup/snapshot 파일명·host 식별자·일회성 Actions run ID를 기록하지 않습니다.

과거 재현은 별도의 문서 archive 디렉터리를 만들지 않고 Git tag, Git history, 병합 PR과 Actions artifact를 사용합니다. 회귀 테스트에 필요한 과거 설정만 [`configs/strategy_v1.1.2.yaml`](../configs/strategy_v1.1.2.yaml)처럼 **ARCHIVE ONLY**로 명확히 표시해 예외적으로 보존합니다.
