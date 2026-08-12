# JD_HOLDINGS Current Work

> 현재 작업·배포·검증 상태의 단일 기준이다. 전략 수치는 `strategy.yaml`과 `docs/JDSS_FINAL_SPEC.md`, 쉬운 설명은 `docs/STRATEGY_GUIDE.md`, 백테스트 재현은 `docs/BACKTEST_REPORT.md`를 따른다.

## 현재 상태

- 기준 브랜치: `main`
- 현재 전략: `JDSS-3.1.0-TWIN-H40-S3` / config `3.1.0`
- 전략·운영 코드 기준 SHA: `1519285cec5326d930e41df3a9caaeed421187a2` (PR #72)
- Oracle에 배포한 main SHA: `08b3fa57ea4015a393de992d7319fd009b2fdb53`
- Oracle `jd_holdings_bot`은 **V3.1 forced dry-run**으로 배포 완료했다.
- live 잠금은 유지한다.

## 전략 기준

- 초기 JDSS 관리배정금: `$20,000`
- 코어: QQQ·SOXX 완료 월말 종가와 6개월 이동평균. OFF→ON 첫 달 TQQQ·SOXL 목표 10%, 다음 월말에도 ON이면 15% 유지
- 부스터 자금 상한: 종목당 `$8,000`(초기 배정금 40%)
- S3 분할매수: 40% / 30% / 20%, 누적 40% / 70% / 90%; 최초 체결가 대비 0% / -2% / -5%
- 부스터 진입: 총점 55점 이상, 반등점수 5점 이상, RED 신규매수 차단
- 익절: TP1 평단 +4%에서 약 30%, TP2 +10%에서 잔량
- TP1 이후 기간 기준 잔여청산 OFF, 재매수 OFF, 자동손절 OFF
- SOXL 섹터 가드: SOXX/SMH EMA60 기준 1·3차 차단
- 유휴자금: JDSS managed cash와 JDSS 관리 SGOV, 현금 버퍼 `$250`
- 매수: 코어·부스터 모두 Telegram 2단계 승인
- 매도: 코어 위험축소 자동, 부스터 TP1·TP2 자동

## 전략·백테스트 동등성

- 연구 최종 후보와 main production 통합 백테스트 핵심 성과는 정확히 일치한다.
- post-runtime-audit 현재 main 재검증: `JDSS V3 Backtest` run `31568228798` 성공
  - Total +1372.96%
  - CAGR 18.81%
  - MDD -26.04%
  - Sharpe 0.940
  - 평균노출 31.87%
  - 코어 체결 320건 / 부스터 체결 443건
- 이는 전략 계산의 동등성을 뜻하며, Telegram 승인 지연·미체결·부분체결·API 장애·프로세스 재시작 같은 현실 변수는 운영 dry-run에서 별도 검증한다.

## 운영 로직 감사 완료 상태

- 실제 bot entrypoint는 `FinalTradingService` + `OperationalTelegramBotApp`을 사용한다.
- 코어 BUY `REJECTED`/`CANCELED`는 유효시간 내 재승인 가능하다.
- 코어 BUY 일부체결 후 취소는 체결분을 보존하고 잔여 목표만 다시 승인한다.
- BUY 결과 `UNKNOWN`은 재주문하지 않고 SAFE_MODE로 전환한다.
- 코어 위험축소 SELL이 거절·UNKNOWN·부분체결 후 종료되면 SAFE_MODE로 전환하고 신규매수를 막는다.
- 월간 코어, 일일 부스터, 주문/TP 감시, SGOV, Reconciliation은 작업별 예외처리로 분리했다.
- 자동운영 오류는 회전 파일 로그(traceback), SQLite `event_logs`, Telegram 요약 알림으로 남긴다.
- 같은 원인의 Telegram 오류는 10분 rate-limit을 적용하되 DB/파일 로그는 계속 기록한다.
- PENDING / PARTIAL_FILLED / REJECTED·CANCELED / UNKNOWN 메시지를 `접수 / 부분체결 / 미완료 / 결과 확인 필요`로 구분한다.

## 검증 상태

- PR #72: 운영 안전성 감사·보강 완료, merge `1519285cec5326d930e41df3a9caaeed421187a2`
  - CI Quality Gate #421: 성공
  - pytest: **168 passed**, coverage **69%**
  - JDSS V3 Dry Run #135: 성공
  - Security #201: 성공
- fault-injection에는 코어 BUY REJECTED/PARTIAL/UNKNOWN, 코어 SELL 미완료 SAFE_MODE, 월간 데이터 누락, 스케줄러 오류 격리, 로그/Telegram rate-limit, TP 복구와 재시작 정합성을 포함한다.
- 현재 main production 백테스트 run `31568228798`: 성공, 기존 V3.1 핵심 성과 정확히 재현.

## Oracle 배포 상태

- Deploy Oracle Dry Run run `31571151100`: **SUCCESS**
- 배포 SHA: `08b3fa57ea4015a393de992d7319fd009b2fdb53`
- 기존 Oracle `jd-holdings 3.0.0` 제거 후 `3.1.0` 설치 성공
- 전략 버전 변경 사전점검: `3.0.0 -> 3.1.0` 통과
- `jdss validate-config`: `JDSS-3.1.0-TWIN-H40-S3 / 3.1.0` 성공
- systemd 서비스: `jd_holdings_bot`
- 운용 모드: **forced `dry_run`**
- Toss read-only smoke: 인증 성공, TQQQ/SOXL/SGOV 가격조회 정상
- 배포 스크립트 최종 결과: `jd_holdings_bot, forced dry_run, smoke OK`
- 배포 추적 Issue #75: 완료 처리

## 문서 상태

- `docs/STRATEGY_GUIDE.md`: 코어·부스터·SGOV·SAFE_MODE를 쉬운 설명과 Mermaid 흐름도로 정리
- `docs/TELEGRAM_BOT_GUIDE.md`: 주문 상태·SAFE_MODE·오류 알림·운영자 행동 순서 정리
- `docs/BACKTEST_REPORT.md`: research / production / 실제 운영 dry-run 차이와 parity 기준 정리
- `docs/JDSS_FINAL_SPEC.md`와 `strategy.yaml`: V3.1 공식 전략 계약 유지

## live 전 남은 필수사항

1. 실제 Oracle 프로세스에서 PENDING, PARTIAL_FILLED, UNKNOWN, 프로세스 kill/restart, Reconciliation 불일치 fault-injection을 검증한다.
2. 코어 위험축소 SELL의 미체결·부분체결·취소 후 자동 재가격/재제출 정책은 Toss 실제 주문상태를 확인한 뒤 별도 판단한다. 현재는 SAFE_MODE로 멈춘다.
3. live 운영계좌는 개인 TQQQ·SOXL과 분리한다. 브로커 수준에서 소유권을 증명할 수 없는 동일티커 혼합은 지원하지 않는다.
4. live 잠금은 위 검증과 별도 승인 전까지 유지한다.

## 다음 작업

1. Oracle V3.1 dry-run 서비스를 실제 프로세스 기준으로 관찰한다.
2. 부분체결·UNKNOWN·재시작·Reconciliation fault-injection을 수행한다.
3. Telegram V3.1 출력과 로그/DB 이벤트를 함께 확인한다.
4. 전략 성과 판단은 production 백테스트를 기준으로 하며 live는 계속 금지한다.

## 작업 종료 갱신 규칙

작업 종료 시 활성 브랜치, 전략·운영 코드 기준 SHA, 검증 결과, Oracle 배포 SHA와 다음 작업을 갱신한다.