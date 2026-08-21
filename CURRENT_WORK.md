# JH_HOLDINGS Current Work

> 현재 전략·개발·배포·검증 상태의 단일 상태판입니다. 이전 값을 교체하는 롤링 문서이며, 상세 전략과 승인된 기준 백테스트는 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 공식 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md)를 따릅니다.

## 현재 릴리즈와 운영

- GitHub 저장소: **`hwanff11/JH_HOLDINGS`** (public)
- 공식 릴리즈: **`v3.2.2`**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- Oracle 런타임: **`/home/ubuntu/JH_HOLDINGS`**
- Oracle systemd: **`jh_holdings_bot` active**
- 마지막 검증 완료 runtime SHA: **`3934c30351508921bf8f8c7f9ac2207e586cbf62`**
- 마지막 forced dry-run 배포: **Actions run [`32494248302`](https://github.com/hwanff11/JH_HOLDINGS/actions/runs/32494248302) 성공**
- 배포 DB snapshot: **`/home/ubuntu/JH_HOLDINGS/shared/backups/jdss-20260821T145153Z-b598999916c006e0d50bdb64a9c83e63927d5fb7.db`**
- 구 런타임 백업: **`/home/ubuntu/JD_HOLDINGS.migration-backup-20260821T000403Z`** (보존)
- 구 `jd_holdings_bot`: **disabled / retired**
- live: **LOCKED OFF**
- Oracle 환경: **`JDSS_TRADING_MODE=dry_run` / `JDSS_LIVE_CONFIRMATION` empty**
- 설정 잠금: **`portfolio.live_enabled=false`**
- 실제 Toss API: read-only smoke 인증·QQQ/TQQQ/SOXL 시세·시장일 조회 성공
- runtime verifier: **Actions run [`32494604393`](https://github.com/hwanff11/JH_HOLDINGS/actions/runs/32494604393) 성공 / `regular` / `PASS_NO_RESTART`**

문서 전용 커밋은 Oracle 프로그램 동작을 바꾸지 않으므로 runtime SHA와 최신 `main` SHA가 일시적으로 다를 수 있습니다. 코드·설정·workflow가 바뀌면 최신 `main`을 다시 forced dry-run 배포하고 이 상태판을 갱신합니다.

## 완료된 pre-live hardening

- 최초진입 Telegram 버튼을 기대 DB 단계와 결합해 오래된 버튼 재사용 차단
- `/help`, Telegram 가이드와 공식 사양의 onboarding 계약 동기화
- 최초진입 `/onboarding` 메뉴·가이드 노출, 명확한 매수 검토/실행 버튼, 시작일 50→75→100 백테스트를 PR [#168](https://github.com/hwanff11/JH_HOLDINGS/pull/168)로 병합
- PR #168 최종 Quality Gate [`32493643477`](https://github.com/hwanff11/JH_HOLDINGS/actions/runs/32493643477), Security Gate [`32493643526`](https://github.com/hwanff11/JH_HOLDINGS/actions/runs/32493643526), Backtest [`32493643478`](https://github.com/hwanff11/JH_HOLDINGS/actions/runs/32493643478) 성공
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

최초진입 계약의 사용자 화면·백테스트 일치 변경을 Oracle forced dry-run runtime에 배포했습니다. Telegram 명령 메뉴와 `/guide`에 `/onboarding`을 노출하고, 매수 검토·최종 실행 버튼을 구분하며, 요청 시작일 기준 50% → 75% → 100% 백테스트를 검증했습니다. 배포 SHA·release venv·서비스 active·Toss read-only·Telegram outbound smoke를 확인했으며 live 잠금은 해제하지 않았습니다.

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
- GitHub `main` classic branch protection 활성: PR 필수, Quality Gate·Security Gate·Backtest 필수, 대화 해결·linear history 필수, 관리자 우회·force push·삭제 금지

## 자동 운영 검증

- runtime verifier는 pinned SSH·배포 SHA·release venv·forced dry-run·service active·Toss read-only smoke를 확인합니다.
- 수동 ChatOps 검증에서는 Telegram `getMe`와 관리자 채팅의 무음 테스트 메시지 전송·삭제까지 확인합니다. 명령별 handler·권한·문구는 focused runtime 테스트가 담당합니다.
- 매주 미국 금요일 after-hours 종료 뒤 자동 verifier가 실행되며, 시장 phase가 `closed`일 때만 systemd restart/recovery를 수행합니다. 장중·pre-market·after-hours에는 재시작하지 않습니다.
- Quality Gate·Security Gate·Backtest는 병합 필수 이름을 유지하면서 문서-only·비전략 변경 fast path를 사용하고, 새 commit이 올라오면 오래된 실행을 취소합니다.

## live 전환 전에만 남아 있는 항목

- 실제 Toss 관리 티커 기존 보유·열린 주문·주문가능금액의 live 전환 계획 확정
- 실제 주문 어댑터·회계·migration 리허설과 별도 명시적 live 승인
- 충분한 forced dry-run soak와 운영자 최종 확인

## 바로 다음 작업

1. Telegram에서 명령 메뉴·`/guide`·`/onboarding`과 매수 버튼 문구를 운영자 화면으로 확인합니다.
2. `/backtest`의 시작일 50% → 75% → 100% 안내와 실행 결과를 확인합니다.
3. 실제 계좌 전환 계획과 별도 live 승인 전까지 live 잠금을 해제하지 않습니다.
