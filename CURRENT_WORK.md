# JD_HOLDINGS Current Work

> 이 파일은 집의 Codex, 외부의 ChatGPT, 그리고 IDE의 Antigravity(안티그라비티)가 작업을 이어받기 위한 **공용 인수인계 상태 파일**이다.
> `AGENTS.md`의 `작업 시작` / `작업 종료` 규칙과 함께 사용한다.
>
> 원칙: 작업자는 세션 시작 시 이 파일을 먼저 읽고, 세션 종료 시 필요한 항목을 최신 상태로 갱신한다.

## 현재 활성 개발 브랜치

`main`

## 기준 브랜치

`main`

- `main`: 안정 기준선 및 공통 작업 규칙
- 활성 개발 브랜치: 실제 기능/전략 개발과 검증을 수행하는 브랜치
- 기능 검증이 끝나면 PR을 통해 `main`에 병합한다.

## 현재 전략 버전

JDSS 2.0 Swing (Option A: ultra_hf_50_tp48)

## 현재 개발 목표

TQQQ/SOXL 및 미국주식 변동성을 활용하는 고회전 단타 스윙 전략을 검증하고 안정화한다.

중점 사항:

- 고수익률 & 고회전 파라미터 조합 (진입 50점, TP 4%/8%) 실거래 dry_run 모니터링
- 임의 주식 티커 백테스트 기능 제공 (`/bt [티커] [기간]`)
- Oracle Cloud 서버 상 봇 서비스 안정 운영

## 마지막 완료 작업

- 고수익률 & 고회전 파라미터 Grid Search 백테스트 시뮬레이션 및 A안(`ultra_hf_50_tp48`) 최종 채택
- `strategy.yaml` 및 전략 파라미터 업데이트 (진입 score 50점, TP1 4%, TP2 8%)
- 텔레그램 백테스트(`/bt`)의 임의 주식 티커 제한 해제 (예: `/bt NVDA 100`, `/bt ALL`은 TQQQ+SOXL 유지)
- 신규 `/guide` (📖 JDSS 용어 & 지표 상세 가이드) 명령어 생성 및 텔레그램 메인 메뉴 추가
- 포지션 상태 4단계 이모티콘 색상화(🟢 1차, 🟡 2차, 🟠 3차, 🔴 4차) 및 관망중 ☕ 밝은 이모티콘 전환
- 대시보드 모바일 20자 구분선, 하단 인라인 버튼 제거 및 응답 속도 최적화, 누적매수금 분모($10,000) 버그 수정
- `docs/STRATEGY_V2.0_SWING.md`, `docs/BACKTEST_HIGH_RETURN_V2.0.md`, `README.md` 전면 상세화 및 부장님 스타일 개편
- 전체 단위 테스트(57개) 통과 및 ruff 정적 검사 통과
- `main` 브랜치 병합 및 GitHub 원격 저장소 커밋/푸시 완료 (`e2f1707`)
- Oracle Cloud 서버 자동 배포(`deploy.sh`) 완수 및 서비스 재시작

## 다음 작업

1. 텔레그램 봇 `dry_run` 상에서 JDSS 2.0 A안 신호 및 실시간 매수/익절 주문 흐름 모니터링
2. 백테스트 임의 티커 입력 결과를 바탕으로 종목별 수익률 패턴 비교 및 추가 개선점 탐색

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

- 작성 주체: Antigravity (JH홀딩스 개발부장)
- 상태: JDSS 2.0 A안 적용, 티커 백테스트 해제, /guide 명령어 추가, 4단계 포지션 색상화, Oracle Cloud 배포 완수
- 마지막 관련 커밋: `e2f1707` (`fix: escape unescaped ampersand in telegram /guide command HTML`)
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
