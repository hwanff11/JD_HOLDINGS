# JD_HOLDINGS Current Work

> 현재 작업·배포·검증 상태의 단일 기준이다. 전략 수치는 `strategy.yaml`과 `docs/JDSS_FINAL_SPEC.md`, 문서 역할은 `docs/README.md`를 따른다.

## 현재 작업

- 기준 브랜치: `main`
- 최신 main 코드: `008fe10dc6060bde02d27220fd854a698ba24005`
- 현재 전략: `JDSS-3.1.0-TWIN-H40-S3` / `3.1.0`
- PR #62에서 연구 후보를 정식 구현·문서·테스트에 반영하고 main에 승격
- PR #65에서 Oracle dry-run 배포 검증을 V3.1 계약으로 최신화
- PR #66에서 cold restart, dry-run 지정가 진행, 전략 버전 전환 배포 안전장치를 보강
- V3.1 main production 통합 백테스트 재검증 완료
- 제외: Oracle V3.1 실제 배포와 live 적용. live는 설정과 애플리케이션 코드에서 계속 차단한다.

## 전략·운영 기준

- 총자금: `$20,000`
- 코어: QQQ·SOXX 완료 월말 종가와 6개월 이동평균. OFF→ON 첫 달 TQQQ·SOXL 목표 10%, 다음 월말에도 ON이면 15% 유지
- 부스터 자금 상한: 종목당 `$8,000`(초기 총자금 40%)
- 부스터 실제 S3 정상 최대 신규투입: 3단계 누적 90%이므로 `$7,200`(초기 총자금 36%)
- 부스터 분할매수: 40% / 30% / 20%, 누적 40% / 70% / 90%; 최초 체결가 대비 0% / -2% / -5%
- 익절: TP1 평단 +4%에서 약 30%, TP2 +10%에서 잔량
- TP1 이후 기간 기준 잔여청산 OFF, 재매수 OFF, 자동손절 OFF
- SOXL 섹터 가드: SOXX/SMH EMA60 기준 1·3차 차단
- 현금: SGOV와 `$250` 버퍼
- 매수: 코어·부스터 모두 Telegram 2단계 승인
- 매도: 코어 위험축소 자동, 부스터 TP1·TP2 자동
- 공식 계약: `docs/JDSS_FINAL_SPEC.md`

## 구현·코드리뷰 상태

- 월간 6개월 추세와 코어 10→15% 단계형 목표를 production·backtest 엔진에 정식 구현
- V3.1 3단계 부스터와 TP1 30% / TP2 +10%를 설정·전략·TP 계산·테스트에 동기화
- 코어와 부스터가 동일 티커를 공유할 때 브로커 합산 수량·평단이 부스터 원장에 섞이던 경로 수정: 부스터 원장은 해당 부스터 주문 체결과 기존 부스터 원장만 사용
- TP 부분체결 후 취소·재주문 시 이전 부분체결을 잃을 수 있던 계산 수정: TP 계획에 누적 체결수량을 보존하고 포지션은 신규 체결 delta만 차감
- dry-run 재시작 시 코어+부스터 합산 보유량의 평균단가를 두 원장의 원가 합으로 재구성
- cold restart 시 DB의 DRY 미체결 주문을 메모리 broker에 복원하고 order sequence를 이어감. `UNKNOWN` 주문은 복원하지 않아 lost-response 안전장치를 유지
- MarketData dry-run의 PENDING 지정가를 주문 모니터 조회 시 최신 가격으로 다시 평가해 TP/SGOV 가격도달 체결을 진행
- PARTIAL_FILLED dry-run 주문 취소를 허용해 취소·재가격 복구 경로와 일치시킴
- V3.0→V3.1처럼 `config_version`이 바뀌는 배포는 서비스를 먼저 정지한 뒤 active booster cycle 또는 미완료 주문이 있으면 배포를 차단하고 기존 서비스를 복구
- Oracle 배포 workflow의 V3.0 하드코딩 검증을 V3.1 전략/설정 버전으로 교체하고 회귀 테스트 추가
- Telegram 최종 런타임의 V3.0 잔존 표시를 V3.1 계약으로 변환하고 `/guide`를 V3.1 기준으로 동기화
- 문서에서 H40 40%는 자금 상한이고 S3 실제 정상 최대 신규투입은 36%임을 명확히 구분

## 검증 상태

- 전략 승격 PR #62 최종 검증: CI #367, JDSS V3 Dry Run #97, Security #147 성공
- 배포 게이트 PR #65: CI #373, Security #153 성공
- 운영 안정화 PR #66 최종 head `25c3f151d73948e9a8ce03a5e17a07a467bb63b3`
  - CI Quality Gate run `31560691263` (#378) 성공
  - Ruff 성공
  - pytest `146 passed`, 전체 커버리지 68%
  - `jdss validate-config`: `JDSS-3.1.0-TWIN-H40-S3 / 3.1.0` 성공
  - JDSS V3 Dry Run run `31560691290` (#100) 성공
  - Security run `31560691262` (#158) 성공
- 연구 동일 조건 비교: V3.0 Total +678.07%, CAGR 14.05%, MDD -28.29%, Sharpe 0.835, Sortino 1.004 → V3.1 최종 후보 Total +1372.96%, CAGR 18.81%, MDD -26.04%, Sharpe 0.940, Sortino 1.197
- V3.1 main production 재검증: Issue #64, `JDSS V3 Backtest` run `31559442771` 성공
  - 전략 main SHA `54baca27e612d123b44d7a9fa1a036d2e42c0a44`
  - Total +1372.96%, CAGR 18.81%, MDD -26.04%, Sharpe 0.940, 평균노출 31.87%
  - 코어 체결 320건, 부스터 체결 443건, SGOV 추정수익 `$21,538.41`
  - 연구 최종 후보와 핵심 성과가 정확히 일치하여 research → production 엔진 동등성 확인
- PR #65/#66은 실행·배포 안정화 변경으로 전략 수식과 production 백테스트 엔진을 변경하지 않았다.

## 배포 상태

- GitHub `main`: V3.1.0, SHA `008fe10dc6060bde02d27220fd854a698ba24005`
- 기존 GitHub Release: `v3.0.0`, target `df640d16c485b770d9c57c570f5a13b3bdb4e2de`
- Oracle 배포 SHA: `df640d16c485b770d9c57c570f5a13b3bdb4e2de` — 아직 V3.0 dry-run
- `jd_holdings_bot` Oracle 서비스는 V3.1 배포 성공이 확인되기 전까지 V3.0 코드로 간주한다.
- V3.1 배포 workflow는 현재 V3.1 전략 식별자·config 3.1.0·live 잠금을 검증한다.
- V3.0→V3.1 배포 시 진행 중 부스터 사이클 또는 미완료 주문이 있으면 자동 중단하고 기존 서비스를 복구한다.
- 운영 SHA의 최종 확인 기준은 Oracle `/home/ubuntu/JD_HOLDINGS/current` 링크와 배포 Actions 결과다.

## live 전 필수 보완

1. `PortfolioService.portfolio_equity()`는 현재 브로커의 USD 주문가능금액과 TQQQ·SOXL·SGOV 전체 보유량을 합산한다. dry-run은 전략 전용 계좌라 문제없지만 실제 Toss 계좌에 개인 자산이 함께 있으면 코어 목표자산이 부풀 수 있으므로 live 전에 JDSS 관리 현금·자산만 계산하는 managed equity 계층을 구현해야 한다.
2. Oracle dry-run 브로커는 재시작 시 DB의 현재 원가를 기준으로 보유량과 buying power를 재구성한다. 닫힌 과거 사이클의 실현손익을 별도 simulated-cash 원장으로 영속화하지 않으므로 장기간 Oracle dry-run 잔고를 성과측정 자료로 사용하지 않는다. dry-run은 주문·승인·복구·정합성 검증용으로 사용한다.
3. `telegram_bot.py`의 일부 내부 표현은 V3.0 역사 코드가 남아 있으나 실제 엔트리포인트 `FinalTelegramBotApp`이 V3.1 표시로 변환한다. 향후 구조 정리 시 기본 모듈 자체의 표시 문구를 V3.1로 통합할 수 있다.
4. live 승격 전에는 코어 위험축소 지정가가 미체결·취소됐을 때의 자동 재가격/재제출 정책을 실브로커 기준으로 별도 검증해야 한다.

## 다음 작업

1. V3.1을 Oracle에 적용하려면 `Deploy Oracle Dry Run` 절차를 실행한다. 버전 전환 사전점검에서 active booster cycle/미완료 주문이 있으면 먼저 기존 V3.0 계약으로 안전하게 종료한다.
2. 배포 성공 후 서비스 active, Reconciliation, cold restart DRY 주문복원, Telegram V3.1 출력, Toss read-only smoke를 확인한다.
3. Oracle V3.1 dry-run에서는 주문·승인·부분체결·재시작 복구를 관찰하되 장기 잔고수익률은 성과 근거로 사용하지 않는다.
4. live는 계속 금지하며 managed equity 보완과 별도 운영 검증·승인 전에는 활성화하지 않는다.

## 작업 종료 갱신 규칙

작업 종료 시 활성 브랜치, 마지막 커밋, 검증 결과, 배포 SHA와 다음 작업을 갱신한다. 완료 이력 전체를 누적하지 않고 다음 작업자가 바로 이어가는 데 필요한 현재 상태만 유지한다.
