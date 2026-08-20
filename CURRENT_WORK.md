# JD_HOLDINGS Current Work

> 현재 전략·개발·배포·검증 상태의 단일 상태판입니다. 이전 값을 교체하는 롤링 문서이며, 상세 전략과 승인된 기준 백테스트는 [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md), 공식 계약은 [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md)를 따릅니다.

## 현재 릴리즈와 운영

- 공식 릴리즈: **`v3.2.2`**
- 전략 ID: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- 활성 개발 브랜치: **`codex/v322-command-ops-hardening`**
- 작업 시작 기준 `origin/main`: **`eab9440a12b9c58672970fcee903df0d3944e616`**
- 마지막 확인 production/runtime SHA: **`6f7d93fb839b9fb0c183c98db5af162889880dd6`**
- Oracle 운영: **V3.2.2 forced dry-run / live LOCKED OFF**
- 현재 변경분: **로컬 검증 완료, PR·CI·Oracle 재배포 전**

## 현재 활성 목표

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
- portfolio SAFE_MODE는 정상 조회 한 번으로 자동 해제하지 않습니다. 원인 복구를 증명하고 운영자가 명시적으로 해제하는 별도 절차가 마련되기 전까지 live를 열지 않습니다.
- 실제 주문 어댑터에서 승인 ID·신호·최종 quote를 주문 예약 트랜잭션에 직접 결합하는 방어층, 실제 수수료·배당·기업행동 회계, 실계좌 migration 리허설이 아직 live 승격 조건으로 남아 있습니다.
- 따라서 이번 변경도 **live 활성화를 포함하지 않으며**, 대표의 별도 승인 없이 잠금을 해제하지 않습니다.

## 바로 다음 작업

1. 변경분을 명시적으로 stage·commit하고 PR을 생성합니다.
2. GitHub Quality/Security/Backtest CI 결과와 기준 백테스트 artifact를 확인합니다.
3. 병합 뒤 `env -u GITHUB_TOKEN ./deploy.sh`로 Oracle forced dry-run을 갱신하고 exact SHA·서비스·Telegram·read-only Toss smoke를 확인합니다.
4. 배포 결과와 남은 live 차단 조건을 이 상태판에 반영합니다.
