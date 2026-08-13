# JD_HOLDINGS Current Work

> 현재 전략·배포·검증 상태의 단일 기준입니다. 상세 전략과 백테스트는 `docs/STRATEGY_GUIDE.md`, 공식 계약은 `docs/JDSS_FINAL_SPEC.md`를 따릅니다.

## 현재 릴리즈·운영 상태

- 공식 릴리즈: **`v3.2.2`**
- production 코드 SHA: **`6f7d93fb839b9fb0c183c98db5af162889880dd6`**
- Oracle runtime SHA: **`6f7d93fb839b9fb0c183c98db5af162889880dd6`**
- Oracle `jd_holdings_bot`: **active / V3.2.2 forced dry-run**
- runtime verifier: run **`31704844851` SUCCESS / `PASS_NO_RESTART`**
- 검증 당시 시장이 `pre_market`이어서 안전규칙에 따라 verifier의 추가 systemd 재시작만 생략
- live: **LOCKED OFF**

## 현재 전략

- 전략: **`JDSS-3.2.2-RS6M-ONEWAY-HWM75`**
- config/package: **3.2.2**
- 시작 위험원금: **$50,000**
- HWM75 통제복리: 최고자산 증가분의 75%만 위험예산 확대에 반영
- QQQ 중심 동적 노출: **0.5 / 1.0 / 1.25 / 1.5x**
- 고변동 브레이크: QQQ 20일 연환산 변동성 30% 이상 → 0.5x
- RS6M: SOXX 126거래일 상대강도 우위 시 레버리지 슬리브의 50%를 SOXL로 분산
- RS 이탈: 월중 SOXL → TQQQ one-way exit
- JDSS H40-S3: 직접 자금전략이 아니라 최대 5% virtual overlay 신호엔진
- SGOV: OFF
- 모든 위험증가 BUY: Telegram 2단계 승인
- 위험축소 SELL: 자동
- live: **LOCKED OFF / forced dry-run**

## Canonical 백테스트 기준

exact-main Backtest run `31698628950`, 2011-01-01~최신 완결 거래일, 매수/매도 수수료 각각 0.1%, 슬리피지 0.1% 기준입니다.

- Total Return: **+2,234.92%**
- CAGR: **22.37%**
- MDD: **-30.93%**
- Sharpe: **1.004**
- Sortino: **1.318**
- 평균 노출: **80.65%**
- allocation 체결: **854건**
- HWM75 최대/최종 sizing base: **$916,499.08**

연구 기준값 22.48% / -30.93% / 1.007과 매우 근접하며, provider 데이터 수정 가능성을 고려해 CI canonical gate는 작은 허용범위를 사용합니다.

## 연구 경고

- 2023+는 후보 선택 과정에서 여러 번 확인되어 pristine OOS가 아닙니다.
- CSCV-style PBO 약 **64.29%** 경고가 남아 있습니다.
- V3.2.2 승격은 대표 승인에 따른 production 전략 계약 변경이며, 과거 성과가 미래 초과수익을 보장한다는 의미가 아닙니다.

## 운영/계좌 규칙

- V3.2.2는 QQQ/TQQQ/SOXL 수량을 자체 SQLite 원장과 Toss 보유수량으로 reconciliation합니다.
- 따라서 같은 Toss 계좌에 개인 **QQQ/TQQQ/SOXL**을 혼합 보유하지 않습니다. 개인 QQQM 등 별도 티커는 원장상 구분 가능합니다.
- V3.1.1 direct H40 포지션/TP plan이 남아 있으면 V3.2.2에서 정상상태로 인정하지 않고 SAFE_MODE 대상으로 봅니다.
- V3.1.1 → V3.2.2 최초 Oracle forced-dry-run 배포에서는 기존 SQLite를 `v322-migration` 이름으로 백업하고 새 원장을 초기화합니다.

## GitHub / Oracle

- 승격 PR **#128** squash merge 완료: `3a613f4`
- GitHub Release **`v3.2.2`** 생성 완료
- 운영 안전·문서 정리 PR **#130** squash merge 완료: `6f7d93f`
- PR #130 Quality Gate run `31704559136`: **SUCCESS**, pytest **197 passed**, Ruff/config 성공
- PR #130 Security run `31704559046`: **SUCCESS**
- PR #130 Backtest run `31704559140`: **SUCCESS**, V3.2.2 canonical gate 재확인
- Oracle direct deploy `6f7d93f`: **SUCCESS**, forced dry-run/service active/Toss read-only smoke 성공
- V3.1.1 → V3.2.2 원장 전환 완료: legacy position 0, open order 0, 기존 DB `v322-migration` 백업 보존
- 배포 후 package/config 3.2.2, exact runtime SHA, forced dry-run/live lock, QQQ·TQQQ·SOXL 시세와 미국장 캘린더 read-only smoke 성공
- Oracle runtime verifier run `31704844851`: **SUCCESS**, phase `pre_market`, service active
- 원격 구버전/종료 브랜치 **49개 삭제 완료**, 보존 원격 브랜치는 `main`만 남음
- 대표 복구 기준은 Git tag `v2.2.2`, `v3.0.0`, `v3.2.2`와 `docs/HISTORY.md`로 보존
- live 활성화는 이번 릴리즈에 포함하지 않았습니다.

## 마지막 완료 작업

- 고등학생 수준의 `docs/ONE_PAGE_REPORT.md`와 용어·QQQ 비교·거래 사이클 작성
- 백테스트·전략·흐름도를 `docs/STRATEGY_GUIDE.md`로 통합하고 대표 역사를 `docs/HISTORY.md`로 압축
- 중복 v1 문서, 종료 연구 스크립트·결과·브랜치 정리
- 멱등 재시도 체결 원장 미반영, 열린 BUY 중복수량, legacy direct 신호, 목표 변경 시 기존 주문, 만료 BUY 취소 결함 수정

## 다음 작업

1. Oracle forced dry-run과 Telegram V3.2.2 배분·승인·SAFE_MODE 흐름을 실제 시장 데이터로 관찰합니다.
2. 다음 `closed` 세션에 필요하면 runtime verifier를 다시 실행해 추가 재시작 검증을 `PASS_RESTARTED`로 완료합니다.
3. 같은 Toss 계좌에 개인 QQQ/TQQQ/SOXL을 혼합하지 않습니다.
4. 실제 주문의 `PARTIAL_FILLED`/`UNKNOWN`, 위험축소 미체결과 reconciliation을 충분히 관찰하기 전에는 live를 승인하지 않습니다.
5. 배포 시 확인된 OpenSSH post-quantum KEX 경고와 `gitleaks-action@v2` Node 20 경고는 기능 변경과 분리한 인프라·의존성 유지보수로 처리합니다.
