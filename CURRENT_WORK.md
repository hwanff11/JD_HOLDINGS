# JD_HOLDINGS Current Work

> 현재 작업·배포·검증 상태의 단일 기준이다. 전략 수치는 `strategy.yaml`과 `docs/JDSS_FINAL_SPEC.md`, 쉬운 설명은 `docs/STRATEGY_GUIDE.md`, 백테스트 재현은 `docs/BACKTEST_REPORT.md`를 따른다.

## 현재 상태

- 기준 브랜치: `main`
- 현재 전략: `JDSS-3.1.0-TWIN-H40-S3` / config `3.1.0`
- 현재 runtime 코드 SHA: `dc168a18f6cb6a91ffedab1246d30d825f8e5ef1`
- Oracle 배포 SHA: `dc168a18f6cb6a91ffedab1246d30d825f8e5ef1`
- Oracle `jd_holdings_bot`: **V3.1 forced dry-run**, 실제 systemd restart/fault-injection 검증 완료
- live 잠금 유지

## 전략 기준

- 초기 JDSS 관리배정금: `$20,000`
- 코어: QQQ·SOXX 완료 월말 종가와 6개월 이동평균. OFF→ON 첫 달 TQQQ·SOXL 목표 10%, 다음 월말에도 ON이면 15% 유지
- 부스터 자금 상한: 종목당 `$8,000`
- S3 분할매수: 40% / 30% / 20%, 누적 40% / 70% / 90%; 최초 체결가 대비 0% / -2% / -5%
- 부스터 진입: 총점 55점 이상, 반등점수 5점 이상, RED 신규매수 차단
- 익절: TP1 평단 +4%에서 약 30%, TP2 +10%에서 잔량
- TP1 이후 기간 기준 잔여청산 OFF, 재매수 OFF, 자동손절 OFF
- SOXL 섹터 가드: SOXX/SMH EMA60 기준 1·3차 차단
- 유휴자금: JDSS managed cash와 JDSS 관리 SGOV, 현금 버퍼 `$250`
- 매수: 코어·부스터 모두 Telegram 2단계 승인
- 매도: 코어 위험축소 자동, 부스터 TP1·TP2 자동

## 전략·백테스트 동등성

- post-runtime-audit main production 백테스트 run `31568228798`: 성공
  - Total +1372.96%
  - CAGR 18.81%
  - MDD -26.04%
  - Sharpe 0.940
  - 평균노출 31.87%
  - 코어 체결 320건 / 부스터 체결 443건
- 이는 전략 계산의 동등성을 뜻한다. 승인 지연·미체결·부분체결·API 장애·재시작 같은 현실 변수는 아래 Oracle dry-run 검증으로 별도 확인한다.

## 운영 안전장치

- 실제 bot entrypoint: `FinalTradingService` + `OperationalTelegramBotApp`
- 코어 BUY `REJECTED`/`CANCELED`: 유효시간 내 재승인
- 코어 BUY 부분체결 후 취소: 체결분 보존, 잔여 목표만 재승인
- BUY 결과 `UNKNOWN`: 성공을 추정하거나 재주문하지 않고 SAFE_MODE
- 코어 위험축소 SELL 거절·UNKNOWN·부분체결 종료: SAFE_MODE + 신규매수 차단
- 월간 코어, 일일 부스터, 주문/TP 감시, SGOV, Reconciliation은 작업별 예외 격리
- 운영 오류: 회전 파일 로그(traceback) + SQLite `event_logs` + Telegram 요약
- 동일 Telegram 오류 10분 rate-limit, DB/파일 로그는 계속 기록
- PENDING / PARTIAL_FILLED / REJECTED·CANCELED / UNKNOWN을 `접수 / 부분체결 / 미완료 / 결과 확인 필요`로 구분
- managed cash/equity와 미체결 BUY 예약을 사용해 개인 현금까지 전략자금으로 쓰지 않음
- 개인 TQQQ/SOXL과 JDSS TQQQ/SOXL의 동일 계좌 혼합은 지원하지 않음

## GitHub 검증 상태

- PR #72 운영 로직 감사: merge `1519285cec5326d930e41df3a9caaeed421187a2`
- PR #77 Oracle runtime verifier: merge `a2261f37e608706340373dac489082fa3d18781e`
- PR #79 Oracle yfinance writable cache 보강: merge `dc168a18f6cb6a91ffedab1246d30d825f8e5ef1`
  - CI #430: SUCCESS
  - JDSS V3 Dry Run #137: SUCCESS
  - Security #210: SUCCESS
  - 배포 사전 전체 pytest: **170 passed**
- 전략수식·주문판단·백테스트 계산은 PR #79에서 변경하지 않았다.

## Oracle 배포·실제 프로세스 검증

### 최신 배포

- Deploy Oracle Dry Run run `31572957555`: **SUCCESS**
- 배포 SHA: `dc168a18f6cb6a91ffedab1246d30d825f8e5ef1`
- `JDSS-3.1.0-TWIN-H40-S3 / 3.1.0` config validation 성공
- systemd: `jd_holdings_bot`
- 모드: **forced `dry_run`**, live confirmation empty
- Toss read-only smoke: 인증/가격조회 정상

### 실제 Oracle runtime 검증

최종 Verify Oracle V3.1 Runtime run `31573086250`: **SUCCESS**

- 미국장 `closed` 확인 후에만 실제 서비스 재시작 수행
- 재시작 전 safety state: clean
- 실제 PID: `199138 -> 199267`
- 재시작 전후 positions/core/SGOV/trades/open-orders fingerprint: **동일**
- startup Reconciliation age: `29.8s`, SAFE_MODE: clear
- Oracle 서버 격리환경에서 production fault-injection: **20 tests passed**
  - 코어 BUY rejected / partial / unknown
  - 불완전 core sell SAFE_MODE
  - runtime 오류·Telegram rate-limit
  - cold restart/order sequence/managed account 안전성
- 서버 log/journal 정상
- Telegram transport: 정상, configured recipient 1개에 검증 메시지 전달
- Toss read-only smoke 재통과
- 최종 결과: `ORACLE_V31_RUNTIME_VERIFY=PASS`

### 검증 중 발견·수정한 사항

- 1차 Oracle runtime run `31572203952` 자체는 PASS했지만, systemd `ProtectHome=read-only` 환경에서 yfinance가 기본 홈 cache를 쓰지 못해 TzCache/CookieCache INFO 경고를 남겼다.
- PR #79에서 yfinance 내부 cache를 `JDSS_CACHE_PATH/yfinance`로 이동했다.
- 최신 runtime을 재배포한 뒤 최종 run `31573086250`의 새 프로세스 journal에서는 해당 cache 실패 경고가 재발하지 않았다.

## 문서 상태

- `docs/STRATEGY_GUIDE.md`: 코어·부스터·SGOV·SAFE_MODE를 쉬운 설명과 Mermaid 흐름도로 정리
- `docs/TELEGRAM_BOT_GUIDE.md`: 주문 상태·SAFE_MODE·오류 알림·운영자 행동 순서 정리
- `docs/BACKTEST_REPORT.md`: research / production / 실제 운영 dry-run 차이와 parity 기준 정리
- `docs/JDSS_FINAL_SPEC.md` + `strategy.yaml`: V3.1 공식 전략 계약

## live 전 남은 필수사항

1. 실제 Toss 주문에서 발생하는 `PARTIAL_FILLED`/`UNKNOWN`의 API 상태변화와 체결 이벤트를 실주문 전용 검증계정 또는 별도 승인된 방식으로 관찰한다.
2. 코어 위험축소 SELL의 미체결·부분체결·취소 후 자동 재가격/재제출 정책은 Toss 실제 동작을 확인한 뒤 결정한다. 현재는 안전하게 SAFE_MODE로 멈춘다.
3. live 운영계좌는 개인 TQQQ·SOXL과 분리한다.
4. live 잠금은 별도 승인·검증 전까지 유지한다.

## 다음 작업

- V3.1 Oracle dry-run은 현재 정상 운영·검증 완료 상태다.
- 당분간 Telegram 신호·주문·Reconciliation·로그를 dry-run으로 관찰한다.
- 전략 연구를 재개할 경우 production 백테스트를 기준으로 비교한다.
- live 전환은 위 남은 필수사항을 해결한 뒤 별도 결정한다.

## 작업 종료 갱신 규칙

작업 종료 시 전략·운영 코드 SHA, Oracle 배포 SHA, 검증 결과와 다음 작업을 갱신한다. 완료 이력 전체를 길게 누적하지 않고 다음 작업자가 바로 이어갈 정보만 유지한다.
