# JD_HOLDINGS Current Work

> 집의 Codex, 외부의 ChatGPT, IDE의 Antigravity가 작업을 이어받기 위한 공용 인수인계 파일이다. GitHub 원격 저장소를 Source of Truth로 사용한다.

## 현재 작업 구조

- 운영 안정 기준선: `main`
- 현재 활성 브랜치: `main`
- FINAL 기능 PR #4: `main` 병합 완료 (`b871413`)
- Oracle dry-run 워크플로 원본: `ops/oracle-dry-run-deploy`
- 연구 기록: `research/jdss-v2-swing-optimization` (운영 병합 대상 아님)

## 현재 전략 버전

`JDSS-2.1.0-FINAL` / config `2.1.0`

기준 문서: `docs/JDSS_FINAL_SPEC.md`

## FINAL 전략 계약

- TQQQ, SOXL / 종목당 전략자금 $10,000
- 모든 매수 단계 Score 55 이상, Reversal Score 5 이상, `Regime != RED`
- 매수 비중 40% / 30% / 20% / 10%
- 최초 실제 체결가 대비 추가매수 -2% / -5% / -7%, 하루 최대 한 단계
- TP1 평단 +4% 약 50%, TP2 평단 +6% 잔량
- TP1 완전체결 후 20개 완결 거래일 경과 시 미체결 잔량을 평단 +2% `REMAINDER_EXIT`로 전환
- SOXL 섹터 가드: SOXX/SMH EMA60 기준 1·3·4차
- 자동손절·재매수 없음, 모든 매수는 2단계 사용자 승인 필수

## 완료 상태

- FINAL 코드·설정·명세와 운영 E2E Dry Run이 `main`에 병합됐다.
- TP2 자동복구, TP1 완료시점 영속화, 20거래일 판정, 잔여청산, 재시작 Reconciliation 및 SAFE_MODE 경로가 구현됐다.
- GitHub Actions FINAL Dry Run 게이트가 추가됐고 병합 전 CI를 통과했다.
- Oracle 배포용 수동 워크플로는 `main` 계보 확인, Ruff·pytest·설정 검증, 강제 `dry_run`, 실주문 확인값 제거, Toss 조회 전용 smoke를 수행한다.
- README, 전략·백테스트·Telegram·배포·개발 워크플로 문서를 FINAL 기준으로 정합화했다.
- 전체 Markdown·소스·설정·테스트 교차 감사에서 발견된 FINAL 누락을 수정했다.
  - 패키지·Telegram 표시용 코드 버전을 `2.1.0`으로 통일했다.
  - CLI와 Telegram `/bt`가 모두 `StrategyBacktestEngine`을 사용하도록 통일했다.
  - Telegram `/guide`의 50점, 3차 -4%, TP2 +8% 레거시 문구를 FINAL 수치로 교체했다.
  - SOXX/SMH 중 사용 가능한 벤치마크 하나로도 섹터 가드를 적용하도록 백테스트를 실거래 계약과 일치시켰다.
  - Telegram 타임라인을 문서대로 최근 15건으로 맞추고 `REMAINDER_EXIT`를 잔여청산으로 표시한다.
  - GitHub SHA checkout을 local `main`에 연결하고 `deploy.sh`가 `origin/main`과 정확히 일치할 때만 배포하도록 수정했다.
  - systemd의 DB·로그·캐시를 shared 경로에 두고 FINAL 설정 경로를 명시했다.
  - 모든 Markdown 로컬 링크를 검사하고 archive 문서의 깨진 링크와 최신 기준 안내를 수정했다.
- 로컬 검증: pytest 80개 통과, Ruff 통과, `jdss validate-config` 통과, FINAL E2E Dry Run 및 배포 계약 테스트 통과, YAML 파싱·Markdown 링크·`bash -n deploy.sh` 통과.
- GitHub CI 성공 후 확인된 Node 20 deprecation 경고를 제거하기 위해 공식 권장 `actions/checkout@v7`, `actions/setup-python@v7`로 모든 워크플로를 갱신했다.
- Telegram `/score`에 CCI·RSI·EMA·볼린저·거래량·ATR·종가 위치의 한국어 해석과 FINAL 핵심 조건 충족 여부를 추가했다.
- Telegram `/guide`에 보조지표 기준을 쉽게 설명하는 세 번째 카드를 추가하고 운영 가이드를 같은 내용으로 최신화했다.
- 로컬 및 배포 전 검증: pytest 81개 통과, Ruff 통과, Telegram 카드·점수 메시지 4,096자 제한 검증 완료.

## 운영 배포 상태와 남은 게이트

1. Oracle dry-run 배포와 조회 전용 smoke는 완료했다.
2. 당분간 Telegram `/bt` 백테스트 전용으로 운용한다.
3. JDSS SQLite의 TQQQ/SOXL은 `qty=0`, `EMPTY`, JDSS 미체결 주문 0건을 유지한다.
4. 향후 live 검토 시 기존 운영 자산과 JDSS 상태의 관계를 먼저 결정하고 Reconciliation을 재실행한다.
5. Reconciliation이 완전히 통과하기 전까지 신규 실주문을 금지하고 `dry_run`을 유지한다.

## 실거래 잠금

배포 준비 및 최초 Oracle 반영은 `JDSS_TRADING_MODE=dry_run`, 빈 `JDSS_LIVE_CONFIRMATION`을 유지한다. 실거래 활성화와 리스크 한도 변경은 별도 사용자 승인과 운영 점검 없이는 수행하지 않는다.

## 마지막 인수인계

- 작성 주체: Codex
- 상태: JDSS 2.1 FINAL 전체 계약 감사와 Oracle dry-run 배포 완료. `/score` 보조지표 해석과 `/guide` 설명 카드까지 운영 반영. JDSS 내부 포지션·주문은 비어 있으며 당분간 Telegram 백테스트 전용으로 운용. live 승격 금지.
- 마지막 관련 커밋: `8fb24d4` (`fix: align FINAL runtime and deployment contract`)
- main 반영 커밋: `2b209e8` (`docs: record FINAL contract audit`)
- 액션 갱신 커밋: `0e685cf` (`ci: upgrade workflows to Node 24 actions`)
- 백테스트 전용 운영 커밋: `f48b118` (`docs: set dry-run backtest-only operations`)
- Telegram 지표 설명 커밋: `b9dd21c` (`feat: clarify Telegram score indicators`)
- GitHub CI: run `31383081468` 성공 (`actions/checkout@v7`, `actions/setup-python@v7`, Ruff, pytest, 설정 검증)
- Oracle dry-run 배포: `b9dd21ca9a76dce9bdbe4d5f42c6991d27d53857` 배포 완료
- 배포 후 검증: 서비스 active/enabled, 패키지 2.1.0, FINAL 설정 검증, `dry_run` 잠금, Toss 인증·TQQQ/SOXL 시세 조회 성공
- JDSS SQLite 확인: TQQQ/SOXL `qty=0`, `EMPTY`, JDSS 미체결 주문 0건
- 운영 방침: Telegram `/bt` 백테스트 전용, `dry_run` 유지. 실제 계좌 상태를 JDSS에 자동 인수하거나 주문을 변경하지 않음.
- 다음 우선순위: Telegram 백테스트 동작 모니터링. live 검토 전 비공개 Reconciliation 재검증.

## 갱신 규칙

작업 종료 시 현재 브랜치, 마지막 커밋, 검증 결과, 완료 작업, 다음 작업을 갱신한다. 다음 작업자가 즉시 이어갈 수 있는 상태만 간결하게 유지한다.
