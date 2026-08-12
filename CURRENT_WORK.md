# JD_HOLDINGS Current Work

> 현재 작업·배포·검증 상태의 단일 기준이다. 전략 수치는 `strategy.yaml`과 `docs/JDSS_FINAL_SPEC.md`, 쉬운 설명은 `docs/STRATEGY_GUIDE.md`, 백테스트 재현은 `docs/BACKTEST_REPORT.md`를 따른다.

## 현재 상태

- 기준 브랜치: `main`
- 현재 전략: `JDSS-3.1.0-TWIN-H40-S3` / config `3.1.0`
- 현재 runtime 코드 SHA: `9231a0513e28491e3e993bcf5144936c9b7634b0`
- Oracle 배포 SHA: `9231a0513e28491e3e993bcf5144936c9b7634b0`
- Oracle `jd_holdings_bot`: **V3.1 forced dry-run**
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
- 전략 계산의 동등성을 뜻하며 승인 지연·미체결·API 장애·재시작 같은 현실 변수는 Oracle dry-run에서 별도 검증한다.

## 운영 안전장치

- 실제 bot entrypoint: `FinalTradingService` + `OperationalTelegramBotApp`
- BUY 결과 `UNKNOWN`: 성공을 추정하거나 재주문하지 않고 SAFE_MODE
- 코어 위험축소 SELL 거절·UNKNOWN·부분체결 종료: SAFE_MODE + 신규매수 차단
- 월간 코어, 일일 부스터, 주문/TP 감시, SGOV, Reconciliation은 작업별 예외 격리
- 운영 오류: 회전 파일 로그(traceback) + SQLite `event_logs` + Telegram 요약
- 동일 Telegram 오류 10분 rate-limit, DB/파일 로그는 계속 기록
- managed cash/equity와 미체결 BUY 예약을 사용해 개인 현금까지 전략자금으로 쓰지 않음
- 개인 TQQQ/SOXL과 JDSS TQQQ/SOXL의 동일 계좌 혼합은 지원하지 않음

## GitHub 검증 상태

- PR #72 운영 로직 감사: merge `1519285cec5326d930e41df3a9caaeed421187a2`
- PR #77 Oracle runtime verifier: merge `a2261f37e608706340373dac489082fa3d18781e`
- PR #79 Oracle yfinance writable cache 보강: merge `dc168a18f6cb6a91ffedab1246d30d825f8e5ef1`
- PR #83 SGOV Reconciliation 시세 의존성 제거: merge/runtime `9231a0513e28491e3e993bcf5144936c9b7634b0`
  - Ruff: SUCCESS
  - 전체 pytest: **172 passed**
  - `JDSS-3.1.0-TWIN-H40-S3 / 3.1.0` config validation: SUCCESS
- PR #83은 전략수식·주문판단을 변경하지 않고 dry-run holdings/Reconciliation의 불필요한 Yahoo 현재가 의존성만 제거했다.

## Oracle 배포·운영 검증

### 최신 배포

- Deploy Oracle Dry Run run `31580042160`: **SUCCESS**
- 배포 SHA: `9231a0513e28491e3e993bcf5144936c9b7634b0`
- systemd: `jd_holdings_bot`
- 모드: **forced `dry_run`**, live 잠금 유지
- Toss read-only smoke: 인증 성공
- smoke 가격조회: TQQQ / SOXL / SGOV 성공

### SGOV Reconciliation 오류 수정

- 기존 증상: `RECONCILIATION_ERROR: yfinance 현재가 조회 실패: SGOV` 및 자동 점검 경고 반복
- 원인: dry-run `get_holdings()`가 수량 정합성 점검 중 `lastPrice` 표시를 위해 Yahoo 1분봉을 조회하여, SGOV 시세가 일시적으로 비면 Reconciliation 전체가 실패했다.
- 수정: Reconciliation용 holdings 조회를 실시간 Yahoo 시세와 분리했다. 보유수량·주문 정합성은 시세 장애와 무관하게 검사한다.
- 실제 현재가가 필요한 주문/평가 경로의 `get_price()` 실패 처리는 유지하여 안전장치를 약화하지 않았다.
- Yahoo 가격 공급 실패 상황에서도 holdings 경로가 동작하고 `get_price()`는 실패하는 회귀 테스트를 추가했다.
- 배포 후 Toss read-only smoke에서 SGOV 가격조회도 정상 확인했다.

### 실제 runtime 재시작 검증 상태

- 직전 PR #79 기준 Verify Oracle V3.1 Runtime run `31573086250`: **SUCCESS**, `ORACLE_V31_RUNTIME_VERIFY=PASS`
- PR #83 배포 후 Verify run `31580238889`는 서버 시장 세션이 `pre_market`이어서 운영 안전규칙에 따라 파괴적 systemd 재시작 전에 의도적으로 중단됐다.
- 따라서 해당 run 실패는 SGOV 코드 오류가 아니다. 장중/프리마켓에 운영 안전장치를 우회하지 않았다.

## 문서 상태

- `docs/STRATEGY_GUIDE.md`: 코어·부스터·SGOV·SAFE_MODE 설명
- `docs/TELEGRAM_BOT_GUIDE.md`: 주문 상태·SAFE_MODE·오류 알림·운영자 행동 순서
- `docs/BACKTEST_REPORT.md`: research / production / 실제 운영 dry-run 차이와 parity 기준
- `docs/JDSS_FINAL_SPEC.md` + `strategy.yaml`: V3.1 공식 전략 계약

## live 전 남은 필수사항

1. 실제 Toss 주문에서 발생하는 `PARTIAL_FILLED`/`UNKNOWN`의 API 상태변화와 체결 이벤트를 승인된 방식으로 관찰한다.
2. 코어 위험축소 SELL의 미체결·부분체결·취소 후 자동 재가격/재제출 정책은 Toss 실제 동작 확인 후 결정한다. 현재는 SAFE_MODE로 멈춘다.
3. live 운영계좌는 개인 TQQQ·SOXL과 분리한다.
4. live 잠금은 별도 승인·검증 전까지 유지한다.

## 다음 작업

- Oracle V3.1 forced dry-run을 계속 운영한다.
- 배포 시각 이후 Telegram에서 동일한 `yfinance 현재가 조회 실패: SGOV` Reconciliation 오류가 재발하는지 관찰한다.
- 미국장이 `closed`인 안전 시간대에 필요하면 최신 SHA의 full Oracle runtime verifier를 다시 수행한다.
- 전략 연구를 재개할 경우 production 백테스트를 기준으로 비교한다.
- live 전환은 위 남은 필수사항을 해결한 뒤 별도 결정한다.

## 작업 종료 갱신 규칙

작업 종료 시 전략·운영 코드 SHA, Oracle 배포 SHA, 검증 결과와 다음 작업을 갱신한다. 완료 이력 전체를 길게 누적하지 않고 다음 작업자가 바로 이어갈 정보만 유지한다.
