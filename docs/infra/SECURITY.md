# JD_HOLDINGS 보안 기준

이 문서는 JDSS의 비밀정보, Telegram 관리자 인증, 주문 승인, Toss API, SQLite, GitHub Actions와 Oracle 배포에 적용하는 현재 보안 기준이다. 현재 브랜치·배포 SHA·점검 진행 상태는 `CURRENT_WORK.md`에서 관리한다.

## 1. 신뢰 경계

| 경계 | 신뢰하는 정보 | 반드시 검증할 정보 |
|---|---|---|
| Telegram | 허용된 관리자 Chat ID | 메시지/콜백 사용자, 형식, 토큰, 만료 |
| JDSS 애플리케이션 | 검증된 `strategy.yaml`과 내부 상태 모델 | 환경변수, DB 기존 상태, 외부 시세·주문 응답 |
| Toss OpenAPI | TLS 공식 고정 호스트 | HTTP 상태, JSON 형식, 필수 응답값, 주문 경계 |
| SQLite | 트랜잭션으로 확정된 JDSS 원장 | 브로커 잔고·미체결 주문과의 일치 |
| GitHub Actions | 최신 원격 `main`과 Environment | 배포 SHA, 요청 주체, Secret 존재 |
| Oracle | commit별 릴리스 | shared DB·로그·환경파일 권한, 서비스 상태 |

## 2. 비밀정보

- `.env`, Telegram Bot Token, Toss 앱 키·시크릿, SSH 개인키, 전체 계좌번호와 인증 헤더는 Git에 저장하지 않는다.
- 애플리케이션은 Toss 계좌 선택에 필요한 최소 정보만 사용한다.
- GitHub Actions 배포 비밀값은 `oracle-dry-run` Environment Secret으로 관리한다.
- 예외·API 원문·주문 응답에 인증정보나 승인 토큰이 노출되지 않게 한다.
- 비밀값 노출이 의심되면 live를 잠그고 해당 자격증명을 폐기·재발급한다.

## 3. Telegram 인증과 승인

- 허용된 관리자 Chat ID만 명령과 콜백을 처리한다.
- BUY는 검토 승인과 최종 실행 승인의 2단계를 유지한다.
- 승인 토큰은 난수로 만들고 DB에는 SHA-256 해시만 저장한다.
- 토큰은 상수시간 비교, 만료, 승인단계, 1회사용 상태를 모두 검증한다.
- 가격·수량·세션이 바뀌면 이전 토큰으로 주문하지 않고 재검토를 요구한다.
- 코어 BUY는 신호 당시 계획주수와 최종 시점 잔여 목표주수 중 작은 값을 사용한다.

## 4. V3.2.2 HWM75 자금경계

V3.2.2의 가장 중요한 자금 안전장치는 **JDSS가 Toss 전체 현금을 전략자금으로 착각하지 않는 것**이다.

- 시작 위험원금은 `$50,000`이다.
- 완결 거래일 새 최고자산 증가분의 75%만 위험예산을 확대한다.
- 위험예산은 현재 평가액보다 클 수 없다.
- 통합 allocation 원가가 위험예산을 점유한다.
- 미체결 BUY의 잔여 지정가와 예상수수료도 예약한다.
- 열린 코어 BUY와 아직 원장에 반영되지 않은 체결수량도 종목 목표수량에서 예약한다.
- 목표 변경 전에 기존 allocation BUY·SELL 상태를 최신화하고 취소·정산한다.
- 신규 BUY는 HWM75 잔여 위험예산과 종목 잔여 목표수량을 넘을 수 없다.
- 새 최고자산 이익의 나머지 25%는 JDSS 현금이지만 위험예산 확대에 쓰지 않는다.
- JDSS 손실로 전략 가용현금이 줄어도 개인 USD로 자동 보충하지 않는다.
- 실제 Toss 주문가능금액이 JDSS 원장상 가용금액보다 작으면 더 작은 값을 사용한다.
- 같은 Toss 계좌의 개인 USD·QQQM은 JDSS 원장 밖에 둘 수 있다.
- **개인 QQQ·TQQQ·SOXL과 JDSS 수량을 같은 계좌에 혼합 보유하지 않는다.** 브로커 합산수량 때문에 JDSS 원장을 증명할 수 없다.
- SGOV는 production에서 비활성화하며 JDSS 자금경계에 포함하지 않는다.

managed cash 계산과 BUY 주문예약은 SQLite `BEGIN IMMEDIATE` 트랜잭션으로 처리해 두 승인 콜백이 동시에 같은 위험예산과 목표수량을 예약하지 못하게 한다.

## 5. 주문·정합성 안전장치

- 모든 주문은 결정적 client order ID와 DB 예약으로 멱등성을 확보한다.
- Toss 경계에서 종목, 주문방향, 주문유형, 양수수량, 유한·양수 가격을 검증한다.
- 브로커 QQQ/TQQQ/SOXL 기대수량은 JDSS 통합 allocation 원장 수량이다.
- DB/브로커 수량 또는 미체결 주문 불일치는 SAFE_MODE로 전환한다.
- 주문응답 유실은 성공으로 추정하거나 재주문하지 않고 UNKNOWN으로 유지한다.
- TP 부분체결은 누적수량과 신규 delta를 구분해 이중반영하지 않는다.
- 종목 또는 portfolio SAFE_MODE에서는 allocation 신규 BUY 승인과 실행을 차단한다.
- 실거래 잠금, 2단계 승인, HWM75 게이트, 멱등성, Reconciliation, SAFE_MODE를 약화하는 변경은 별도 승인 없이 허용하지 않는다.

## 6. 재시작 안전성

- dry-run raw cash는 $50,000에서 시작해 전체 JDSS 실제 체결과 설정 수수료로 재구성하되 신규 BUY는 별도의 HWM75 위험예산으로 제한한다.
- 체결 0으로 증명되는 DRY 미체결 주문만 cold restart에서 복원한다.
- `UNKNOWN`과 재시작 시점 `PARTIAL_FILLED`는 메모리 브로커 상태를 추정하지 않는다.
- DRY broker order sequence는 완료 주문까지 포함한 전체 역사 최대번호 다음부터 이어간다.
- 누적 부분체결은 이전 누적값과의 delta만 수량·현금에 반영한다.
- DB 열린 주문에 broker order id가 없거나 브로커에서 찾지 못하면 SAFE_MODE 이벤트로 남긴다.

## 7. live 잠금

- Oracle은 forced `dry_run`으로 배포한다.
- `portfolio.live_enabled=false`를 유지한다.
- V3.2.2 애플리케이션은 전체 `live` 시작도 거부한다.
- 배포 스크립트는 서버 `.env`의 `JDSS_TRADING_MODE=dry_run`, 빈 `JDSS_LIVE_CONFIRMATION`을 유지한다.
- live 전환은 백테스트·dry-run·Toss 실제 주문동작 검증과 운영자의 별도 명시적 승인 없이는 하지 않는다.

## 8. API와 네트워크

- Toss API 호스트는 공식 HTTPS 기본 URL로 고정한다.
- 연결·응답 시간제한을 적용한다.
- 401 토큰갱신은 제한된 횟수만 재시도한다.
- 성공 응답도 JSON 객체와 필수값을 검증한다.
- API 오류 문자열을 셸 명령으로 사용하지 않는다.

## 9. SQLite·로그·파일권한

- 외부 값은 SQL 파라미터 바인딩을 사용한다.
- WAL, foreign key, busy timeout과 `BEGIN IMMEDIATE`를 유지한다.
- DB·로그·캐시는 Oracle shared 경로만 쓰기 가능하게 한다.
- systemd `UMask=0077`을 유지한다.
- 승인 토큰·인증헤더·앱시크릿·SSH 키를 이벤트로그에 기록하지 않는다.

## 10. GitHub·Oracle 배포권한

- 배포 대상은 원격과 일치하는 최신 `main`으로 제한한다.
- ChatOps 요청은 저장소 소유자가 생성한 전용 접두어 이슈만 허용한다.
- Actions는 `contents: read`를 기본으로 하고 필요한 경우에만 `issues: write`를 사용한다.
- Oracle 서비스는 비루트 사용자, `NoNewPrivileges`, 빈 capability, private devices/tmp, 커널·control group 보호를 유지한다.
- Secret이나 Oracle 환경파일이 없으면 배포를 중단하고 우회하지 않는다.

## 11. 의존성 관리

- Python과 GitHub Actions 의존성은 Dependabot으로 확인한다.
- 의존성 업데이트는 기능변경과 분리하고 CI와 Dry Run을 통과해야 한다.
- 보안 업데이트라도 전략 결과·주문 동작에 영향을 주면 회귀 테스트한다.

## 12. 변경 전 체크리스트

- [ ] 새로운 비밀값을 저장·출력하지 않는가
- [ ] Telegram 명령·콜백 관리자 검사가 유지되는가
- [ ] live 잠금과 2단계 BUY 승인이 유지되는가
- [ ] 현재 원가 + 열린 BUY + 신규 BUY가 HWM75 위험예산을 넘지 않는가
- [ ] 새 최고자산 이익 중 75%만 위험예산 확대에 반영하는가
- [ ] 코어 보유 + 주문중 + 신규 BUY가 종목 목표수량을 넘지 않는가
- [ ] 개인자금으로 손실을 자동 보충하지 않는가
- [ ] SGOV production 경로가 비활성화되어 있는가
- [ ] 미체결 BUY 예약과 동시성 트랜잭션이 유지되는가
- [ ] 주문 멱등성·Reconciliation·SAFE_MODE가 유지되는가
- [ ] 재시작에서 UNKNOWN/PARTIAL을 추정복구하지 않는가
- [ ] systemd·Actions 권한이 불필요하게 확대되지 않았는가
- [ ] 관련 테스트와 문서가 함께 갱신됐는가
