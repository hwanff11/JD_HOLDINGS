# JD_HOLDINGS 개발 협업 워크플로

## 목적

집의 Codex, 외부의 ChatGPT, 그리고 IDE의 Antigravity(안티그라비티)가 GitHub 원격 저장소를 단일 기준점(Source of Truth)으로 사용해 같은 작업을 안전하게 이어간다. `main`에는 검증된 안정 버전만 반영한다.

## 현재 상태 확인

활성 개발 브랜치, 최신 `main` SHA, Oracle 배포 상태와 다음 작업은 [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)에서만 관리한다. 이 절차 문서에는 변동 상태를 복제하지 않는다.

문서를 처음 읽는 환경은 `CURRENT_WORK.md` → `docs/README.md` → 작업별 기준 문서 순서로 확인한다. `docs/archive/` 및 현행 문서의 `Archive` 구역은 과거 재현용이며 현재 구현의 입력으로 사용하지 않는다.

## 개발 환경별 역할

### 집: Codex + 로컬 PC

- 본격적인 기능 개발, 디버깅, 장시간 또는 대규모 백테스트를 수행한다.
- 작업 시작 시 원격 브랜치를 pull하고, 작업 종료 전 commit + push한다.
- 로컬 실행 환경이 필요한 통합 테스트와 운영 전 검증을 담당한다.

### 외부: ChatGPT + GitHub

- 사용자가 `작업 시작`, 전략 연구, 오류 수정, 저장소 변경 등을 요청하면 **항상 GitHub의 최신 `main`과 현재 작업 상태를 먼저 읽고** 시작한다. 과거 대화의 코드 상태만 믿고 수정하지 않는다.
- `CURRENT_WORK.md`, `AGENTS.md`, `docs/README.md`와 작업에 관련된 현행 기준 문서를 확인한 뒤 수정 범위를 정한다.
- `main`에서 직접 기능·전략 연구를 하지 않는다. 작업 목적에 맞는 별도 개발/연구 브랜치를 만들고, 필요한 경우 Draft PR을 만들어 변경과 검증 상태를 한곳에서 추적한다.
- 코드·전략 변경 후에는 GitHub Actions를 우선 사용해 CI(Quality Gate), Security, Dry Run, Backtest/Research 등 해당 작업에 필요한 검증을 실행한다.
- Actions가 실패하면 실패 자체를 전략 결과로 해석하지 않는다. 먼저 Job/Step 로그에서 원인을 확인하고, 코드·설정·워크플로 문제를 수정한 뒤 같은 조건으로 재실행한다. 운영 안전장치를 연구 편의를 위해 느슨하게 바꾸지 않으며, 필요한 경우 연구 프로세스 내부에서만 격리된 방식으로 실험한다.
- 백테스트·연구 결과는 핵심 지표를 GitHub Actions 로그와 Job Summary에 직접 출력하도록 구성한다. ChatGPT는 가능하면 Artifact를 매번 다운로드하지 않고 로그에서 Total Return, CAGR, MDD, Sharpe, Sortino, 평균 노출, 기간별 수익률 등 핵심 결과를 바로 읽어 비교한다. Artifact는 상세 JSON/Markdown 등 재현·감사용 원본 보관에 사용한다.
- 전략 연구는 한 번에 가능한 한 하나의 핵심 변수만 변경하고 baseline과 후보들을 동일 조건에서 비교한다. 결과가 좋지 않은 후보는 운영 코드에 누적시키지 않고 연구 PR을 병합 없이 종료한다.
- 후보 평가 시 단순 최고 수익률만 보지 않고 CAGR, MDD, Sharpe/Sortino, 최근 연도·반기별 성과, 평균 노출, 거래 수, 비용과 과최적화 가능성을 함께 본다. 개선 효과가 미미하면 미세 튜닝을 중단하고 다음 구조적 아이디어로 이동한다.
- 유효한 후보가 확인되어도 즉시 `main`에 반영하지 않는다. 연구 결과를 사용자에게 보고하고 채택 방향을 확정한 뒤, 필요한 문서·설정·테스트·구현을 함께 동기화하여 정식 변경으로 만든다.
- 작업 과정에서 CI/Security 오류, 불필요한 Workflow/PR/Issue 등 저장소 운영상 문제가 발견되면 연구 결과와 분리해 원인을 해결하고 GitHub 상태를 정상화한다.
- 외부 작업이 끝나면 원격 브랜치에 push하여 집의 Codex가 그대로 이어받을 수 있게 한다.

#### ChatGPT 문제 해결·전략 연구 기본 루프

```text
최신 main / CURRENT_WORK 확인
  ↓
현행 문서·설정·구현 확인
  ↓
작업 또는 research 브랜치 생성
  ↓
최소 범위 변경 + Draft PR
  ↓
GitHub Actions 실행
  ├─ CI / Ruff / pytest / 설정 검증
  ├─ Security
  └─ 필요 시 Research / Backtest / Dry Run
  ↓
실패 시 Job 로그에서 원인 확인
  ↓
원인 수정 → 동일 조건 재실행
  ↓
성공 시 Actions 로그/Summary에서 결과 직접 회수
  ↓
baseline과 성과·리스크 비교
  ├─ 탈락: PR 병합 없이 종료
  └─ 유망: 다음 단일 변수 실험 또는 사용자 채택 확인
  ↓
최종 채택안만 정식 구현·문서·테스트 동기화
  ↓
main 반영 → 필요 시 Oracle Cloud 배포
```

ChatGPT는 사용자가 매 단계마다 `확인해`, `계속해`라고 반복 지시해야만 다음 논리 단계가 무엇인지 판단하는 방식으로 작업하지 않는다. 한 번의 요청 범위 안에서 확인 가능한 실패 원인 분석, 수정, 재검증, 결과 비교까지 연결해서 수행한다. 단, GitHub Actions 완료처럼 실제 시간이 필요한 비동기 작업은 현재 실행 상태를 정확히 보고하며 완료되지 않은 결과를 추정하지 않는다.

### IDE: Antigravity

- 저장소 루트의 `AGENTS.md`, `CURRENT_WORK.md`, `docs/README.md`를 먼저 읽는다.
- IDE에 열려 있던 파일보다 GitHub 최신 `main`과 현재 작업 브랜치를 우선한다.
- 코드 탐색으로 문서의 수치를 검증하고, 과거 Archive 내용을 현재 설정으로 되돌리지 않는다.
- 작업 종료 전에 테스트 결과와 변경 이유를 `CURRENT_WORK.md`에 남기고 commit + push한다.

### GitHub: 공용 Source of Truth + 자동 검증 서버

- 소스코드와 작업 브랜치, PR을 관리한다.
- GitHub Actions를 이용해 `pytest`, `ruff`, 설정 검증, 연구용 백테스트를 자동 실행한다.
- 전략 후보 비교처럼 일회성·반복 가능한 계산은 Oracle Cloud 운영 서버보다 GitHub Actions를 우선 사용한다.
- 백테스트 결과는 핵심 요약을 Actions 로그/Job Summary에서 즉시 확인할 수 있게 하고, 상세 JSON/Markdown은 재검토 가능한 Artifact로 보관한다.

### Oracle Cloud: 24시간 JDSS 운영 서버

- Telegram Bot, 정규장 종료 후 분석, 매수 승인 흐름, 주문/포지션 감시 등 지속 실행이 필요한 운영 기능을 담당한다.
- 연구용 대규모 백테스트는 원칙적으로 Oracle Cloud 운영 프로세스와 분리한다.
- 검증 완료되어 `main`에 반영된 코드만 운영 서버 배포 대상으로 삼는다.

## 표준 개발·연구 흐름

```text
최신 main
  ↓
작업/연구 브랜치 생성
  ↓
Codex 또는 ChatGPT에서 코드·전략 수정
  ↓
GitHub에 push
  ↓
GitHub Actions
  ├─ Ruff
  ├─ pytest
  ├─ 설정 검증
  └─ 필요 시 전략 백테스트/후보 비교
  ↓
성과·리스크 검토
  ↓
채택 후보만 PR 검토 후 main 반영
  ↓
필요 시 Oracle Cloud 운영 서버 배포
```

전략 연구에서는 `main`의 운영 전략을 바로 덮어쓰지 않는다. 별도 research 브랜치에서 후보를 비교하고, Total Return/CAGR뿐 아니라 MDD, MAE, 장기 고착, 거래비용, 보유기간, 자금 활용률과 OOS 안정성을 함께 평가한다. 검증되지 않은 높은 백테스트 수익률만을 이유로 운영 전략을 교체하지 않는다.

## GitHub Actions 사용 원칙

GitHub Actions는 필요한 작업이 있을 때 GitHub가 임시 실행 환경을 제공하여 자동으로 작업한 뒤 종료하는 방식으로 사용한다.

적합한 작업:

- `pytest`, `ruff check .`, 설정 검증
- 전략 A/B/C 후보 비교
- TQQQ/SOXL 장기 백테스트
- 재현 가능한 연구 결과 생성
- PR 병합 전 자동 품질 확인

Oracle Cloud와 역할을 구분한다. GitHub Actions는 일회성 테스트·연구·검증용이고, Oracle Cloud는 JDSS의 상시 운영용이다.

## 사용자 단축 지시어

### `작업 시작`

1. `CURRENT_WORK.md`를 읽는다.
2. `docs/README.md`에서 현재 문서와 Archive의 경계를 확인한다.
3. 현재 저장소·브랜치와 미커밋 변경 여부를 확인한다.
4. 로컬 변경이 없으면 `git fetch origin`과 `git pull --ff-only`로 현재 브랜치를 최신화한다.
5. 로컬 변경이 있으면 임의로 덮어쓰거나 stash/reset하지 않고 사용자에게 상태를 알린다.
6. ChatGPT 환경에서는 최신 `main`, 활성 작업 브랜치, 관련 PR/Actions 상태까지 다시 읽는다.
7. 현재 브랜치와 최신 커밋을 간단히 보고한 뒤 작업한다.

```bash
git status
git fetch origin
git pull --ff-only
```

### `작업 종료`

1. 변경 파일과 의도하지 않은 변경이 없는지 확인한다.
2. 테스트와 정적 검사를 실행한다.
3. 실패한 검증은 원인과 함께 정확히 보고한다.
4. 변경사항을 현재 작업 브랜치에 commit·push한다.
5. `CURRENT_WORK.md`를 다음 작업자가 바로 이어갈 수 있도록 갱신한다.
6. 마지막 커밋 SHA, 테스트 결과, 완료 작업과 남은 작업을 보고한다.

기본 검증 명령:

```bash
pytest
ruff check .
```

기본 Git 절차:

```bash
git status
git add <변경파일>
git commit -m "<변경 내용>"
git push origin <현재브랜치>
```

## 핵심 규칙

1. 작업 시작 전 항상 원격 최신 상태를 확인한다.
2. 작업 종료 후 완료된 변경은 commit + push 한다.
3. 같은 브랜치를 Codex, ChatGPT, Antigravity가 동시에 수정하지 않는다.
4. 작업 장소를 바꾸기 전 현재 환경에서 먼저 push하고, 다음 환경에서는 최신 원격 상태를 확인한다.
5. 변경 유형별 문서·설정·테스트 동기화 범위는 저장소 루트 `AGENTS.md`의 변경 영향표를 따른다.
6. 실거래 활성화, 주문 로직, 자금관리 핵심 변경은 별도 검토 후 반영한다.
7. 강제 push, reset, rebase, 대량 삭제 등 파괴적 Git 작업은 사용자의 명시적 승인 없이 수행하지 않는다.
8. 충돌이 발생하면 충돌 파일과 해결 방향을 사용자에게 알리고, 양쪽 의도를 보존해 해결한다.
9. 연구용 백테스트는 운영 Oracle Cloud와 분리하고, 가능하면 GitHub Actions를 사용한다.
10. GitHub Actions에서 검증된 결과와 PR 검토를 거친 변경만 `main` 및 운영 배포 후보로 취급한다.

## 인수인계 체크리스트

- 현재 브랜치
- 마지막 커밋 SHA
- 완료한 작업
- 미완료 작업
- 테스트 결과
- GitHub Actions/백테스트 실행 상태 및 결과
- 다음 작업 우선순위
- 전략/설정 변경 여부

상세 상태는 `CURRENT_WORK.md`에 짧고 명확하게 유지한다.
