# JD_HOLDINGS Current Work

> 현재 작업·배포·검증 상태의 단일 기준이다. 전략 수치는 `strategy.yaml`과 `docs/JDSS_FINAL_SPEC.md`, 쉬운 설명은 `docs/STRATEGY_GUIDE.md`, 백테스트 재현은 `docs/BACKTEST_REPORT.md`를 따른다.

## 현재 상태

- 현재 전략: `JDSS-3.1.1-TWIN-H40-S3` / config `3.1.1`
- 현재 전략 코드 SHA: `29ae1491f9b7428379d0050a8182c5f866c35e6a`
- Oracle 배포/runtime SHA: `29ae1491f9b7428379d0050a8182c5f866c35e6a`
- Oracle `jd_holdings_bot`: **V3.1.1 forced dry-run**
- 최종 runtime verifier: run `31655301396` **SUCCESS / phase=closed / PASS_RESTARTED**
- live 잠금 유지
- 본 문서 갱신은 운영코드 변경이 없는 최종 인수인계 문서 변경이므로 이후 `main` SHA가 Oracle runtime SHA보다 앞설 수 있다.

## V3.1.1 최종 전략·자금 계약

- JDSS 고정 전략원금: **$50,000**
- 월급·추가입금·개인 USD·개인 QQQ/QQQM: JDSS 관리원금에 편입하지 않음
- JDSS 자체 실현수익: 다음 매매 사이징에 재투자하지 않음
- 손실 발생 시: 개인자금으로 자동 보충하지 않음
- JDSS 이익으로 과거 손실을 회복해 고정원금 $50,000까지 돌아오는 것은 허용
- 고정원금을 넘는 실현현금은 Toss 계좌에 남아 있어도 JDSS BUY에 사용하지 않음
- SGOV: **OFF**. 미투입 JDSS 원금은 USD 현금으로 유지
- 개인 TQQQ/SOXL의 같은 Toss 계좌 혼합 보유: 금지

## 매매 전략

### 월간 쌍발 코어

- TQQQ→QQQ, SOXL→SOXX 완료 월말 데이터 사용
- 최근 6개월 월말 종가 단순이동평균
- 월말 종가가 6개월 평균보다 높으면 코어 ON
- OFF→ON 첫 달: 고정원금의 10%, 종목당 약 `$5,000`
- 다음 월에도 ON: 15%, 종목당 약 `$7,500`
- OFF: 목표 0%
- BUY는 Telegram 2단계 승인, 위험축소 SELL은 자동

### H40-S3 부스터

- 종목별 H40 cap: **$20,000**, 고정원금의 40%
- S3: 40% / 30% / 20%, 누적 40% / 70% / 90%
- 정상 한 사이클 최대 신규투입: **$18,000**, 고정원금의 36%
- 최소 Score 55 / Reversal Score 5
- RED 신규·추가매수 차단
- 추가매수: 최초 체결가 대비 -2% / -5%
- TP1: 평단 +4%에서 약 30%
- TP2: 평단 +10%에서 잔량
- 재매수·자동손절·기간강제청산 OFF
- SOXL 섹터 가드: SOXX/SMH EMA60 기준 1·3차 차단

## 관리원장·동일 Toss 계좌 규칙

- 코어·부스터 현재 원가가 $50,000 고정원금을 점유한다.
- 열린 BUY의 미체결 금액·예상수수료도 예약한다.
- 신규 BUY는 `남은 고정원금`, `JDSS 실제 가용현금`, `Toss 실제 주문가능금액` 중 가장 작은 한도를 사용한다.
- 실제 Toss USD가 많아도 JDSS는 관리한도를 초과해 사용할 수 없다.
- 개인 USD·QQQ·QQQM은 JDSS 원장에서 제외한다.
- 브로커가 TQQQ/SOXL 동일 티커 수량을 합산하므로 개인 TQQQ/SOXL을 같은 계좌에 보유하면 Reconciliation 계약을 만족할 수 없다.

## Production 백테스트 최종 검증

병합된 V3.1.1 `main` production 백테스트 run `31653458235`: **SUCCESS**

- Total Return: **+414.80%**
- CAGR: **11.07%**
- MDD: **-22.97%**
- Sharpe: **0.839**
- 평균노출: **21.28%**
- 코어 체결: **319건**
- 부스터 체결: **443건**
- 고정 원금 상한: **$50,000.00**
- 최대 동시 원가투입: **$45,951.75**
- 수익 재투자: **OFF**
- SGOV: **OFF**

최대 원가투입이 $50,000 아래로 유지돼 production 백테스트에서도 고정원금 게이트가 확인됐다. 승인 지연·실제 호가·미체결·API 장애·재시작 같은 현실 변수는 Oracle dry-run에서 별도 검증한다.

## GitHub 검증 상태

V3.1.1 구현 PR #88 병합 후 주요 품질게이트:

- Quality Gate run `31653311154`: **SUCCESS**
  - Ruff: All checks passed
  - pytest: **173 passed in 12.17s**
  - coverage: 69%
  - config validation: `JDSS-3.1.1-TWIN-H40-S3 / 3.1.1`
- End-to-end/Fault-injection Dry Run run `31653311202`: **SUCCESS**
- Security run `31653311174`: **SUCCESS**
- Production Backtest run `31653311166`: **SUCCESS**
- exact-main production 재검증 run `31653458235`: **SUCCESS**

Oracle verifier 자체에서 발견된 운영검증 오류도 모두 수정했다.

1. Environment Secret 미연결 → PR #92에서 `oracle-dry-run` Environment 연결
2. release 폴더에 `.git`이 있다고 가정 → PR #95에서 `current -> releases/<SHA>` 경로로 SHA 검증
3. runtime `.env`/venv 경로 오인 → PR #98에서 `shared/.env`, shared venv로 수정
4. 존재하지 않는 `MarketClock.phase()` 호출 → PR #101에서 `classify_session()`으로 수정

각 수정은 회귀 테스트를 추가하고 Quality Gate/Security/Backtest를 통과한 뒤 병합했다.

## Oracle 최종 배포·runtime 검증

### 최종 배포

- Deploy Oracle Dry Run run `31655222518`: **SUCCESS**
- 배포 SHA: `29ae1491f9b7428379d0050a8182c5f866c35e6a`
- 배포 구조: `current -> releases/29ae1491f9b7428379d0050a8182c5f866c35e6a`
- systemd: `jd_holdings_bot`
- 모드: forced `dry_run`
- V3.1.1 config validation: 성공
- Toss read-only smoke: 성공
- live 잠금 유지

### 최종 runtime verifier

Verify Oracle V3.1 Runtime run `31655301396`: **SUCCESS**

- source contract 확인: 성공
- focused runtime safety tests: **27 passed**
- SSH/Environment Secret: 성공
- Oracle `current` exact SHA 확인: 성공
- `shared/.env` forced dry-run/live lock 확인: 성공
- `jd_holdings_bot` active: 성공
- 서버 V3.1.1 config validation: 성공
- Toss read-only smoke: 성공
- 시장 phase: `closed`
- 안전규칙에 따라 systemd 실제 재시작: 수행
- 재시작 후 service active + config validation: 성공
- 결과: **ORACLE_V311_RUNTIME_VERIFY=PASS_RESTARTED**

## Telegram V3.1.1 확인

- `/dashboard`: JDSS 통합 현황
- `/portfolio`: 6개월 코어와 H40-S3 상태
- `/account`: 실제 Toss 전체계좌 조회
- `/status`: 종목별 코어·부스터 상세
- `/score`: JDSS 점수·지표·게이트
- `/history`: 최근 점수 이력
- `/signal`: 활성 BUY 신호
- `/backtest`: production 백테스트
- `/guide`: V3.1.1 쉬운 설명
- `/order`, `/errors`, `/ping`, `/help`
- `/sgov`: production 메뉴에서 제거
- 가이드에 `$50,000 고정`, H40 `$20,000`, S3 최대 `$18,000`, 수익 비재투자, SGOV OFF, 개인 QQQ/QQQM 분리를 반영

## 연구 정리

- 자본증액/Profit-Lock 연구 PR #87은 V3.1.1 fixed-$50k 결정으로 대체되어 **미병합 상태로 종료**했다.
- 연구 기록은 삭제하지 않고 재현 이력으로 보존한다.
- V3.1.1은 전략자금 확대보다 TQQQ/SOXL 위험예산을 명확히 제한하는 운영정책을 선택했다.

## live 전 남은 필수사항

1. 실제 Toss 주문에서 발생하는 `PARTIAL_FILLED`/`UNKNOWN`의 API 상태변화와 체결 이벤트를 승인된 방식으로 관찰한다.
2. 코어 위험축소 SELL의 미체결·부분체결·취소 후 자동 재가격/재제출 정책은 Toss 실제 동작 확인 후 결정한다. 현재는 SAFE_MODE로 멈춘다.
3. live 운영계좌에서 개인 TQQQ·SOXL과 JDSS 보유분을 혼합하지 않는다.
4. live 잠금은 위 항목을 해결하고 별도 명시적 승인하기 전까지 유지한다.

## 다음 작업

- Oracle V3.1.1 forced dry-run을 계속 운영한다.
- Telegram 신호·승인·TP·SAFE_MODE 흐름을 실제 시장 데이터로 관찰한다.
- 새로운 전략연구가 필요하면 현재 production V3.1.1을 비교 기준선으로 사용한다.
- 월급·추가저축·개인 장기투자는 JDSS 원금에 자동 편입하지 않는다.
- live 전환은 이번 작업 범위가 아니며 별도 결정한다.

## 작업 종료 갱신 규칙

작업 종료 시 전략·운영 코드 SHA, Oracle 배포 SHA, 검증 결과와 다음 작업을 갱신한다. 완료 이력 전체를 길게 누적하지 않고 다음 작업자가 바로 이어갈 정보만 유지한다.
