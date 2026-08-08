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
- 토스증권 주간거래 전 주문 리셋(08:50 KST) 완벽 대응 완료:
  - 08:50 ~ 09:00 KST 사이에는 주문 모니터링 봇을 일시 정지(Pause)시켜 불필요한 API 오류 및 무한 재시도 방지
  - 09:00 KST 정각에 봇이 깨어나며 기존 취소된 TP1/TP2 주문을 감지하고 자동으로 재접수(복구)하도록 조치
- 텔레그램 백테스트(`/bt`) 상장 초기 종목 데이터 부족 처리 완료:
  - `MarketDataError` 발생 시 친절한 에러 문구 표시 및 100일 요청 시 40일치 데이터만 있을 경우 40일치만 진행되는 로직을 안내하는 메시지 추가.
- 텔레그램 백테스트(`/bt`) 타임라인 출력 포맷 최적화:
  - 매수, 매도, 미체결 내역의 잔여 정보(점수, 수량, 가격, 사유 등)를 하나의 대괄호 파이프(`|`)로 묶어 가독성 극대화 (`🟢[260805][1차매수][51점|29주|$135.68]`)
- 전체 소스코드 보안성/로직 점검 및 상세 한글화 완료 (진짜 마지막 작전):
  - `database.py`: 동적 쿼리 구문 SQL 인젝션 가능성(`bandit B608`) 검토 후 `# nosec` 적용 완료
  - `order_manager.py`, `tp_manager.py`: 조용한 예외 처리(`try-except-pass`) 구문에 로깅(Warning) 보강
  - `core/` (스코어링, 지표), `backtest/engine.py` (시뮬레이터 코어), `telegram_bot.py` (봇 명령어) 등 핵심 비즈니스 로직에 상세 한글 Docstring 추가
  - `README.md` 및 `CURRENT_WORK.md` 최신화
- 전체 단위 테스트(57개) 통과 및 ruff, bandit 정적 검사 무결성 확인 완료
- 마크다운 문서 생태계 대청소 완료:
  - 낡은 `docs/spec` 및 `SCORE_CALIBRATION_V1.2.md` 파일을 `docs/archive/spec_v1/`로 이동(레거시 격리).
  - 인프라 및 운영 기술 문서(`DECISIONS.md`, `DEPLOYMENT.md`, `DEVELOPMENT_WORKFLOW.md`)를 `docs/infra/`로 이동.
  - 최상단 `docs/`에는 핵심 문서(전략, 텔레그램 봇 가이드, 리포트) 3개만 남겨 가독성 극대화.
- 텔레그램 `/bt` 백테스트 응답 메시지 UI 2차 최적화:
  - 군더더기 안내 문구(과거 데이터 부족 등) 및 쓰이지 않는 연평균(cagr) 행 완전 삭제.
  - 1차익절 및 2차완청 결과 문자열 맨 끝에 폭죽 이모티콘(🎉) 추가.
- 텔레그램 실시간 매수 검토 알림(`rv` 콜백) 편의성 개선:
  - 승인 유효시간(`review_token_ttl_minutes`)을 30분에서 480분(8시간)으로 대폭 연장하여, 새벽 발생 신호를 아침에도 여유롭게 검토 가능하도록 조치.
  - 토큰 만료 에러 팝업 시 유효시간(분)과 함께 좀 더 친절한 안내 문구가 표시되도록 `database.py` 수정.

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
- 상태: 보안 패치, 한글 주석 보강, 마크다운 문서 생태계 대청소 완료. 57개 단위테스트 및 정적분석(Ruff, Bandit) All Pass. 
- 마지막 관련 커밋: `1a513cc` (docs: clean up markdown files by moving infra docs and legacy score calibration)
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
