# JH_HOLDINGS Current Work

> 현재 전략·개발·배포·검증 상태의 단일 상태판입니다. 이전 값을 교체하는 롤링 문서이며, 상세 전략과 승인된 기준 백테스트는 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 공식 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md)를 따릅니다.

## 현재 릴리즈와 운영

- GitHub 저장소: **`hwanff11/JH_HOLDINGS`** (public)
- 공식 릴리즈: **`v3.2.2`**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- Oracle 런타임: **`/home/ubuntu/JH_HOLDINGS`**
- Oracle systemd: **`jh_holdings_bot` active**
- Oracle 런타임 마이그레이션 완료·검증 SHA: **`7eb74b12d9797c1882f7952265141ef81040a09e`**
- 마이그레이션 백업: **`/home/ubuntu/JD_HOLDINGS.migration-backup-20260821T000403Z`** (보존)
- 구 `jd_holdings_bot`: **disabled / retired**
- live: **LOCKED OFF**
- Oracle 환경: **`JDSS_TRADING_MODE=dry_run` / `JDSS_LIVE_CONFIRMATION` empty**
- 실제 Toss API: read-only smoke 인증·QQQ/TQQQ/SOXL 시세·시장일 조회 성공

## 현재 개발 목표

`hardening/pre-live-operations`에서 실거래 전 운영·보안 마감 작업을 진행합니다. 전략 수익 로직은 바꾸지 않고 다음 안전 경계를 강화합니다.

1. 최초진입 Telegram의 오래된 단계 버튼을 현재 DB 단계와 묶어 재사용 차단
2. `/help`, Telegram 가이드, 공식 사양의 최초진입 계약 동기화
3. Oracle 배포를 release별 가상환경 + DB 백업 + atomic switch + 자동 rollback 구조로 강화
4. SSH `accept-new` 제거 및 검증된 `known_hosts` 고정
5. 완료된 Oracle 이름변경 migration workflow/script 제거
6. Security workflow의 CodeQL 업로드와 CI coverage 하한 추가
7. GitHub `main` branch protection/ruleset을 필수 운영조건으로 명시
8. public 저장소의 비밀정보·배포정보 노출 경계를 다시 점검

## 현재 안전장치

- `strategy.yaml`의 `portfolio.live_enabled=false`
- 런타임 live hard lock과 빈 live confirmation
- 위험증가 BUY는 최신 가격·수량 검토 후 60초 최종 승인
- 위험축소 SELL은 자동이지만 미완료·UNKNOWN이면 신규 BUY 차단
- 주문 client ID 멱등성, 브로커 응답 종목·방향·수량 검증, 부분체결 delta 반영
- 시작·주기 reconciliation 불일치 시 sticky SAFE_MODE
- 실제 Toss read-only preflight와 forced dry-run 모의원장을 자동 혼합하지 않음
- 최초진입 50% → 75% → 100%, 단계별 전량 체결 후 최소 3 미국 거래일, 단계 개방은 운영자 확인 필요
- 배포 전 정확한 최신 `main`, 설정, 테스트, dry-run 잠금 검증

## live 차단 조건

현재도 live 전환은 승인하지 않습니다. 다음 항목을 모두 끝내기 전까지 잠금을 유지합니다.

- pre-live hardening PR 전체 CI/Security 성공
- GitHub `main` branch protection/ruleset 활성화
- Oracle SSH host key를 신뢰 경로로 확인해 Actions `ORACLE_SSH_KNOWN_HOSTS`에 등록
- 강화된 배포 스크립트로 forced dry-run 재배포 및 rollback-safe smoke 확인
- `/help` → `/onboarding` → 단계 버튼 → BUY 2단계 승인 → 주문감시 → 재시작 → reconciliation 리허설
- 실제 Toss 관리 티커 기존 보유·열린 주문·주문가능금액의 live 전환 계획 확정
- 실제 주문 어댑터/회계/migration 리허설 검증과 별도 명시적 live 승인

## 바로 다음 작업

1. pre-live hardening 변경을 PR로 묶고 Quality Gate·Security를 통과시킵니다.
2. GitHub `main` 보호와 Oracle SSH known_hosts 값을 운영 설정에 반영합니다.
3. merge 후 Oracle에 forced dry-run으로 재배포하고 runtime verifier를 실행합니다.
4. 모든 운영 리허설이 끝날 때까지 live 잠금을 유지합니다.
