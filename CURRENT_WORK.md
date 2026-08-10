# JD_HOLDINGS Current Work

> 집의 Codex, 외부의 ChatGPT, IDE의 Antigravity가 작업을 이어받기 위한 공용 인수인계 파일이다. GitHub 원격 저장소를 Source of Truth로 사용한다.

## 현재 작업 구조

- 운영 안정 기준선: `main`
- 현재 활성 브랜치: `codex/simplify-oracle-deployment`
- 작업 기준: `origin/main` 최신; 운영 SHA는 서버 `current` 링크가 실시간 기준
- JDSS 2.2.2 PR #13: `main` 병합 및 Oracle `dry_run` 배포 완료 (`a98b671`)
- JDSS 2.2.1 PR #11: `main` 병합 및 Oracle 배포 완료 (`3de66d1`)
- JDSS 2.2 SGOV PR #8: `main` 병합 완료 (`c86ca23`)
- FINAL 기능 PR #4: `main` 병합 완료 (`b871413`)
- Oracle dry-run 워크플로 원본: `ops/oracle-dry-run-deploy`
- 연구 기록: `research/jdss-v2-swing-optimization` (운영 병합 대상 아님)

## 현재 전략 버전

`JDSS-2.2.2-SGOV` / config·package `2.2.2`

기준 문서: `docs/JDSS_FINAL_SPEC.md`

## JDSS 2.2 전략 계약

- TQQQ, SOXL / 종목당 전략자금 $10,000
- 모든 매수 단계 Score 55 이상, Reversal Score 5 이상, `Regime != RED`
- 매수 비중 40% / 30% / 20% / 10%
- 최초 실제 체결가 대비 추가매수 -2% / -5% / -7%, 하루 최대 한 단계
- TP1 평단 +4% 약 50%, TP2 평단 +6% 잔량
- TP1 완전체결 후 20개 완결 거래일 경과 시 미체결 잔량을 평단 +2% `REMAINDER_EXIT`로 전환
- SOXL 섹터 가드: SOXX/SMH EMA60 기준 1·3·4차
- 자동손절·재매수 없음, 모든 매수는 2단계 사용자 승인 필수
- 총 전략 배정금에서 TQQQ/SOXL 현재 원가와 `$250` 버퍼를 제외한 유휴자금을 SGOV로 운용
- 목표보다 `$100` 이상 부족할 때만 SGOV 추가 예치
- SGOV 매수는 최우선 매도호가 `+$0.01`, 매도는 최우선 매수호가 `-$0.01`; 호가 실패 시 현재가 `±0.1%`
- 60초 미체결 SGOV 지정가는 취소 후 최신 호가로 재가격
- 전략 매수 전 필요한 JDSS 관리 SGOV를 선매도하고 체결·달러 매수가능금액 확인 후 `/signal` 재실행 없이 최종 승인 자동 재개
- 현금화 의도·최종 승인 대기 중 SGOV 자동 재예치 차단, 최종 TQQQ/SOXL 주문은 사용자 승인 필수
- 기존 개인 SGOV는 비관리 수량으로 격리하며 자동 편입·매도 금지
- SGOV 전용 원장·부분체결·미체결 주문 Reconciliation과 SAFE_MODE 적용

## JDSS 2.2 구현 완료 상태

- Oracle dry-run 배포 경로를 단일 `deploy.sh`로 통합해 Actions의 중복 pytest·Ruff, 별도 dry-run SSH, 별도 smoke SSH를 제거했다.
- 로컬 직접 배포의 기본 검증은 유지하고 Actions에서만 중복 검증을 생략하며, 서버 dry-run 강제·재시작 1회·Toss smoke test를 한 경로에서 수행하도록 정리했다.
- 배포 때마다 후속 문서 PR이 필요하지 않도록 실제 운영 SHA의 기준을 서버 `current` 링크와 배포 출력으로 통일했다.
- ChatGPT에서도 서버 비밀값 접근 없이 저장소 소유자 배포 이슈와 40자리 `main` SHA로 Actions를 시작하고, 결과를 이슈 댓글로 확인하는 dry-run ChatOps 경로를 추가했다.
- 2.2.2에서 SQLite `cash_release_intents`로 신호별 SGOV 현금화 의도를 영속화하고 재시작 후 자동 재개를 구현했다.
- SGOV 체결 후 TQQQ/SOXL 최종 승인을 자동 전송하되 본 주문의 수동 최종 승인은 유지했다.
- 활성 현금화 중 SGOV 자동 재예치 차단, 복수 의도 현금 예약, 실제 DB·연결 주문 취소를 구현했다.
- Toss 호가 조회와 호가 기반 시장가성 지정가, 60초 취소·재가격, `±0.1%` fallback을 구현했다.
- 2.2.2 전체 pytest 98개, Ruff, 설정·배포 스크립트·Markdown 링크 검증을 로컬에서 통과했다.
- `strategy.yaml`, 패키지, Telegram 표시 버전을 `2.2.1`으로 올렸다.
- SGOV 전용 자금관리 서비스와 SQLite `idle_cash_state`, 누적 부분체결 진행 원장을 추가했다.
- 자동 예치, 전략 매수 전 선현금화, 개인 SGOV 격리, 재시작 정합성 검사를 구현했다.
- Telegram `/sgov`, 대시보드, 도움말, 4번째 가이드 카드와 SGOV 주문 표시를 추가했다.
- CLI·Telegram 백테스트가 SGOV 상장 전 0%, 상장 후 조정종가 일별 수익률을 유휴현금에 적용하도록 연결했다.
- SGOV 관리 테스트를 추가했고 전체 pytest 89개가 통과했다.
- 2011-01-01~2026-08-04 장기 회귀: 포트폴리오 `+322.58%`, CAGR `+9.69%`, MDD `-24.68%`, SGOV 기여 `$10,810.54`.
- 구현 커밋 `aaab561`을 PR #8로 검증해 `main`에 병합했고 Oracle에 `c86ca23`을 배포했다.
- Oracle 운영은 JDSS 2.2.2이며 서비스 active, `dry_run` 잠금, 빈 `JDSS_LIVE_CONFIRMATION`을 유지한다. 실제 SHA는 서버 `current` 링크로 확인한다.
- 배포 후 TQQQ·SOXL·SGOV 시세와 미국장 캘린더 조회 전용 Toss smoke test를 통과했다.
- Telegram `/signal`이 DB의 `ACTIVE` 플래그만 신뢰하던 결함을 수정해, 표시·승인 전에 현재 버전과 점수·반등·RED 국면 게이트를 재검증하고 부적격 레코드를 `INVALID` 처리한다.

## 2.1 기준선 완료 상태

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
- Git 추적 대상 Markdown 18개를 소스·`strategy.yaml`·워크플로와 교차 검증했다.
  - `docs/README.md`를 새 문서 진입점으로 추가해 현재 계약·운영 문서·Archive의 우선순위를 명확히 했다.
  - README의 Oracle 배포 SHA를 `b9dd21c`로 갱신하고 TQQQ/SOXL `qty=0`, `EMPTY`, 미체결 주문 0건 상태를 반영했다.
  - FINAL 사양에 현재 점수 구성과 WATCH 50 / B 72 / A 82 / S 90 표시 등급을 반영했다.
  - 과거 전략·백테스트·결정 기록에 Archive 경계를 강화하고 Telegram의 현재 15건 타임라인·3카드 가이드를 D-011로 기록했다.
  - 배포 가이드에 `env -u GITHUB_TOKEN ./deploy.sh`, 실제 shared DB·로그·캐시 경로와 dry-run 운영 스냅샷을 반영했다.
  - Codex·ChatGPT·Antigravity가 동일한 문서 읽기 순서와 브랜치 인수인계 규칙을 사용하도록 협업 문서를 정리했다.

## 운영 배포 상태와 남은 게이트

1. JDSS 2.2.2 Oracle dry-run 배포를 완료했다. 현재 SHA는 서버 `current` 링크가 기준이다.
2. 당분간 Telegram `/bt`, `/sgov` 등 조회·검증 중심으로 운용한다.
3. JDSS SQLite의 TQQQ/SOXL은 `qty=0`, `EMPTY`, JDSS 미체결 주문 0건을 유지한다.
4. 향후 live 검토 시 기존 운영 자산과 JDSS 상태의 관계를 먼저 결정하고 Reconciliation을 재실행한다.
5. Reconciliation이 완전히 통과하기 전까지 신규 실주문을 금지하고 `dry_run`을 유지한다.
6. 2.2 장기 SGOV 회귀와 전체 Ruff·pytest·설정·문서 링크 검증, PR #8의 GitHub Actions 검증을 완료했다.
7. 최초 운영 점검에서 Telegram `/sgov`, SGOV 원장 마이그레이션, broker reconciliation 상태를 확인한다.

## 실거래 잠금

배포 준비 및 최초 Oracle 반영은 `JDSS_TRADING_MODE=dry_run`, 빈 `JDSS_LIVE_CONFIRMATION`을 유지한다. 실거래 활성화와 리스크 한도 변경은 별도 사용자 승인과 운영 점검 없이는 수행하지 않는다.

## 마지막 인수인계

- 작성 주체: Codex
- 상태: JDSS 2.2.2 SGOV 현금화 자동 재개·호가 집행 구현, PR #13 `main` 병합, Oracle dry-run 배포·smoke test 완료. live 승격 금지.
- JDSS 2.2 구현 커밋: `aaab561` (`Add JDSS 2.2 SGOV cash management`)
- JDSS 2.2 main 병합 커밋: `c86ca23` (PR #8)
- main 반영 커밋: `2b209e8` (`docs: record FINAL contract audit`)
- 액션 갱신 커밋: `0e685cf` (`ci: upgrade workflows to Node 24 actions`)
- 백테스트 전용 운영 커밋: `f48b118` (`docs: set dry-run backtest-only operations`)
- Telegram 지표 설명 커밋: `b9dd21c` (`feat: clarify Telegram score indicators`)
- 문서 인수인계 정리 커밋: `20e3315` (`docs: finalize cross-environment handoff`)
- 신호 DB 수정: PR #10, main `ac1e49b`
- JDSS 2.2.1 릴리스: PR #11, main `3de66d1`
- JDSS 2.2.2 릴리스: PR #13, main·Oracle `a98b671`
- JDSS 2.2.2 배포 기록: PR #14, main `cdedd4b`
- GitHub CI: PR #13의 CI run `31397181235` 및 JDSS 2.2 Dry Run `31397181217` 성공
- Oracle dry-run 배포: `a98b6717f70d7adca0f118b93de452fccc342dc1` 배포 완료
- 배포 후 검증: `jd_holdings_bot` active, 패키지 2.2.2, 전략 `JDSS-2.2.2-SGOV`, config 2.2.2, `dry_run`, 빈 live 확인값, Toss TQQQ·SOXL·SGOV 시세·미국장 캘린더 조회 성공
- JDSS SQLite 확인: TQQQ/SOXL `qty=0`, `EMPTY`, JDSS 미체결 주문 0건
- 문서 검증: Git 추적 Markdown 18개, 깨진 로컬 링크 0개, FINAL/Archive 경계 확인
- 운영 방침: Telegram `/bt`, `/sgov` 조회·검증 중심, `dry_run` 유지. 실제 계좌 상태를 JDSS에 자동 인수하거나 주문을 변경하지 않음.
- 다음 우선순위: Telegram에서 SGOV 현금화 의도·자동 재개·취소·재가격 이벤트와 SGOV 원장·broker reconciliation을 관찰하고, 별도 승인 전까지 live 전환하지 않는다.

## 갱신 규칙

작업 종료 시 현재 브랜치, 마지막 커밋, 검증 결과, 완료 작업, 다음 작업을 갱신한다. 다음 작업자가 즉시 이어갈 수 있는 상태만 간결하게 유지한다.
