# JD_HOLDINGS Current Work

> 현재 작업·배포·검증 상태의 단일 기준이다. 전략 수치는 `strategy.yaml`과 `docs/JDSS_FINAL_SPEC.md`, 쉬운 설명은 `docs/STRATEGY_GUIDE.md`, 백테스트 재현은 `docs/BACKTEST_REPORT.md`를 따른다.

## 현재 상태

- 기준 브랜치: `main`
- 최신 전략·운영 코드 SHA: `1519285cec5326d930e41df3a9caaeed421187a2`
- 현재 전략: `JDSS-3.1.0-TWIN-H40-S3` / `3.1.0`
- PR #72에서 실제 production 실행경로를 전략·주문·예외·로그·Telegram 관점으로 재감사하고 운영 안전성을 보강했다.
- 이번 감사에서는 `strategy.yaml`, `src/jd_holdings/core/**`, `src/jd_holdings/backtest/**`를 변경하지 않아 V3.1 전략수식과 production 백테스트 계산을 그대로 유지했다.
- Oracle V3.1 실제 배포와 live 적용은 이번 범위에서 제외했다. Oracle은 아직 V3.0 dry-run으로 간주하며 live 잠금은 유지한다.

## 전략 기준

- 초기 JDSS 관리배정금: `$20,000`
- 코어: QQQ·SOXX 완료 월말 종가와 6개월 이동평균. OFF→ON 첫 달 TQQQ·SOXL 목표 10%, 다음 월말에도 ON이면 15% 유지
- 부스터 자금 상한: 종목당 `$8,000`(초기 배정금 40%)
- S3 정상 최대 신규투입: 3단계 누적 90%이므로 `$7,200`(초기 배정금 36%)
- 분할매수: 40% / 30% / 20%, 누적 40% / 70% / 90%; 최초 체결가 대비 0% / -2% / -5%
- 부스터 진입: 총점 55점 이상, 반등점수 5점 이상, RED 신규매수 차단
- 익절: TP1 평단 +4%에서 약 30%, TP2 +10%에서 잔량
- TP1 이후 기간 기준 잔여청산 OFF, 재매수 OFF, 자동손절 OFF
- SOXL 섹터 가드: SOXX/SMH EMA60 기준 1·3차 차단. 일부 섹터 데이터 누락 시 현재 계약은 이벤트 경고 후 사용 가능한 기준으로 판단
- 유휴자금: JDSS managed cash와 JDSS 관리 SGOV, 현금 버퍼 `$250`
- 매수: 코어·부스터 모두 Telegram 2단계 승인
- 매도: 코어 위험축소 자동, 부스터 TP1·TP2 자동
- live: 잠금 유지

## 전략·백테스트 동등성

- 연구 최종 후보와 main production 통합 백테스트의 핵심 성과는 정확히 일치한다.
- V3.0 → V3.1: Total +678.07% → +1372.96%, CAGR 14.05% → 18.81%, MDD -28.29% → -26.04%, Sharpe 0.835 → 0.940, Sortino 1.004 → 1.197
- V3.1 production 재검증: `JDSS V3 Backtest` run `31559442771`
  - Total +1372.96%, CAGR 18.81%, MDD -26.04%, Sharpe 0.940, 평균노출 31.87%
  - 코어 체결 320건, 부스터 체결 443건, SGOV 추정수익 `$21,538.41`
- production 백테스트 동등성은 신호·점수·코어·익절 계산이 정식 코드에서 재현된다는 뜻이다. Telegram 승인 지연, 호가 미체결·부분체결, API 장애, 서버 재시작 같은 현실 변수는 운영 dry-run에서 별도 검증한다.

## 운영 로직 감사에서 보강한 사항

- 실제 bot entrypoint는 `FinalTradingService`와 `OperationalTelegramBotApp`을 사용한다.
- 코어 BUY가 `REJECTED`/`CANCELED`로 끝나면 유효시간 안에서 같은 월말 신호를 재승인할 수 있게 되돌린다.
- 코어 BUY가 일부 체결 후 취소되면 체결분을 원장에 보존하고 잔여 목표를 다시 승인한다.
- 코어 주문 결과가 `UNKNOWN`이면 성공을 추정하거나 재주문하지 않고 종목을 SAFE_MODE로 전환한다.
- 코어 위험축소 SELL이 거절·UNKNOWN·부분체결 후 종료되면 SAFE_MODE로 전환하고 신규매수를 차단한다.
- 코어 재승인 시 승인 attempt를 주문 ID에 포함해 이전 종료 주문 ID를 재사용하지 않는다.
- 월간 QQQ/SOXX 공통 거래일 데이터가 불완전하면 조용히 건너뛰지 않고 명시적 오류로 기록하며 월말 완료 marker를 남기지 않는다.
- 월간 코어, 일일 부스터 분석, 주문/TP 모니터, SGOV, Reconciliation, 신호만료를 각각 독립 예외처리해 한 작업의 장애가 다른 안전 점검을 굶기지 않는다.
- 시작 Reconciliation 불일치는 봇 시작 직후 Telegram으로 명확히 경고한다.
- 자동운영 오류는 서버 회전 로그 파일(traceback), SQLite `event_logs`, Telegram 요약 알림으로 남긴다.
- 같은 자동오류·같은 Reconciliation 원인은 Telegram에서 10분 동안 반복 알림을 억제하되 DB/파일 로그는 계속 남긴다. 정상 정합성으로 회복된 뒤 같은 문제가 재발하면 다시 알린다.
- PENDING/ PARTIAL_FILLED / REJECTED·CANCELED / UNKNOWN 주문 메시지를 각각 `접수 / 부분체결 / 미완료 / 결과 확인 필요`로 구분한다.
- managed cash/equity, 동시 BUY 예약, 코어 승인 직전 수량 상한, dry-run cash·order sequence 복원, 불명확 재시작 SAFE_MODE 등 PR #70의 안전장치는 그대로 유지한다.

## 검증 상태

- PR #72 최종 head `9b985a0427d7e003e0ee857a7bd8ad4f9cb7c175`
  - CI Quality Gate #421 / run `31567867569`: 성공
  - Ruff: 성공
  - pytest: **168 passed**, 전체 coverage **69%**
  - `jdss validate-config`: `JDSS-3.1.0-TWIN-H40-S3 / 3.1.0` 성공
  - JDSS V3 Dry Run #135 / run `31567867594`: 성공
  - Security #201 / run `31567867568`: 성공
  - PR #72 squash merge SHA `1519285cec5326d930e41df3a9caaeed421187a2`
- 확장된 V3 Dry Run은 정상 E2E 외에 다음 fault-injection을 포함한다.
  - 코어 BUY REJECTED 재승인 및 주문 ID 재사용 방지
  - 코어 BUY PARTIAL 후 CANCELED
  - 코어 BUY UNKNOWN
  - 코어 SELL 즉시/사후 미완료 SAFE_MODE
  - 월간 데이터 공통 거래일 누락
  - 포트폴리오 스케줄러 실패 중 주문 모니터·Reconciliation 계속 수행
  - 운영 오류 DB 영속 및 Telegram rate-limit
  - TP1→TP2, TP2 취소 자동복구, 재시작 후 최종 청산·Reconciliation 정상

## 문서 상태

- `docs/STRATEGY_GUIDE.md`: 고등학생도 이해할 수 있도록 코어·부스터·SGOV·SAFE_MODE를 쉬운 비유로 설명하고 Mermaid 전체 전략 흐름도, 코어 상태 흐름, Telegram 승인 sequence, 오류 격리 흐름을 포함한다.
- `docs/TELEGRAM_BOT_GUIDE.md`: 명령어, 주문 상태별 의미, SAFE_MODE 대응 순서, 시작 정합성 경고, 로그/Telegram 알림 정책을 운영자 관점으로 정리했다.
- `docs/BACKTEST_REPORT.md`: research backtest / production backtest / 실제 운영 dry-run을 구분하고 production parity와 실제 체결 차이를 명시했다.
- `docs/JDSS_FINAL_SPEC.md`와 `strategy.yaml`은 전략수식 변경이 없어 기존 V3.1 공식 계약을 유지한다.

## 배포 상태

- GitHub main V3.1 최신 전략·운영 코드 SHA: `1519285cec5326d930e41df3a9caaeed421187a2`
- 기존 GitHub Release: `v3.0.0`, target `df640d16c485b770d9c57c570f5a13b3bdb4e2de`
- Oracle 배포 SHA: `df640d16c485b770d9c57c570f5a13b3bdb4e2de` — 아직 V3.0 dry-run
- `jd_holdings_bot` Oracle 서비스는 V3.1 배포 성공이 확인되기 전까지 V3.0 코드로 간주한다.

## live 전 남은 필수사항

1. V3.1을 Oracle에 dry-run 배포한 뒤 실제 서비스 프로세스에서 PENDING, PARTIAL_FILLED, UNKNOWN, 프로세스 kill/restart, Reconciliation 불일치를 fault-injection으로 검증한다.
2. 코어 위험축소 SELL의 미체결·부분체결·취소 후 자동 재가격/재제출 정책은 Toss 실제 주문상태와 API 동작을 확인한 뒤 별도 보강한다. 현재는 불완전 위험축소를 SAFE_MODE로 멈추는 보수적 정책이다.
3. live 운영계좌는 개인 TQQQ·SOXL과 분리한다. 브로커 수준에서 소유권을 증명할 수 없는 동일티커 혼합은 지원하지 않는다.
4. 향후 JDSS 초기 배정금 변경 기능을 넣는다면 입출금·증감 이력을 영속하는 capital-allocation 원장을 추가한다.
5. live 잠금은 위 검증과 별도 승인 전까지 유지한다.

## 다음 작업

1. `Deploy Oracle Dry Run`으로 V3.1을 실제 Oracle 서비스에 적용한다.
2. 배포 성공 후 서비스 active, Telegram V3.1 출력, managed cash/equity, Reconciliation, Toss read-only smoke를 확인한다.
3. Oracle 프로세스에서 주문·부분체결·UNKNOWN·재시작 fault-injection을 수행해 GitHub Actions의 방어 로직이 서버에서도 같은 방식으로 동작하는지 확인한다.
4. 전략 성과 판단은 production 백테스트를 기준으로 하고, live는 계속 금지한다.

## 작업 종료 갱신 규칙

작업 종료 시 활성 브랜치, 최신 전략·운영 코드 SHA, 검증 결과, 배포 SHA와 다음 작업을 갱신한다. 완료 이력 전체를 길게 누적하지 않고 다음 작업자가 바로 이어가는 데 필요한 현재 상태만 유지한다.
