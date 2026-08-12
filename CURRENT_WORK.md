# JD_HOLDINGS Current Work

> 현재 작업·배포·검증 상태의 단일 기준이다. 전략 수치는 `strategy.yaml`과 `docs/JDSS_FINAL_SPEC.md`, 문서 역할은 `docs/README.md`를 따른다.

## 현재 작업

- 기준 브랜치: `main`
- 최신 전략·운영 코드 SHA: `90562a0dead3b1d1fb092fe5ec9b0ff58fc35139`
- 현재 전략: `JDSS-3.1.0-TWIN-H40-S3` / `3.1.0`
- PR #62: 연구 후보를 production 전략·백테스트·문서·테스트에 정식 승격
- PR #65: Oracle dry-run V3.1 배포 게이트 최신화
- PR #66: cold restart와 전략 버전 전환 배포 안전장치 보강
- PR #70: V3.1 문서·설정·운영 코드 전반 리뷰 후 managed account, 동시 주문, 승인수량, SAFE_MODE, 재시작·부분체결 안전성 보강
- V3.1 production 통합 백테스트는 연구 최종 후보와 동일 성과를 재현했다.
- 제외: Oracle V3.1 실제 배포와 live 적용. live는 설정과 애플리케이션 코드에서 계속 차단한다.

## 전략 기준

- 초기 JDSS 관리배정금: `$20,000`
- 코어: QQQ·SOXX 완료 월말 종가와 6개월 이동평균. OFF→ON 첫 달 TQQQ·SOXL 목표 10%, 다음 월말에도 ON이면 15% 유지
- 부스터 자금 상한: 종목당 `$8,000`(초기 배정금 40%)
- S3 정상 최대 신규투입: 3단계 누적 90%이므로 `$7,200`(초기 배정금 36%)
- 분할매수: 40% / 30% / 20%, 누적 40% / 70% / 90%; 최초 체결가 대비 0% / -2% / -5%
- 익절: TP1 평단 +4%에서 약 30%, TP2 +10%에서 잔량
- TP1 이후 기간 기준 잔여청산 OFF, 재매수 OFF, 자동손절 OFF
- SOXL 섹터 가드: SOXX/SMH EMA60 기준 1·3차 차단
- 유휴자금: JDSS managed cash와 JDSS 관리 SGOV, 현금 버퍼 `$250`
- 매수: 코어·부스터 모두 Telegram 2단계 승인
- 매도: 코어 위험축소 자동, 부스터 TP1·TP2 자동
- 공식 계약: `docs/JDSS_FINAL_SPEC.md`

## 운영·코드리뷰 완료 상태

- `managed cash`: 초기 `$20,000`에서 JDSS 주문의 실제 누적 체결과 설정 수수료만 반영한다. 닫힌 사이클의 실현손익도 재시작 뒤 유지한다.
- `managed equity`: managed cash + JDSS 코어·부스터 + JDSS 관리 SGOV만 포함한다. 전체 Toss 계좌의 개인 현금·개인 SGOV는 코어 목표비중에 포함하지 않는다.
- 같은 Toss 계좌의 개인 TQQQ·SOXL 혼합 보유는 지원하지 않는다. 브로커가 동일 티커 수량을 합산하므로 JDSS 원장과 증명할 수 없어 Reconciliation이 SAFE_MODE로 전환한다.
- 모든 BUY는 미체결 BUY 잔여금과 수수료까지 예약하며, managed cash 검사와 주문예약을 하나의 SQLite `BEGIN IMMEDIATE` 트랜잭션으로 처리해 동시 Telegram 실행의 이중예약을 차단한다.
- 코어 최종승인 직전에는 `신호 생성 당시 계획주수`와 `현재 managed equity·현재 지정가·현재 코어수량으로 계산한 잔여 목표주수` 중 작은 값을 상한으로 사용한다.
- 종목 SAFE_MODE 또는 SGOV 자금 SAFE_MODE에서는 기존 코어 신호를 포함해 모든 신규 BUY 승인·실행을 차단한다.
- 코어와 부스터 동일티커 브로커 합산 수량·평단이 부스터 원장에 섞이지 않도록 주문 체결 delta와 슬리브별 원장을 사용한다.
- TP 부분체결 후 취소·재주문은 이전 누적체결을 보존하고 신규 체결 delta만 포지션에 반영한다.
- dry-run 재시작 시 코어·부스터·JDSS 관리 SGOV는 SQLite 원장에서, 현금은 전체 JDSS 누적체결 원장에서 복원한다.
- 체결 0으로 증명 가능한 DRY 미체결 주문만 자동복원한다. `UNKNOWN`과 재시작 시점의 `PARTIAL_FILLED`는 추정복구하지 않고 SAFE_MODE로 남긴다.
- 새 DRY 주문번호는 열린 주문뿐 아니라 과거 완료 주문을 포함한 전체 이력의 최대 `DRY-########` 다음부터 이어가 ID 재사용을 막는다.
- DB 열린 주문에 broker order id가 없거나 브로커에서 주문을 확인할 수 없으면 Reconciliation/OrderMonitor가 SAFE_MODE 이벤트를 기록하고 신규매수를 막는다. OrderMonitor는 해당 상황에서 반복 예외로 종료하지 않는다.
- MarketData dry-run 누적 부분체결은 이전 적용값과의 신규 delta만 보유량·현금에 반영한다.
- V3.0→V3.1처럼 `config_version`이 바뀌는 배포는 서비스를 정지한 뒤 active booster cycle 또는 미완료 주문이 있으면 배포를 중단하고 기존 서비스를 복구한다.
- Telegram 최종 런타임과 `/guide`, 전략·배포·보안 문서를 V3.1 managed-account 계약과 동기화했다.

## 검증 상태

- PR #70 최종 head `e1881af80f6cef9128920e3336b4db459dc868a5`
  - CI Quality Gate #408 / run `31564056368` 성공
  - JDSS V3 Dry Run #125 / run `31564056363` 성공
  - Security #188 / run `31564056332` 성공
  - PR #70 squash merge SHA `90562a0dead3b1d1fb092fe5ec9b0ff58fc35139`
- 연구 동일 조건 비교: V3.0 Total +678.07%, CAGR 14.05%, MDD -28.29%, Sharpe 0.835, Sortino 1.004 → V3.1 최종 후보 Total +1372.96%, CAGR 18.81%, MDD -26.04%, Sharpe 0.940, Sortino 1.197
- V3.1 production 재검증: Issue #64, `JDSS V3 Backtest` run `31559442771` 성공
  - Total +1372.96%, CAGR 18.81%, MDD -26.04%, Sharpe 0.940, 평균노출 31.87%
  - 코어 체결 320건, 부스터 체결 443건, SGOV 추정수익 `$21,538.41`
  - 연구 후보와 production 엔진의 핵심 성과가 정확히 일치했다.
- PR #65/#66/#70은 실행·배포·회계 안전성 보강이며 V3.1 전략 수식과 production 백테스트 엔진을 변경하지 않았다.

## 배포 상태

- GitHub main V3.1 최신 코드 SHA: `90562a0dead3b1d1fb092fe5ec9b0ff58fc35139`
- 기존 GitHub Release: `v3.0.0`, target `df640d16c485b770d9c57c570f5a13b3bdb4e2de`
- Oracle 배포 SHA: `df640d16c485b770d9c57c570f5a13b3bdb4e2de` — 아직 V3.0 dry-run
- `jd_holdings_bot` Oracle 서비스는 V3.1 배포 성공이 확인되기 전까지 V3.0 코드로 간주한다.
- V3.1 배포 workflow는 V3.1 전략 식별자·config 3.1.0·live 잠금·버전 전환 사전점검을 검증한다.

## live 전 필수 보완

1. 코어 위험축소 SELL이 실브로커에서 미체결·부분체결·취소될 때의 자동 재가격/재제출 정책을 Toss 실제 주문상태 기준으로 검증·보강한다.
2. live 운영계좌는 개인 TQQQ·SOXL과 분리한다. 동일계좌 혼합이 필요하다면 별도 계좌/슬리브 식별처럼 브로커 수준에서 소유권을 증명할 수 있는 구조를 먼저 마련한다.
3. 향후 JDSS 초기 배정금을 `$20,000`에서 변경할 기능을 넣는다면 mutable config 값만 바꾸지 말고 입출금·증감 이력을 영속하는 명시적 capital-allocation 원장을 추가한다.
4. Oracle V3.1 dry-run에서 PENDING·PARTIAL_FILLED·UNKNOWN·프로세스 재시작·Reconciliation 실패를 포함한 fault-injection을 실제 서비스 경로로 검증한 뒤 live 승격을 별도 승인한다.
5. live 잠금은 위 검증과 별도 승인 전까지 유지한다.

## 다음 작업

1. V3.1을 Oracle에 적용하려면 `Deploy Oracle Dry Run` 절차를 실행한다. 버전 전환 사전점검에서 active booster cycle/미완료 주문이 있으면 기존 V3.0 계약으로 안전하게 종료한 뒤 다시 배포한다.
2. 배포 성공 후 서비스 active, managed cash/equity, Reconciliation, cold restart 주문복원, Telegram V3.1 출력, Toss read-only smoke를 확인한다.
3. Oracle V3.1 dry-run에서 주문·승인·부분체결·재시작 복구를 관찰한다. 전략 성과 판단은 production 백테스트를 기준으로 한다.
4. live는 계속 금지한다.

## 작업 종료 갱신 규칙

작업 종료 시 활성 브랜치, 마지막 커밋, 검증 결과, 배포 SHA와 다음 작업을 갱신한다. 완료 이력 전체를 누적하지 않고 다음 작업자가 바로 이어가는 데 필요한 현재 상태만 유지한다.