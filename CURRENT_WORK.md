# JD_HOLDINGS Current Work

> 이 파일은 집의 Codex, 외부의 ChatGPT, 그리고 IDE의 Antigravity(안티그라비티)가 작업을 이어받기 위한 **공용 인수인계 상태 파일**이다.
> `AGENTS.md`의 `작업 시작` / `작업 종료` 규칙과 함께 사용한다.
>
> 원칙: 작업자는 세션 시작 시 이 파일을 먼저 읽고, 세션 종료 시 필요한 항목을 최신 상태로 갱신한다.

## 현재 활성 개발 브랜치

`research/jdss-v2-swing-optimization`

## 기준 브랜치

`main`

- `main`: 검증된 안정 기준선 및 공통 작업 규칙
- `research/jdss-v2-swing-optimization`: JDSS 2.0 전략 후보 비교와 백테스트 검증을 수행하는 현재 연구 브랜치
- 기능/전략 검증이 끝나면 PR을 통해 채택된 변경만 `main`에 병합한다.

## 현재 전략 버전

JDSS 2.0 Swing (Option A: ultra_hf_50_tp48)

## 현재 개발 목표

TQQQ/SOXL 및 미국주식 변동성을 활용하는 고회전 단기 스윙 전략을 검증하고 안정화한다.

현재 연구 목표:

- JDSS 2.0 A~F 전략 후보를 동일 조건으로 백테스트하여 현재 A안 대비 개선 가능성을 검증한다.
- 2021~2024 검증구간(OOS 성격)을 우선 평가한다.
- CAGR/Total Return뿐 아니라 MDD, P95 MAE, 40일 초과 고착비율, 평균 보유기간, 사이클 수 등 위험·회전 지표를 함께 비교한다.
- TQQQ/SOXL 실거래 후보 전략과 임의 미국주식 연구용 백테스트 기능을 분리하여 관리한다.
- Oracle Cloud 서버의 JDSS 운영 안정성을 유지하면서 연구용 계산은 운영 프로세스와 분리한다.

## 현재 전략 후보

- A: 현재 기준안 — 진입 50 / 추가매수 -2·-4·-7% / TP 4·8%
- B: 진입점수 55
- C: 추가매수 -3·-6·-10%
- D: TP 3·6%
- E: 추가매수 -3·-6·-10% + TP 3·6%
- F: TP 5·10%

## 마지막 완료 작업

- GitHub Actions와 Oracle Cloud 역할 분리 및 표준 연구/개발 흐름을 `docs/infra/DEVELOPMENT_WORKFLOW.md`에 명문화했다.
- `AGENTS.md`에도 GitHub Actions=연구/검증, Oracle Cloud=24시간 운영 원칙을 반영하여 문서 간 규칙을 통일했다.
- 현재 활성 브랜치를 `research/jdss-v2-swing-optimization`, 기준 브랜치를 `main`으로 명확히 구분했다.
- JDSS 2.0 A~F 후보 비교용 연구 브랜치 및 GitHub Actions 백테스트 흐름을 구성했다.
- 텔레그램 `/bt` 임의 티커 백테스트 기능과 기존 운영 기능은 유지한다.

## 다음 작업

1. GitHub Actions의 JDSS 2.0 A~F 백테스트 완료 여부 및 산출물을 확인한다.
2. TQQQ/SOXL별 및 합산 성과를 비교한다.
3. 2021~2024 검증구간을 우선으로 CAGR, MDD, P95 MAE, 장기 고착, 평균 보유기간, 사이클 수를 평가한다.
4. 유의미한 개선 후보가 있으면 과최적화 여부를 추가 검증한 뒤 최종 채택안을 결정한다.
5. 채택된 전략만 PR 검토 후 `main`에 병합하고 필요 시 Oracle Cloud 운영 서버에 배포한다.

## 실행 환경 역할

### 집 (Codex + 로컬 PC)

- 본격 기능 개발, 디버깅, 장시간/대규모 백테스트
- 시작: `작업 시작` → status/fetch/pull 후 개발
- 종료: `작업 종료` → test/commit/push/CURRENT_WORK 갱신

### 외부 (ChatGPT + GitHub)

- 최신 GitHub 소스를 기준으로 전략 검토, 코드 수정, 테스트 추가, 연구 브랜치/PR 관리
- 가능한 자동 검증과 전략 비교는 GitHub Actions를 우선 사용
- 작업 결과는 원격 브랜치에 push하여 Codex가 그대로 이어받도록 한다.

### IDE (Antigravity)

- 로컬 IDE workspace + GitHub remote를 사용한 개발 및 실시간 코드 검증
- 환경 전환 전 commit/push, 시작 시 최신 원격 상태 동기화

### GitHub Actions

- pytest, Ruff, 설정 검증, 전략 A/B 테스트, 장기 백테스트 등 일회성·반복 가능한 연구/검증 작업
- 가능하면 JSON/Markdown 등의 결과 artifact를 남겨 재검토 가능하게 한다.

### Oracle Cloud

- Telegram Bot, 정규장 종료 후 분석, 승인/주문/포지션 감시 등 24시간 JDSS 운영 서비스
- 연구용 대규모 백테스트와 분리한다.
- 검증되어 `main`에 반영된 코드만 운영 배포 대상으로 삼는다.

## 마지막 인수인계

- 작성 주체: ChatGPT
- 상태: JDSS 2.0 전략 최적화 연구 진행 중. 개발/연구/운영 환경 역할과 브랜치 규칙을 AGENTS.md, CURRENT_WORK.md, DEVELOPMENT_WORKFLOW.md에 일관되게 정리 완료.
- 활성 브랜치: `research/jdss-v2-swing-optimization`
- 기준 브랜치: `main`
- 다음 우선순위: A~F GitHub Actions 백테스트 결과 확인 및 전략 후보 평가

## 갱신 규칙

작업 종료 시 최소 다음 항목을 확인/갱신한다.

- 현재 활성 개발 브랜치
- 현재 전략 버전
- 현재 개발 목표(변경된 경우)
- 마지막 완료 작업
- 다음 작업
- 마지막 인수인계의 작성 주체/상태/커밋

장황한 작업일지로 만들지 않는다. 다음 작업자가 **어디서 무엇을 이어서 해야 하는지** 판단할 수 있을 정도로만 유지한다.
