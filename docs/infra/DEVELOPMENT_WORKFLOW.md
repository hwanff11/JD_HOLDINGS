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

### 로컬 Codex·IDE

- 기능 개발, 디버깅과 재현 가능한 통합 테스트
- 작업 전 원격 동기화와 미커밋 변경 확인
- 로컬 환경이 필요한 Dry Run·재시작·복구 검증
- 종료 전 의도한 파일만 commit·push하고 인수인계 상태 갱신

### ChatGPT·GitHub

- 최신 `main`, `CURRENT_WORK.md`와 관련 기준 문서를 다시 읽고 작업 범위 확정
- 별도 `codex/` 또는 research 브랜치와 Draft PR에서 변경 추적
- Quality Gate, Security, Backtest/Research와 필요한 workflow 실행
- 실패 시 Job/Step 로그에서 원인을 확인하고 같은 조건으로 재검증
- 연구 결과의 핵심 지표는 Job Summary, 상세 원본은 artifact에 보존
- 미채택 후보와 일회성 workflow·스크립트·결과물을 `main`에 누적하지 않음

### GitHub Actions

- Ruff, pytest, 설정 검증과 배포 계약 검사
- 동일 조건의 기준 백테스트와 후보 연구
- PR 병합 전 반복 가능한 품질·보안 검증
- 상세 JSON/Markdown은 artifact로 보관하고 저장소 현행 문서로 복제하지 않음

### Oracle Cloud

- Telegram Bot, 정규장 종료 후 분석, 승인·주문·포지션 감시 등 상시 운영
- 연구용 대규모 백테스트와 후보 탐색은 운영 프로세스와 분리
- 검증 완료된 최신 `main`만 배포하고 현재 잠금·안전 계약 유지

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
- 배포 여부와 runtime SHA
- 남은 오류·불일치·다음 우선순위
- 전략/설정/live 계약 변경 여부

과거 실행 내역 전체를 복사하지 않고 PR·Actions 링크를 사용합니다.
