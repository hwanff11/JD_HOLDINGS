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

- 텔레그램 `/guide` 명령어 400 Bad Request 에러 버그 수정 완료 (HTML 이스케이프 `&gt;`, `&lt;` 및 `chat_id` 매핑 보강)
- 텔레그램 백테스트(`/bt`) 매매 내역 UI 4차 개편 완료:
  - 매수신호 1줄 컴팩트 추가 (`📣[260515][2차신호][85점][$205.00]`)
  - 1차익절 및 2차완청 이모티콘을 검은 동그라미(⚫)로 통일 적용
  - `[보유금액]` 삭제로 스마트폰 화면 1줄 고정 (줄바꿈 100% 방지)
  - 매수 4단계 이모지 시각화 (🟢 1차 / 🟡 2차 / 🟠 3차 / 🔴 4차)
- 텔레그램 백테스트(`/bt`) 매매 내역 가독성 최적화 (기본 최근 20개 출력 및 `<code>` 고정폭 모노스페이스 정렬)
- 마크다운 문서 최신화 완료 (`TELEGRAM_BOT_GUIDE.md` 생성, `DECISIONS.md` D-008/D-009 반영, `DEVELOPMENT_WORKFLOW.md` 최신화, `STRATEGY_GUIDE.md` 및 `BACKTEST_REPORT.md` 2.0 표기 명시)
- `docs/` 폴더 내 파편화된 문서들을 `STRATEGY_GUIDE.md`, `BACKTEST_REPORT.md` 로 통합 관리되도록 전면 개편
- `strategy.py`, `engine.py` 등 핵심 전략 코드에 한글 주석 상세 추가하여 1인 유지보수성 극대화
- 텔레그램 `/bt` 백테스트 명령 시 SOXL 섹터 가드(SOXX, SMH) 데이터를 추가로 불러와 실거래 로직과 일치하도록 불일치 이슈 해결
- 백테스트 데이터 소스가 `YFinance` 임을 확인 완료 (처음 조회하는 임의 종목도 인터넷을 통해 실시간 구성)
- 고수익률 & 고회전 파라미터 Grid Search 백테스트 시뮬레이션 및 A안(`ultra_hf_50_tp48`) 최종 채택
- 전체 단위 테스트(57개) 통과 및 ruff 정적 검사 통과

## 다음 작업

1. 텔레그램 봇 `dry_run` 상에서 JDSS 2.0 A안 신호 및 실시간 매수/익절 주문 흐름 모니터링

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
- 상태: 백테스트 매매내역 20건단계별 이모지(🟢🟡🟠🔴🌟🎉)+체결금액 표시 반영 완료, 57개 테스트 통과, OCI 배포 완수
- 마지막 관련 커밋: `287e67c` (`style: add 1-line signal entries, black circle icons for TP1/TP2 in backtest timeline`)
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
