# JD_HOLDINGS 보안 기준

이 문서는 JDSS의 비밀정보, Telegram 관리자 인증, 주문 승인, Toss API, SQLite, GitHub Actions와 Oracle 배포에 적용하는 현재 보안 기준이다. 현재 브랜치·배포 SHA·점검 진행 상태는 `CURRENT_WORK.md`에서 관리한다.

## 1. 신뢰 경계

| 경계 | 신뢰하는 정보 | 반드시 검증할 정보 |
|---|---|---|
| Telegram | 허용된 관리자 Chat ID 1개 | 메시지 Chat ID, 콜백 사용자·Chat ID, 콜백 형식·토큰·만료 |
| JDSS 애플리케이션 | 검증된 `strategy.yaml`과 내부 상태 모델 | 환경변수, DB 기존 상태, 외부 시세·주문 응답 |
| Toss OpenAPI | TLS로 연결한 공식 고정 호스트 | HTTP 상태, JSON 객체 형식, 필수 응답값, 주문 입력 경계 |
| SQLite | 트랜잭션으로 확정된 JDSS 관리원장 | 브로커 잔고·미체결 주문과의 일치 여부 |
| GitHub Actions | 원격과 정확히 일치하는 최신 `main`과 Environment | 배포 SHA, 요청 주체, Secret 존재 여부 |
| Oracle | commit별 읽기 전용 릴리스 | shared DB·로그·환경파일 권한, 서비스 상태, smoke test |

## 2. 비밀정보

- `.env`, Telegram Bot Token, Toss 앱 키·시크릿, SSH 개인키, 전체 계좌번호와 인증 헤더는 Git에 저장하지 않는다.
- 예제 설정에는 실제 값이나 실제 형식으로 오인될 값을 넣지 않는다.
- 애플리케이션은 Toss 계좌 선택에 필요한 계좌 순번만 사용하며 전체 계좌번호를 보관하지 않는다.
- 로컬 SSH 키는 로컬에 유지한다. GitHub Actions 배포가 필요하면 `oracle-dry-run` Environment에 별도 Secret으로 등록한다.
- 예외·API 원문·주문 응답을 로그에 남길 때 인증정보나 승인 토큰이 포함되지 않는지 확인한다.
- 비밀값 노출이 의심되면 즉시 실거래를 잠그고 해당 자격증명을 폐기·재발급한다.

## 3. Telegram 인증과 승인

- 관리자 Chat ID는 정확히 1개만 허용한다.
- 모든 명령은 메시지 Chat ID를 검사하고, 모든 콜백은 메시지 Chat ID와 클릭한 사용자 ID를 함께 검사한다.
- 매수는 검토 승인과 최종 실행 승인의 2단계를 유지한다.
- 승인 토큰은 암호학적 난수로 생성하고 DB에는 SHA-256 해시만 저장한다.
- 토큰은 상수시간 비교, 만료시간, 승인 단계, 1회 사용 상태를 모두 검증한다.
- 가격·수량·세션이 최종 승인 뒤 바뀌면 이전 실행 토큰으로 주문하지 않고 새 검토를 요구한다.
- 코어 매수는 승인 직전에 신호 당시 계획주수와 현재 managed-equity 잔여 목표주수 중 작은 값을 다시 적용한다.
- 오류 메시지는 관리자에게 필요한 범위만 노출하고 실제 예외는 서버 로그에서 확인한다.

## 4. 주문·자금 안전장치

- 실주문은 `JDSS_TRADING_MODE=live`와 정확한 `JDSS_LIVE_CONFIRMATION`이 동시에 있어야 한다. V3.1은 애플리케이션 계층에서도 live를 계속 차단한다.
- Oracle dry-run 배포는 `deploy.sh`가 거래 모드를 `dry_run`으로 덮어쓰고 실주문 확인값을 비운다.
- 모든 주문은 결정적 client order ID와 DB 예약을 통해 멱등성을 확보한다.
- 모든 BUY는 초기 JDSS 배정금과 JDSS 누적체결로 계산한 managed cash를 초과할 수 없다. 같은 Toss 계좌의 추가 개인 현금은 JDSS 주문가능자금으로 사용하지 않는다.
- 미체결 BUY 잔여금은 수수료까지 포함해 예약한다. managed cash 확인과 BUY 주문예약은 하나의 SQLite `BEGIN IMMEDIATE` 트랜잭션에서 수행해 동시 콜백의 이중예약을 차단한다.
- 실제 브로커 USD 주문가능금액이 managed cash보다 작으면 더 작은 값을 실행 상한으로 사용한다.
- Toss 경계에서 종목 코드, 주문 방향, 주문 유형, 양수 수량과 유한·양수 지정가를 검증한다.
- 같은 Toss 계좌의 개인 SGOV는 관리원장에서 제외할 수 있지만 **개인 TQQQ·SOXL과 JDSS TQQQ·SOXL을 같은 계좌에 혼합 보유하는 운영은 지원하지 않는다.** 합산 잔고로 인해 JDSS 수량을 증명할 수 없으므로 Reconciliation이 SAFE_MODE로 처리한다.
- 재시작 시 DB, 브로커 보유량과 미체결 주문을 대조하고 불일치하면 SAFE_MODE로 전환한다.
- 종목 SAFE_MODE 또는 SGOV 자금 SAFE_MODE에서는 기존 코어 신호를 포함한 모든 신규 BUY 승인·실행을 차단한다.
- 실거래 잠금, 2단계 승인, managed cash, 멱등성, Reconciliation 또는 SAFE_MODE를 약화하는 변경은 별도 승인 없이는 허용하지 않는다.

## 5. 재시작과 주문 정합성

- dry-run 현금은 전체 JDSS 누적체결과 설정 수수료로 재구성해 닫힌 사이클의 실현손익을 유지한다.
- 체결 0으로 상태를 증명할 수 있는 DRY 미체결 주문만 cold restart에서 자동 복원한다.
- `UNKNOWN`과 재시작 시점의 `PARTIAL_FILLED` 주문은 메모리 브로커 상태를 임의로 추정하지 않는다. Reconciliation에서 SAFE_MODE로 남겨 수동 확인 대상으로 둔다.
- DRY broker order sequence는 열린 주문뿐 아니라 완료 주문까지 포함한 전체 이력의 최대 번호 다음부터 이어가 ID 재사용을 막는다.
- 프로세스 내 누적 부분체결은 이전 누적값과의 delta만 보유량·현금에 반영한다.
- DB에는 열린 주문이 있으나 broker order id가 없거나 브로커에서 해당 주문을 찾을 수 없으면 주문 모니터가 반복 예외를 내도록 두지 않고 SAFE_MODE 이벤트를 기록한다.

## 6. API와 네트워크

- Toss API 호스트는 코드의 공식 HTTPS 기본 URL로 고정한다.
- 모든 요청에 연결·응답 시간제한을 적용한다.
- 401은 토큰을 한 번만 갱신해 재시도하고 무한 재귀·무한 재시도를 허용하지 않는다.
- 네트워크·시간초과와 재시도 불가 오류를 구분한다.
- 성공 응답도 JSON 객체인지 확인하고 필수 값이 없으면 실패 처리한다.
- API 오류 응답의 메시지를 명령어나 셸 입력으로 사용하지 않는다.

## 7. SQLite와 로그

- 외부 값은 SQL 파라미터 바인딩으로 처리한다. 동적 SQL은 코드에서 허용 목록으로 만든 열·자리표시자에만 사용한다.
- WAL, foreign key, busy timeout과 `BEGIN IMMEDIATE` 트랜잭션을 유지한다.
- DB·로그·캐시는 릴리스와 분리된 Oracle shared 경로만 쓰기 가능하게 한다.
- systemd `UMask=0077`로 새 파일은 운영 사용자만 접근하도록 한다.
- 이벤트 로그에 승인 토큰, 인증 헤더, 앱 시크릿과 SSH 키를 기록하지 않는다.

## 8. 배포와 실행 권한

- 배포 대상은 깨끗하고 원격과 일치하는 `main` 커밋으로 제한한다.
- ChatOps 요청은 저장소 소유자가 생성한 전용 제목의 이슈만 허용하고, SHA 입력 대신 Actions가 실행 시점의 최신 원격 `main`을 직접 확정한다.
- Actions 권한은 `contents: read`를 기본으로 하고 배포 결과 댓글에 필요한 `issues: write`만 추가한다.
- Oracle 서비스는 비루트 사용자, `NoNewPrivileges`, 빈 capability, private devices/tmp, 커널·control group 보호와 제한된 주소 패밀리를 사용한다.
- 배포 후 서비스 active, 강제 dry-run, 빈 live 확인값, managed cash/equity, Reconciliation과 Toss 조회 전용 smoke test를 확인한다.
- GitHub Secret 또는 Oracle 환경파일이 없으면 배포를 중단하며 우회하지 않는다.

## 9. 의존성 관리

- Python과 GitHub Actions 의존성은 Dependabot으로 매주 확인한다.
- 의존성 업데이트는 기능 변경과 분리하고 CI와 JDSS Dry Run을 통과해야 한다.
- 보안 업데이트라도 전략 결과나 주문 동작에 영향을 줄 수 있으면 회귀 테스트 후 반영한다.

## 10. 변경 전 보안 체크리스트

- [ ] 새로운 비밀값이나 개인정보를 저장·출력하지 않는가
- [ ] Telegram 명령과 콜백에 관리자 검사가 있는가
- [ ] 외부 입력과 API 응답을 경계에서 검증하는가
- [ ] 실주문 잠금과 2단계 승인이 유지되는가
- [ ] managed cash 한도와 미체결 BUY 예약이 유지되는가
- [ ] 주문 멱등성과 DB 트랜잭션이 유지되는가
- [ ] 종목 또는 SGOV SAFE_MODE에서 신규매수가 실제로 차단되는가
- [ ] 재시작·부분체결·오류 시 추정복구보다 안전한 실패를 우선하는가
- [ ] systemd·Actions 권한이 불필요하게 확대되지 않았는가
- [ ] 관련 테스트와 문서가 함께 갱신됐는가