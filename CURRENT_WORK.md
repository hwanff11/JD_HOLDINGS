# JD_HOLDINGS Current Work

> 이 파일은 집의 Codex와 외부의 ChatGPT가 작업을 이어받기 위한 **공용 인수인계 상태 파일**이다.
> `AGENTS.md`의 `작업 시작` / `작업 종료` 규칙과 함께 사용한다.
>
> 원칙: 작업자는 세션 시작 시 이 파일을 먼저 읽고, 세션 종료 시 필요한 항목을 최신 상태로 갱신한다.

## 현재 활성 개발 브랜치

`codex/jdss-v1.3.1-review`

## 기준 브랜치

`main`

- `main`: 안정 기준선 및 공통 작업 규칙
- 활성 개발 브랜치: 실제 기능/전략 개발과 검증을 수행하는 브랜치
- 기능 검증이 끝나면 PR을 통해 `main`에 병합한다.

## 현재 전략 버전

JDSS v1.3.1 review candidate

## 현재 개발 목표

JDSS v1.3의 전략 로직을 검증하고 v1.3.1 후보를 안정화한다.

중점 사항:

- 반등 확인 없이 진입 가능한 문제 방지
- 점수 calibration 과도한 상향 문제 개선
- TQQQ/SOXL 분할매수 및 익절 구조 검증
- SOXL 추가매수 리스크 필터 검토/검증
- 백테스트 구현 신뢰성 검증
- 위험을 통제하면서 백테스트 및 실제 운용의 기대수익률 개선

## 마지막 완료 작업

- Telegram `/dashboard` 하단을 실제 계좌 버튼 대신 TQQQ/SOXL별 `/status`·`/score` 바로가기 버튼으로 변경
- `/score`의 5개 점수 구성을 동일한 글꼴·줄맞춤으로 통일하고 GREEN/YELLOW/RED 색상 원형 표시 추가
- `/account`에 원화 주문가능금액을 추가하고 토스의 소수 비율 수익률을 퍼센트로 올바르게 변환
- Telegram 명령 메뉴를 dashboard → status → score → signal 순서로 정렬
- 관련 단위 테스트 추가 후 `ruff check .` 및 전체 56개 테스트 통과
- Codex ↔ GitHub ↔ ChatGPT 협업 규칙을 `AGENTS.md` 및 `docs/DEVELOPMENT_WORKFLOW.md`에 정의
- `작업 시작` / `작업 종료` 단축 명령 운영 규칙 정의
- 공용 인수인계 상태 파일 `CURRENT_WORK.md` 도입
- JDSS 전략 평가 원칙을 '과최적화 회피'만 강조하는 표현에서 '위험 대비 수익률 극대화 + 재현성 검증' 원칙으로 명확화

## 다음 작업

1. `codex/jdss-v1.3.1-review`의 최신 상태를 기준으로 전체 전략/백테스트 코드를 다시 확인한다.
2. look-ahead bias(미래정보 사용), 데이터 누수, 체결가 가정, 동일 봉 이벤트 순서, 수수료/슬리피지 반영 여부를 검증한다.
3. v1.3.1 변경사항에 대해 `pytest` 및 `ruff check .`를 실행 가능한 환경에서 확인한다.
4. calibration A/B 및 진입점수 후보를 비교하되 과도한 파라미터 탐색은 피한다.
5. Total Return/CAGR뿐 아니라 MDD, MAE, 승률, 보유기간, 자금 활용률, 종목별/기간별 결과를 함께 평가한다.
6. 위험 수준이 비슷한 후보 중 수익률이 높은 전략을 우선하여 v1.3.1 공식 후보를 결정한다.

## 작업 환경

### 집

- 작업자: 사용자 + Codex
- 저장소: 로컬 clone + GitHub remote
- 시작: `작업 시작` → status/fetch/pull 후 개발
- 종료: `작업 종료` → test/commit/push/CURRENT_WORK 갱신

### 외부

- 작업자: 사용자 + ChatGPT
- 저장소: GitHub 원격 저장소 직접 접근
- 시작: `작업 시작` → CURRENT_WORK와 활성 개발 브랜치 최신 상태 확인
- 종료: `작업 종료` → 변경사항 commit/push/CURRENT_WORK 갱신 및 결과 보고

## 마지막 인수인계

- 작성 주체: Codex
- 상태: Telegram UI·계좌 표시 개선 완료, Oracle 배포 확인 진행
- 마지막 관련 커밋: `e783936` (`Improve Telegram dashboard and account display`)
- 주의: 실제 전략 개발은 `main`이 아니라 위의 활성 개발 브랜치에서 수행한다.

## 갱신 규칙

작업 종료 시 최소 다음 항목을 확인/갱신한다.

- 현재 활성 개발 브랜치
- 현재 전략 버전
- 현재 개발 목표(변경된 경우)
- 마지막 완료 작업
- 다음 작업
- 마지막 인수인계의 작성 주체/상태/커밋

장황한 작업일지로 만들지 않는다. 다음 작업자가 **어디서 무엇을 이어서 해야 하는지** 판단할 수 있을 정도로만 유지한다.
