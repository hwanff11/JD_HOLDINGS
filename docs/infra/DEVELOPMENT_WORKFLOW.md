# JH_HOLDINGS 개발 협업 워크플로

## 목적

Codex, ChatGPT와 Antigravity가 GitHub를 공용 Source of Truth로 사용해 기능·전략·문서 변경을 안전하게 이어갑니다. `main`에는 검증된 변경만 반영하고 Oracle에는 검증된 최신 `main`만 배포합니다.

에이전트가 반드시 실행할 `작업 시작`·`작업 종료`와 Git 안전 규칙은 루트 [`AGENTS.md`](../../AGENTS.md)가 소유합니다. 이 문서는 사람을 위한 전체 흐름과 환경별 책임만 설명하며 같은 체크리스트를 복제하지 않습니다.

## 현재 상태와 문서 확인

활성 개발 브랜치, 최신 `main` SHA, Oracle 배포 상태와 다음 작업은 [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)에서만 관리합니다. 이 절차 문서에는 변동 상태를 복제하지 않습니다.

읽는 순서는 다음과 같습니다.

1. `CURRENT_WORK.md`에서 현재 상태와 활성 목표 확인
2. [`../README.md`](../README.md)에서 작업별 소유 문서 확인
3. `strategy.yaml`, 공식 사양과 실제 구현의 일치 확인
4. 과거 근거가 필요할 때만 [`../HISTORY.md`](../HISTORY.md), Git tag, PR과 Actions artifact 확인

별도의 문서 archive 디렉터리나 버전별 문서를 만들지 않습니다.

## 환경별 역할

환경은 기능이 아니라 **책임과 권한 경계**로 구분합니다. 한 환경에서 확인할 수 없는 상태를 추정하거나 다른 환경의 역할을 임의로 대신하지 않습니다.

| 환경·주체 | 주 책임 | 수행 범위 | 금지·인계 기준 |
|---|---|---|---|
| 사용자(운영 책임자) | 우선순위·전략 채택·배포·실거래 승인 | 요구사항 확정, 연구 후보 선택, 배포 승인, Telegram 최종 BUY 승인 | 배포 승인은 live 활성화 승인이 아님. 실거래와 위험 한도 변경은 별도 명시 승인 |
| 로컬 Codex·IDE | 구현과 재현 가능한 로컬 검증 | 기능 개발, 디버깅, 테스트, 설정 검증, 작업트리 관리, 필요한 로컬 Dry Run | 미커밋 사용자 변경을 덮어쓰지 않음. 원격 로그인·Secret·운영 상태를 가정하지 않고 commit·push 가능한 상태로 인계 |
| ChatGPT Work·GitHub 연결 | 원격 저장소 변경과 작업 종결 관리 | 최신 원격 상태 확인, 브랜치·Draft PR 작성, 리뷰 반영, Actions 추적, 승인된 ChatOps 실행, 병합 결과 확인 | 비밀값을 조회·복제하지 않음. Cloud Browser 로그인·2FA는 사용자 직접 수행. 로컬 미커밋 파일이나 Oracle 내부 상태를 보았다고 가정하지 않음 |
| GitHub Actions | 반복 가능한 표준 검증과 승인된 배포 실행 | Ruff, pytest, 설정 검증, Security Gate, 기준 백테스트, artifact 생성, 최신 `main` forced dry-run 배포 | 임의 브랜치·미검증 코드를 운영에 배포하지 않음. Environment Secret은 사용만 하고 출력하지 않음 |
| Oracle Cloud | 검증된 JDSS 운영 runtime | Telegram Bot, 일일 분석, 승인·주문·포지션 감시, reconciliation, 운영 로그·DB 보존 | 연구·후보 탐색과 소스 편집을 수행하지 않음. `main` 병합만으로 자동 변경되지 않으며 승인된 배포 뒤에만 갱신 |

### 환경별 세부 원칙

- **로컬 Codex·IDE**는 가장 넓은 구현·테스트 환경입니다. 작업 전 원격 동기화와 미커밋 변경을 확인하고, 종료 시 의도한 파일만 commit·push합니다.
- **ChatGPT Work**는 연결된 GitHub 권한으로 원격 작업을 마감하는 환경입니다. 최신 `main`과 기준 문서를 다시 읽고 별도 브랜치·PR에서 변경하며, 필수 검사가 끝날 때까지 성공을 추정하지 않습니다.
- **Cloud Browser**는 GitHub 로그인·2FA 또는 사용자가 화면을 직접 확인해야 할 때만 보조적으로 사용합니다. 소스 변경의 기준은 브라우저 화면이 아니라 GitHub branch·PR·commit입니다.
- **GitHub Actions**는 사람이나 에이전트의 로컬 성공 주장을 대체하는 공통 검증 환경입니다. 핵심 결과는 Job Summary, 상세 원본은 artifact에 두고 현행 Markdown에 실행별 원본을 누적하지 않습니다.
- **Oracle Cloud**는 운영 전용입니다. 배포 뒤에는 forced dry-run, 서비스 상태, 설정 잠금, read-only smoke와 필요한 runtime 검증을 확인하며 연구용 대규모 백테스트는 분리합니다.

## 환경 전환과 인수인계

동일 브랜치를 여러 환경이 동시에 수정하지 않습니다. 작업 주체가 바뀔 때는 다음 순서를 지킵니다.

1. 작업 중인 환경이 변경 범위와 미완료 항목을 정리하고 로컬 검증 결과를 기록합니다.
2. 변경이 있으면 별도 브랜치에 commit·push하여 원격에서 재현 가능한 경계를 만듭니다. 미커밋 파일만 남긴 채 다른 환경에 작업을 넘기지 않습니다.
3. 다음 환경은 전달받은 설명보다 GitHub의 branch·commit·PR과 `CURRENT_WORK.md`를 우선 확인합니다.
4. 원격 상태가 예상과 다르거나 같은 브랜치에 새 변경이 있으면 작업을 중단하고 충돌 가능성을 보고합니다.
5. PR 작성 후에는 GitHub Actions를 공통 판정 기준으로 사용하고, 실패 원인과 수정은 같은 PR에서 추적합니다.
6. 병합 후 runtime 변경이 있는 경우에만 사용자 승인 범위 안에서 배포합니다. 문서-only 변경은 Oracle에 재배포하지 않습니다.
7. 배포 작업은 배포 결과와 runtime 검증까지 확인해야 완료입니다. ChatOps용 issue는 자동화 입력이며 장기 작업 목록으로 남기지 않습니다.

### 대표 작업의 담당 환경

- **기능·버그 수정**: 로컬 Codex·IDE에서 구현·테스트 → ChatGPT 또는 로컬 GitHub 흐름에서 PR·Actions 확인 → 필요 시 배포
- **문서-only 정리**: 로컬 또는 ChatGPT에서 현행 문서 수정 → 문서 계약 검사·PR → 병합 후 배포 생략
- **전략 연구**: 로컬/Actions에서 기준선과 후보 검증 → 사용자가 채택 결정 → 별도 구현 PR → 승인 시 배포
- **운영 장애 진단**: Oracle 로그·runtime verifier로 사실 확인 → 로컬에서 재현·수정 → PR·Actions → 승인된 복구 배포
- **실거래 전환**: 일반 배포와 분리하여 사용자 명시 승인, 계좌 preflight, 주문·회계·복구 리허설을 모두 충족한 별도 변경으로 처리

## 표준 변경 흐름

~~~text
CURRENT_WORK와 최신 main 확인
  ↓
작업별 기준 문서·설정·구현 비교
  ↓
별도 작업/연구 브랜치 생성
  ↓
최소 범위 변경 + Draft PR
  ↓
필수 Ruff·pytest·설정 검증
  ├─ 전략 변경: no-lookahead 백테스트·OOS·비용 검증
  ├─ 주문/DB: 멱등성·부분체결·재시작·reconciliation
  └─ 배포: 권한·forced dry-run·read-only smoke
  ↓
실패 원인 수정 → 같은 조건 재검증
  ↓
문서·설정·코드·테스트 일치 검토
  ↓
PR 병합 → 필요 시 Oracle 배포·검증
  ↓
CURRENT_WORK 롤링 갱신
~~~

비동기 Actions가 끝나지 않았으면 결과를 추정하지 않고 실행 중 상태를 정확히 보고합니다. 확인 가능한 실패 분석·수정·재검증은 한 요청 범위 안에서 계속 진행합니다.

배포 ChatOps는 저장소 소유자가 제목을 `[deploy-oracle-dry-run]`으로 시작하는 issue를 열면 동작합니다. runtime verifier는 `[verify-oracle-v322]` 접두사를 사용합니다. 두 workflow 모두 임의 ref 입력을 받지 않고 실행 시점의 최신 `main`만 checkout하며, 배포·검증 성공이 live 잠금 해제를 뜻하지 않습니다.

## 전략 연구 흐름

1. 현행 production 엔진이 기준 결과를 재현하는지 확인합니다.
2. 가능한 한 한 번에 하나의 구조적 변수를 변경합니다.
3. 후보와 기준선에 같은 데이터·비용·체결·자금계약을 사용합니다.
4. CAGR·MDD·Sharpe/Sortino뿐 아니라 MAE, 손실 지속, 평균노출, 거래 수와 기간별 안정성을 확인합니다.
5. train/validation에서 선택한 뒤 OOS를 열며, 이미 본 OOS는 shadow 근거로만 사용합니다.
6. 유망 후보도 사용자 결정 전에는 production 문서·설정에 반영하지 않습니다.
7. 채택 후보만 별도 구현 PR로 만들고, 미채택 상세 결과는 PR·artifact에 남깁니다.

세부 검증은 [`../research/RESEARCH_PROTOCOL.md`](../research/RESEARCH_PROTOCOL.md)를 따릅니다.

## 문서 변경 흐름

- 현재판 문서의 파일명은 고정하고 제자리에서 갱신합니다.
- 전략 릴리즈는 `strategy.yaml`, 공식 사양, 전략 가이드, 한 장 보고서를 같은 PR에서 동기화합니다.
- 명령·버튼·메시지가 바뀌면 Telegram 가이드와 도움말·포맷 테스트를 함께 갱신합니다.
- 완료된 마이그레이션이나 대표 결정은 `HISTORY.md`에 한 항목만 추가합니다.
- 현재 SHA·배포·검증은 `CURRENT_WORK.md`에만 두며 새 인수인계 문서를 만들지 않습니다.
- 문서만의 설명 변경은 동작 변경과 섞이지 않게 검토하되, 코드 동작이 바뀌었다면 관련 운영 문서를 같은 작업에서 완료합니다.

## PR 완료 기준

- 의도하지 않은 변경과 비밀정보가 없음
- 변경 영향표에 맞는 설정·코드·테스트·문서가 함께 갱신됨
- 필수 로컬 검증과 GitHub Actions 결과를 숨기지 않음
- 전략 변경이면 기준선·후보·OOS·비용과 위험 비교가 재현 가능함
- 주문/DB 변경이면 승인·멱등성·부분체결·재시작·SAFE_MODE를 검증함
- 배포 변경이면 forced dry-run, 최소권한, smoke와 rollback을 검증함
- `CURRENT_WORK.md`가 현재 상태와 다음 작업만 나타내도록 롤링 갱신됨

## 인수인계 항목

- 현재 브랜치와 마지막 커밋 SHA
- 완료한 작업과 의도한 동작 변화
- 테스트·Actions·백테스트 상태와 결과
- 배포 여부와 source/runtime revision 일치 여부
- 남은 오류·불일치·다음 우선순위
- 전략/설정/live 계약 변경 여부

과거 실행 내역 전체를 복사하지 않고 PR·Actions 링크를 사용합니다.
