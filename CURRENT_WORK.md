# JD_HOLDINGS Current Work

> 이 파일은 집의 Codex, 외부의 ChatGPT, 그리고 IDE의 Antigravity(안티그라비티)가 작업을 이어받기 위한 **공용 인수인계 상태 파일**이다.
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

JDSS 2.0 Swing review candidate

## 현재 개발 목표

TQQQ/SOXL의 변동성을 활용하는 고회전 단타 스윙 전략을 검증하고 안정화한다.

중점 사항:

- 반등 확인 없이 진입 가능한 문제 방지
- 점수 calibration 과도한 상향 문제 개선
- TQQQ/SOXL 분할매수 및 익절 구조 검증
- SOXL 추가매수 리스크 필터 검토/검증
- 백테스트 구현 신뢰성 검증
- 위험을 통제하면서 백테스트 및 실제 운용의 기대수익률 개선

## 마지막 완료 작업

- Codex, ChatGPT, Antigravity 3개 AI 개발 환경 통합 협업 규칙 문서 반영 (`AGENTS.md`, `CURRENT_WORK.md`, `docs/DEVELOPMENT_WORKFLOW.md`)
- Telegram 매새지 및 메뉴 구성을 `cci_nvdl` 스타일로 시각적 가시성 및 가이드 문구 대폭 향상
- JDSS 2.0 Swing 후보를 진입 60점, 추가매수 -2/-4/-7%, TP1 +3%, TP2 +6%로 전면 재구성
- 2021~2024 +30.57%·MDD -12.18%·33사이클, 2025 +13.74%·16사이클, 2026년 1~7월 +5.29%·6사이클 확인
- 장기 2011~2026 결과 +157.55%·CAGR +6.26%·MDD -15.09%·153사이클 확인
- `docs/STRATEGY_V2.0_SWING.md`, 후보 탐색 스크립트와 상세 결과 JSON 추가

## 다음 작업

1. 점수 가중치, 진입 컷오프(entry_score), TP 익절 비율 Grid Search 백테스트 실행 및 고수익률 전략 조합 탐색
2. JDSS 2.0 Swing의 신호·미체결·TP1/TP2 흐름을 Telegram dry_run에서 확인한다.
3. 장기 최악 고착(TQQQ 658일, SOXL 727일)을 줄이되 고정손절처럼 수익을 훼손하지 않는 방법을 별도 연구한다.

## 작업 환경

### 집 (Codex)

- 작업자: 사용자 + Codex
- 저장소: 로컬 clone + GitHub remote
- 시작: `작업 시작` → status/fetch/pull 후 개발
- 종료: `작업 종료` → test/commit/push/CURRENT_WORK 갱신

### 외부 (ChatGPT)

- 작업자: 사용자 + ChatGPT
- 저장소: GitHub 원격 저장소 직접 접근
- 시작: `작업 시작` → CURRENT_WORK와 활성 개발 브랜치 최신 상태 확인
- 종료: `작업 종료` → 변경사항 commit/push/CURRENT_WORK 갱신 및 결과 보고

### IDE (Antigravity - JH홀딩스 개발부장)

- 작업자: 사용자 + Antigravity (안티그라비티)
- 저장소: 로컬 IDE workspace + GitHub remote
- 시작: `작업 시작` → status/fetch/pull 후 개발 및 실시간 코드 검증
- 종료: `작업 종료` → test/commit/push/CURRENT_WORK 갱신 및 완료 브리핑

## 마지막 인수인계

- 작성 주체: Codex
- 상태: JDSS 2.0 Swing 코드·백테스트 검증 및 Oracle dry_run 배포 완료
- 마지막 관련 커밋: `d346ef3` (`Redesign JDSS as high-turnover swing strategy`)
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
