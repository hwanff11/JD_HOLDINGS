# JD_HOLDINGS 개발 협업 워크플로

## 목적

집의 Codex, 외부의 ChatGPT, 그리고 IDE의 Antigravity(안티그라비티)가 GitHub 원격 저장소를 단일 기준점(Source of Truth)으로 사용해 같은 작업을 안전하게 이어간다. `main`에는 검증된 안정 버전만 반영한다.

## 현재 활성 개발 브랜치

```text
main
```

현재 개발 기준은 **JDSS-2.2.0-SGOV**이며 운영 배포 기준은 아직 **JDSS-2.1.0-FINAL**이다. SGOV 기능은 `codex/jdss-2.2.0-sgov`에서 검증하고 PR을 거쳐 `main`에 반영한다.

문서를 처음 읽는 환경은 `CURRENT_WORK.md` → `docs/README.md` → 작업별 기준 문서 순서로 확인한다. `docs/archive/` 및 현행 문서의 `Archive` 구역은 과거 재현용이며 현재 구현의 입력으로 사용하지 않는다.

## 개발 환경별 역할

### 집: Codex + 로컬 PC

- 본격적인 기능 개발, 디버깅, 장시간 또는 대규모 백테스트를 수행한다.
- 작업 시작 시 원격 브랜치를 pull하고, 작업 종료 전 commit + push한다.
- 로컬 실행 환경이 필요한 통합 테스트와 운영 전 검증을 담당한다.

### 외부: ChatGPT + GitHub

- 최신 GitHub 소스를 먼저 읽고 전략 검토, 코드 수정, 테스트 추가, 연구 브랜치/PR 관리를 수행한다.
- 가능한 검증은 GitHub Actions로 실행한다.
- 외부 작업이 끝나면 원격 브랜치에 push하여 집의 Codex가 그대로 이어받을 수 있게 한다.

### IDE: Antigravity

- 저장소 루트의 `AGENTS.md`, `CURRENT_WORK.md`, `docs/README.md`를 먼저 읽는다.
- IDE에 열려 있던 파일보다 GitHub 최신 `main`과 현재 작업 브랜치를 우선한다.
- 코드 탐색으로 문서의 수치를 검증하고, 과거 Archive 내용을 현재 설정으로 되돌리지 않는다.
- 작업 종료 전에 테스트 결과와 변경 이유를 `CURRENT_WORK.md`에 남기고 commit + push한다.

### GitHub: 공용 Source of Truth + 자동 검증 서버

- 소스코드와 작업 브랜치, PR을 관리한다.
- GitHub Actions를 이용해 `pytest`, `ruff`, 설정 검증, 연구용 백테스트를 자동 실행한다.
- 전략 후보 비교처럼 일회성·반복 가능한 계산은 Oracle Cloud 운영 서버보다 GitHub Actions를 우선 사용한다.
- 백테스트 결과는 가능하면 JSON/Markdown 등 재검토 가능한 산출물(artifact)로 남긴다.

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
6. 현재 브랜치와 최신 커밋을 간단히 보고한 뒤 작업한다.

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
5. 전략 변경은 문서, 설정, 테스트, 코드가 서로 일치하도록 함께 수정한다.
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
