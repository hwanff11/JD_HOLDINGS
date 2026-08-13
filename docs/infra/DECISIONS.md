# 현행 구현 결정

과거 버전과 미채택 연구는 [`HISTORY.md`](../HISTORY.md)로 통합했습니다. 이 문서는 V3.2.2 운영에 직접 필요한 결정만 기록합니다.

## D-001 — V3.2.2 배분 계약

- QQQ를 기본 자산으로 사용하고 목표 노출은 0.5/1.0/1.25/1.5배입니다.
- 20일 연환산 변동성 30% 이상은 0.5배, 강한 추세는 1.5배, 다섯 추세 표 중 세 표는 1.25배입니다.
- SOXX 126일 수익률이 양수이고 QQQ보다 높으면 레버리지 슬리브를 TQQQ/SOXL로 반분합니다.
- 월중 RS 이탈은 SOXL→TQQQ 한 방향만 허용하고 다음 월 reset 전 재진입하지 않습니다.
- H40-S3는 직접 주문하지 않고 QQQ 최대 5% 교체형 virtual overlay로만 사용합니다.

## D-002 — HWM75 자금경계

- 시작 위험원금은 $50,000입니다.
- 완결 거래일 최고 평가액 증가분의 75%만 위험예산을 확대합니다.
- 위험예산은 현재 평가액보다 클 수 없고, 손실을 개인현금으로 자동보충하지 않습니다.
- 매수가능금액은 JDSS 현금, HWM75 잔여 위험예산, 브로커 주문가능금액 중 최솟값입니다.
- 미체결 BUY 금액과 코어 수량을 예약하며 동시 승인은 `BEGIN IMMEDIATE`로 직렬화합니다.

## D-003 — 주문·승인·재시작

- 모든 위험증가 BUY는 검토와 실행의 일회용 토큰 두 단계를 거칩니다.
- 주문은 양수수량·양수가격의 지정가이며 결정적 `clientOrderId`로 멱등성을 확보합니다.
- 멱등 재시도는 브로커 최신 상태를 DB에 저장한 후 새 체결 delta만 원장에 반영합니다.
- 열린 코어 BUY와 아직 원장 미반영인 체결수량은 잔여 목표수량에서 차감합니다.
- 목표 변경 전에 기존 allocation BUY·SELL을 취소·정산하고 만료 BUY를 감시기가 취소합니다.
- 위험축소 SELL은 자동이며 불완전 종료, 주문 UNKNOWN, 원장 불일치는 SAFE_MODE입니다.
- V3.2.2에서는 과거 직접 H40 BUY 신호를 실행하지 않습니다.

## D-004 — 계좌·원장

- JDSS는 QQQ/TQQQ/SOXL을 통합 allocation 원장으로 관리합니다.
- 동일 티커의 개인수량과 JDSS 수량을 Toss가 합산하므로 같은 계좌의 개인 QQQ/TQQQ/SOXL 혼합을 금지합니다.
- SQLite WAL, foreign key, busy timeout, 트랜잭션과 브로커 reconciliation을 유지합니다.
- SGOV는 OFF이고 미투입 자금은 USD입니다.

## D-005 — 실거래·배포

- `portfolio.live_enabled=false`, 애플리케이션 live hard lock, Oracle forced dry-run을 동시에 유지합니다.
- 최신 원격 `main`과 정확히 일치하는 SHA만 Oracle에 배포합니다.
- 배포와 runtime verifier는 읽기 전용 Toss smoke, 서비스 상태, shared DB·환경파일 권한을 검증합니다.
- live 전환은 별도의 명시적 승인과 충분한 dry-run 운영 관찰 없이는 수행하지 않습니다.

## D-006 — 연구와 역사 보존

- 새 연구는 [`RESEARCH_PROTOCOL.md`](../research/RESEARCH_PROTOCOL.md)의 production parity, no-lookahead, OOS, 비용 스트레스 규칙을 따릅니다.
- 일회성 탐색 스크립트·결과·닫힌 연구 브랜치는 `main`에 누적하지 않습니다.
- 대표 소스는 Git tag `v2.2.2`, `v3.0.0`, `v3.2.2`로 보존하고 요약은 [`HISTORY.md`](../HISTORY.md)에 둡니다.
