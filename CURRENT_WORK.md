# JD_HOLDINGS Current Work

> 현재 전략·개발·배포·검증 상태의 단일 상태판입니다. 이전 값을 교체하는 롤링 문서이며, 상세 전략과 승인된 기준 백테스트는 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 공식 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md)를 따릅니다.

## 현재 릴리즈와 운영

- 공식 릴리즈: **`v3.2.2`**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- 배포 코드·Oracle runtime SHA: **`6dcc9d755b1ed7d82ce613cdf6540528f29a9845`**
- 병합 PR: **[#137](https://github.com/hwanff11/JD_HOLDINGS/pull/137)**
- Oracle `jd_holdings_bot`: **active / V3.2.2 forced dry-run**
- 모의원장 reconciliation: **정상 / `v322_portfolio_safe_mode=0`**
- 실제 Toss read-only preflight: **live 준비 실패 / SOXL 기존 보유 발견**
- live: **LOCKED OFF**

## 이번 릴리즈 완료 범위

1. Markdown 역할을 고정하고 중복 결정 문서를 통합해 다음 버전에도 새 파일이 늘지 않게 합니다.
2. CLI와 Telegram `/bt`를 같은 production-equivalent 백테스트 경로로 통일합니다.
3. Telegram 명령·도움말·승인 버튼·오류 문구를 V3.2.2 계약으로 맞춥니다.
4. 최초 배분, 승인 만료, 부분체결, 재시작, 주문 응답 오염, 계좌·원장 불일치의 복구와 차단을 강화합니다.
5. CI 통과 뒤 forced dry-run으로 배포하고 실제 계좌는 read-only preflight만 수행합니다.

## 이번 릴리즈 후보의 검증

- 전체 회귀 테스트: **238 passed**
- Ruff: **passed**
- `jdss validate-config`: **passed**
- `bash -n deploy.sh`: **passed**
- `git diff --check`: **passed**
- GitHub Quality Gate run **`32381523340`**: **SUCCESS**
- GitHub Backtest run **`32381523237`**: **SUCCESS**
- GitHub Security run **`32381523247`**: **SUCCESS** (dependency audit·CodeQL·secret scan)
- Oracle direct deploy: **SUCCESS** (exact SHA·forced dry-run·service·config·SQLite·시세·Toss 인증 smoke)
- Oracle runtime verifier run **`32381868992`**: **SUCCESS / `PASS_NO_RESTART`**
- verifier 당시 시장이 `closed`가 아니어서 안전규칙에 따라 추가 systemd restart 검증만 생략했습니다.
- 배포 후 10분 error journal: **0건**
- 공용 runner 로컬 재현: **2011-01-01 요청, 확보 데이터 2011-01-03~2026-08-04**
- 최신 재현값: **Total Return +2,232.26% / CAGR 22.40% / MDD -30.93% / Sharpe 1.005**
- 데이터 공급자 수정 허용범위 안이며 승인된 인간용 기준값과 QQQ 비교는 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md)에만 유지합니다.
- 현재 로컬은 Yahoo DNS 조회가 실패해 명시적 경고와 함께 전체 기간을 덮는 기존 캐시를 사용했습니다. 요청 시작을 덮지 못하는 최근 캐시는 장기 결과로 사용하지 않습니다.

## 구현된 운영 안전장치

- BUY는 **1차 최신 가격·수량 검토 → 2차 60초 최종 승인**의 반자동 방식이고, 위험축소 SELL은 자동입니다.
- 완결봉 당일 after-hours BUY를 막고 다음 거래일 허용 세션에서만 목표주수·SELL·BUY를 처리합니다. 토스 08:50~08:59 KST 점검시간에는 주문하지 않습니다.
- 저장 `target_qty`와 보유·열린 주문의 차이만 복구해 미승인·만료·부분체결 뒤 같은 목표가 영구 미달로 남지 않게 합니다.
- 열린 위험축소 SELL과 SELL 후 reconciliation이 끝나기 전에는 새 BUY를 만들지 않습니다.
- 주문 DB 저장 뒤 원장 반영 전 종료된 프로세스를 시작 시 누적 체결수량·체결금액 차이로 복구합니다.
- 브로커 주문 응답의 ID·종목·방향·수량을 예약 주문과 대조하고, 누적 체결수량 감소·종료 주문 재개·불명확한 `UNKNOWN`을 거부하거나 SAFE_MODE로 전환합니다.
- 실제 Toss 보유·열린 주문·주문가능금액은 시작 시 read-only preflight로 확인하되 forced dry-run 모의원장에 자동 채택하지 않습니다.
- 자동 배포 중 특정 버전명만 보고 SQLite를 삭제하던 일회성 V3.2.2 전환 코드를 제거했습니다. 모든 향후 버전 전환은 별도 migration plan·백업·호환성 테스트 없이는 중단됩니다.

## 알려진 경계와 live 차단 조건

- forced dry-run reconciliation은 **SQLite와 모의 브로커**의 일치 검사입니다. 실제 Toss 정합성 완료를 뜻하지 않습니다.
- 실제 Toss에 개인 QQQ/TQQQ/SOXL 보유나 열린 주문이 있으면 자동 채택하지 않으며 live 준비 실패로 봅니다.
- 현재 read-only preflight에서 **SOXL 기존 보유**가 확인됐습니다. 출처·처리계획이 승인되고 실계좌 migration을 리허설하기 전까지 live 차단을 유지합니다.
- portfolio SAFE_MODE는 정상 조회 한 번으로 자동 해제하지 않습니다. 원인 복구를 증명하고 운영자가 명시적으로 해제하는 별도 절차가 마련되기 전까지 live를 열지 않습니다.
- 실제 주문 어댑터에서 승인 ID·신호·최종 quote를 주문 예약 트랜잭션에 직접 결합하는 방어층, 실제 수수료·배당·기업행동 회계, 실계좌 migration 리허설이 아직 live 승격 조건으로 남아 있습니다.
- 따라서 이번 변경도 **live 활성화를 포함하지 않으며**, 대표의 별도 승인 없이 잠금을 해제하지 않습니다.

## 바로 다음 작업

1. forced dry-run과 Telegram `/dashboard`, `/portfolio`, `/account`, `/order`, `/errors`, `/bt`를 실제 시장 주기에서 관찰합니다.
2. 실제 Toss의 기존 SOXL을 개인 보유로 유지할지, 향후 승인된 JDSS 원장으로 전환할지 별도 migration 계획으로 결정합니다. 자동 채택하지 않습니다.
3. 시장이 `closed`일 때 필요하면 runtime verifier를 다시 실행해 restart/recovery를 `PASS_RESTARTED`로 확인합니다.
4. sticky SAFE_MODE 명시적 복구 절차, 주문 트랜잭션의 승인 증빙 결합, 실제 회계·migration 리허설 전에는 live를 승인하지 않습니다.
