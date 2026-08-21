# JH_HOLDINGS Current Work

> 현재 전략·개발·배포·검증 상태의 단일 상태판입니다. 이전 값을 교체하는 롤링 문서이며, 상세 전략과 승인된 기준 백테스트는 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 공식 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md)를 따릅니다.

## 현재 릴리즈와 운영

- GitHub 저장소: **`hwanff11/JH_HOLDINGS`** (public)
- 공식 릴리즈: **`v3.2.2`**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- Oracle 런타임: **`/home/ubuntu/JH_HOLDINGS`**
- Oracle systemd: **`jh_holdings_bot` active**
- 마지막 검증 완료 runtime SHA: **`8948c5de6f7cc1110340f7ee7b87b2e272742922`**
- 마지막 forced dry-run 배포: **Actions run [`32480280389`](https://github.com/hwanff11/JH_HOLDINGS/actions/runs/32480280389) 성공**
- 배포 DB snapshot: **`/home/ubuntu/JH_HOLDINGS/shared/backups/jdss-20260821T120825Z-7a90e983baf85e38f0672dfb1f5f5598dac313d1.db`**
- 구 런타임 백업: **`/home/ubuntu/JD_HOLDINGS.migration-backup-20260821T000403Z`** (보존)
- 구 `jd_holdings_bot`: **disabled / retired**
- live: **LOCKED OFF**
- Oracle 환경: **`JDSS_TRADING_MODE=dry_run` / `JDSS_LIVE_CONFIRMATION` empty**
- 설정 잠금: **`portfolio.live_enabled=false`**
- 실제 Toss API: read-only smoke 인증·QQQ/TQQQ/SOXL 시세·시장일 조회 성공
- runtime verifier: **Actions run [`32480499360`](https://github.com/hwanff11/JH_HOLDINGS/actions/runs/32480499360) 성공 / `pre_market` / `PASS_NO_RESTART`**

문서 전용 커밋은 Oracle 프로그램 동작을 바꾸지 않으므로 runtime SHA와 최신 `main` SHA가 일시적으로 다를 수 있습니다. 코드·설정·workflow가 바뀌면 최신 `main`을 다시 forced dry-run 배포하고 이 상태판을 갱신합니다.

## 완료된 pre-live hardening

- 최초진입 Telegram 버튼을 기대 DB 단계와 결합해 오래된 버튼 재사용 차단
- `/help`, Telegram 가이드와 공식 사양의 onboarding 계약 동기화
- release별 `.venv`, SQLite snapshot, atomic `current` switch와 자동 rollback 배포 적용
- `accept-new`·runner 즉석 신뢰를 제거하고 `ORACLE_SSH_KNOWN_HOSTS` 고정 + `StrictHostKeyChecking=yes` 적용
- 완료된 Oracle 이름변경 migration workflow/script 제거
- CI coverage 하한, CodeQL 업로드, 안정적인 Quality Gate·Security Gate·Backtest check 적용
- 공개 저장소의 비밀정보·배포정보 노출 경계 점검
- PR [#151](https://github.com/hwanff11/JH_HOLDINGS/pull/151), [#153](https://github.com/hwanff11/JH_HOLDINGS/pull/153), [#155](https://github.com/hwanff11/JH_HOLDINGS/pull/155), [#157](https://github.com/hwanff11/JH_HOLDINGS/pull/157) 병합
- 최종 코드 SHA의 Quality Gate [`32477547949`](https://github.com/hwanff11/JH_HOLDINGS/actions/runs/32477547949), Security Gate [`32477547970`](https://github.com/hwanff11/JH_HOLDINGS/actions/runs/32477547970), Backtest [`32477547963`](https://github.com/hwanff11/JH_HOLDINGS/actions/runs/32477547963) 성공
- pinned SSH trust, release-local venv, DB snapshot, atomic switch, rollback safeguard, service active, forced dry-run, Toss read-only smoke를 배포 run에서 확인
- hashed `known_hosts` 호환 runtime verifier에서 배포 SHA·release venv·live 잠금·service active·설정 검증·Toss read-only smoke와 focused SAFE_MODE/reconciliation 테스트 성공

## 현재 개발 목표

전략 수익 로직은 동결하고 forced dry-run 운영 리허설과 live 차단 조건 마감을 진행합니다. 배포 성공과 live 활성화는 별도 결정이며, 현재 작업은 live 잠금을 해제하지 않습니다.

## 현재 안전장치

- `strategy.yaml`의 `portfolio.live_enabled=false`
- 런타임 live hard lock과 빈 live confirmation
- 위험증가 BUY는 최신 가격·수량 검토 후 60초 최종 승인
- 위험축소 SELL은 자동이지만 미완료·UNKNOWN이면 신규 BUY 차단
- 주문 client ID 멱등성, 브로커 응답 종목·방향·수량 검증, 부분체결 delta 반영
- 시작·주기 reconciliation 불일치 시 sticky SAFE_MODE
- 실제 Toss read-only preflight와 forced dry-run 모의원장을 자동 혼합하지 않음
- 최초진입 50% → 75% → 100%, 단계별 전량 체결 후 최소 3 미국 거래일, 단계 개방은 운영자 확인 필요
- 배포 workflow는 정확한 최신 `main`만 받아 pinned SSH·강제 dry-run·rollback-safe smoke를 검증

## 아직 확인할 항목

- runtime verifier 실행 시 시장 phase가 `pre_market`이어서 안전규칙에 따라 실제 systemd restart/recovery 단계는 생략됨. 닫힌 시장에서 재실행 필요
- Telegram 프로세스와 라이브러리는 서비스에 포함되어 있으나 실제 `/ping`, `/help`, `/portfolio`, `/onboarding`, `/account`, `/order`, `/errors` 왕복은 별도 운영 리허설 필요
- GitHub `main`에서 Quality Gate·Security Gate·Backtest를 강제하는 branch protection/ruleset 최종 확인·활성화
- 실제 Toss 관리 티커 기존 보유·열린 주문·주문가능금액의 live 전환 계획 확정
- 실제 주문 어댑터·회계·migration 리허설과 별도 명시적 live 승인

## 바로 다음 작업

1. 닫힌 시장에서 runtime verifier를 다시 실행해 systemd restart/recovery를 완료합니다.
2. Telegram `/ping`, `/help`, `/portfolio`, `/onboarding`, `/account`, `/order`, `/errors` 명령 왕복을 확인합니다.
3. GitHub `main` 필수 check 보호를 확정하고 forced dry-run soak를 계속합니다.
4. 모든 운영 리허설과 별도 승인이 끝날 때까지 live 잠금을 유지합니다.
