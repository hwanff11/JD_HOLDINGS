# JH_HOLDINGS 보안 기준

이 문서는 비밀정보, Telegram 관리자 인증, 주문 승인, Toss API, SQLite, GitHub Actions와 Oracle 배포의 **기술적 안전 경계**를 소유합니다. 정확한 현재 전략·자금 수치는 [`../JDSS_FINAL_SPEC.md`](../JDSS_FINAL_SPEC.md)와 [`../../strategy.yaml`](../../strategy.yaml), 배포·live 상태는 [`../../CURRENT_WORK.md`](../../CURRENT_WORK.md)를 따릅니다.

## 1. 신뢰 경계

| 경계 | 신뢰하는 정보 | 반드시 검증할 정보 |
|---|---|---|
| Telegram | 허용된 관리자 Chat ID | private chat, 메시지/콜백 사용자, 단계, 토큰, 만료, stale button |
| JDSS 애플리케이션 | 검증된 설정과 트랜잭션으로 확정된 내부 상태 | 환경변수, 기존 DB, 외부 시세·주문 응답 |
| Dry-run broker | 현재 프로세스와 SQLite로 증명한 모의 주문 상태 | 재시작 복원, 부분체결 delta, 열린 주문 |
| Toss OpenAPI | 공식 HTTPS 고정 호스트 | 인증·HTTP·JSON·필수값·실제 계좌 수량과 주문 상태 |
| GitHub Actions | 보호된 `main`과 승인된 Environment | 배포 SHA, 요청 주체, Secret, workflow 권한 |
| Oracle | release와 protected shared 경로 | SSH host key, 환경파일·DB·로그 권한, 서비스·원장 상태 |

Dry-run broker와 Toss OpenAPI는 서로 다른 경계입니다. 한쪽의 성공·수량을 다른 쪽의 성공·수량으로 간주하지 않습니다.

## 2. 공개 저장소와 비밀정보

저장소가 public이어도 코드 자체가 자격증명이 되지 않도록 설계합니다.

- `.env`, Telegram Bot Token, Toss 앱 키·시크릿, SSH 개인키, 전체 계좌번호, 인증 헤더, GitHub Environment secret을 Git·로그·문서·Issue에 저장하지 않습니다.
- 공개 Markdown에는 서버 절대경로·OS 사용자명·서비스 실명·backup/snapshot 파일명·host 식별자·일회성 실행 ID를 남기지 않습니다. 정확한 운영 식별자는 보호된 배포 설정과 비공개 운영 기록에서만 관리합니다.
- `.env.example`에는 키 이름과 안전한 비밀 아닌 기본값만 둡니다.
- Actions 비밀값은 승인된 Environment Secret으로 관리합니다.
- 예외·API 원문·주문 응답에 인증정보나 승인 토큰이 노출되지 않게 합니다.
- Gitleaks는 전체 Git history를 검사하고, 의심 노출이 있으면 live를 잠근 뒤 해당 자격증명을 폐기·재발급합니다.
- 전략 수식·운영 구조는 public 저장소에서 누구나 볼 수 있음을 전제로 합니다. 영업비밀로 유지해야 하는 내용은 별도 승인 없이 추가 공개하지 않습니다.

## 3. GitHub `main` 보호

`main`은 운영 배포의 출발점이므로 GitHub branch protection/ruleset을 필수로 사용합니다.

- PR 없이 직접 변경 금지
- force push 금지
- branch delete 금지
- 필수 CI: `Quality Gate`, `Security`
- 전략 성과·실행 경로를 바꾸는 PR은 canonical `Backtest` 검증도 확인
- 가능하면 conversation resolution과 최신 base 반영을 요구
- Actions 기본 권한은 `contents: read`이며 필요한 workflow만 최소 권한 추가

보호 설정이 꺼져 있으면 코드가 정상이어도 production 변경통제는 미완료로 봅니다.

## 4. Telegram 인증·승인·최초진입

- 정확히 1개의 관리자 Chat ID만 허용합니다.
- 명령과 callback 모두 `private` chat인지, `chat.id`와 실제 `from_user.id`가 관리자와 같은지 확인합니다.
- 위험증가 BUY는 가격·수량 검토와 최종 실행의 2단계 승인을 모두 요구합니다.
- 승인 토큰은 암호학적 난수로 만들고 DB에는 SHA-256 hash만 저장하며 상수시간 비교, 단계, 만료, 1회사용을 검증합니다.
- 가격·수량·세션이 바뀌면 이전 토큰으로 주문하지 않고 재검토합니다.
- 최초진입 단계 버튼은 callback에 생성 당시 단계번호를 포함하고, 현재 DB 단계와 다르면 stale button으로 거부합니다.
- 50→75→100 다음 단계는 현재 단계 전량 체결과 최소 대기 거래일을 다시 확인합니다.

## 5. 전략자금 경계

- 초기 위험원금, HWM 이익 재투자율과 공식은 설정·사양을 직접 읽습니다.
- 위험예산은 현재 평가액보다 클 수 없고 JDSS 손실을 개인 현금으로 자동 보충하지 않습니다.
- 기존 allocation 원가와 미체결 BUY의 잔여 지정가·예상수수료를 위험예산에서 예약합니다.
- 열린 코어 BUY와 아직 원장에 반영되지 않은 체결수량도 종목 잔여 목표에서 예약합니다.
- 목표 변경 전에 기존 allocation BUY·SELL 상태를 최신화하고 취소·정산합니다.
- 신규 BUY는 위험예산, JDSS 원장상 현금, 브로커 주문가능금액과 종목 잔여 목표 중 가장 제한적인 경계를 넘지 않습니다.
- 같은 Toss 계좌의 개인 QQQ/TQQQ/SOXL을 JDSS 수량과 혼합하지 않습니다.

BUY 주문예약은 SQLite `BEGIN IMMEDIATE` 트랜잭션 안에서 현금과 목표수량을 함께 다시 검사해 동시 승인으로 한도를 이중 사용하지 못하게 합니다.

## 6. 주문·정합성 안전장치

- 모든 주문은 결정적 client order ID와 DB 예약으로 멱등성을 확보합니다.
- 브로커 경계에서 종목, 주문방향, 주문유형, 양수수량, 유한·양수 가격을 검증합니다.
- 브로커 receipt의 client order ID, broker order ID, 종목, 방향, 주문수량, 체결수량을 예약값과 대조합니다.
- 동일 주문 재시도는 브로커 최신 receipt를 먼저 저장하고 새 체결 delta만 원장에 반영합니다.
- DB/브로커 보유수량 또는 열린 주문 불일치는 SAFE_MODE로 전환합니다.
- 주문응답 유실은 성공으로 추정하거나 재주문하지 않고 `UNKNOWN`으로 유지합니다.
- 누적 부분체결은 이전 적용값과 신규 누적값의 delta만 반영하고 누적 체결수량 감소를 거부합니다.
- 위험축소 SELL은 종료·원장 반영·정합성 확인이 끝나기 전 신규 BUY를 허용하지 않습니다.
- SAFE_MODE는 정상 조회 한 번으로 자동 해제하지 않습니다.

## 7. 시작·재시작 안전성

- 서비스 시작 시 DB 전략 세대·schema와 현재 설정의 호환성을 확인합니다.
- dry-run 보유수량과 현금은 증명 가능한 체결·수수료로 복원합니다.
- 체결 0으로 증명되는 DRY 미체결 주문만 cold restart에서 복원합니다.
- `UNKNOWN`과 재시작 시점 `PARTIAL_FILLED`를 메모리 상태로 추정 복구하지 않습니다.
- DRY broker order sequence는 완료 주문을 포함한 역사상 최대번호 다음부터 이어갑니다.
- 시작 시 아직 allocation 원장에 반영되지 않은 확정 체결은 누적 delta만 한 번 반영합니다.
- 열린 주문의 broker ID가 없거나 현재 브로커에서 찾지 못하면 SAFE_MODE 이벤트를 남깁니다.

## 8. live 잠금

- 정확한 현재 상태는 `CURRENT_WORK.md`를 확인합니다.
- `portfolio.live_enabled=false`, 애플리케이션 live hard lock, Oracle forced dry-run, 빈 live confirmation을 함께 유지합니다.
- live 전환은 백테스트·dry-run·실제 Toss 주문 경계·최초 계좌 적용 preflight와 별도 명시적 승인 없이는 하지 않습니다.
- read-only smoke 성공, 문서 체크리스트 추가, 서비스 active만으로 live 준비 완료라고 판단하지 않습니다.

## 9. Toss API·네트워크

- Toss API 호스트는 공식 HTTPS 기본 URL로 고정합니다.
- 연결·응답 timeout을 적용하고 401 token refresh는 제한된 횟수만 재시도합니다.
- 성공 응답도 JSON 객체, 필수값, 숫자 범위와 주문 상태를 검증합니다.
- API 오류 문자열을 셸 명령으로 사용하지 않습니다.
- 유지보수 시간과 일시적 장애를 주문 성공 또는 미보유 상태로 바꾸지 않습니다.

## 10. SSH·Oracle

- GitHub Actions와 로컬 배포 모두 `StrictHostKeyChecking=yes`를 사용합니다.
- Oracle host public key는 Oracle 콘솔 또는 기존 신뢰 경로로 확인한 뒤 known_hosts에 고정합니다.
- Actions 중 `ssh-keyscan` 결과를 즉석 신뢰하거나 `accept-new`로 우회하지 않습니다.
- Actions는 `ORACLE_SSH_KNOWN_HOSTS`가 없으면 배포·runtime verifier를 중단합니다.
- Oracle 서비스는 비루트 사용자, `NoNewPrivileges`, 빈 capability, private devices/tmp와 커널·control group 보호를 유지합니다.
- DB·로그·캐시는 `shared`만 쓰기 가능하게 하고 systemd `UMask=0077`을 유지합니다.

## 11. 배포·DB rollback

- 배포 대상은 원격과 일치하는 최신 `main`으로 제한합니다.
- 새 release는 release 내부 `.venv`에서 미리 설치·검증하고 기존 서비스는 그동안 계속 실행합니다.
- 서비스를 멈춘 직후 SQLite `backup()` API로 일관된 DB snapshot을 만듭니다.
- `current` symlink와 systemd unit을 새 release로 바꾼 뒤 config/init-db/service/read-only smoke를 검증합니다.
- stop 이후 실패하면 직전 current, systemd unit, DB snapshot을 자동 복원하고 직전 서비스를 다시 시작합니다.
- 표준 deploy는 config version 변경을 수행하지 않습니다. 버전 변경은 별도 migration plan·호환성 테스트·백업을 요구합니다.
- 실제 거래원장을 자동 삭제하지 않습니다.

## 12. Security workflow

- `pip-audit`로 Python dependency 취약점을 검사합니다.
- `bandit`으로 Python 코드의 일반 보안 패턴을 검사합니다.
- CodeQL 결과는 GitHub Code Scanning에 업로드합니다.
- Gitleaks는 `fetch-depth: 0`으로 전체 Git history를 검사합니다.
- Dependabot은 Python과 GitHub Actions 의존성을 주기적으로 확인합니다.
- CI는 coverage를 측정할 뿐 아니라 최소 하한을 적용해 안전경계 테스트가 조용히 사라지는 것을 방지합니다.

## 13. 변경 전 체크리스트

- [ ] 새로운 비밀값을 저장·출력하지 않는가
- [ ] public 저장소에 불필요한 비밀 운영정보를 추가하지 않는가
- [ ] `main` 보호와 필수 CI가 유지되는가
- [ ] Telegram private/admin 검사와 stale callback 차단이 유지되는가
- [ ] live 잠금과 반자동 BUY 2단계 승인이 유지되는가
- [ ] 위험축소 SELL 실패·부분체결·UNKNOWN 뒤 신규 BUY가 차단되는가
- [ ] 현재 원가 + 열린 BUY + 신규 BUY가 위험예산과 목표수량을 넘지 않는가
- [ ] dry-run 모의원장과 실제 Toss read-only 조회를 명확히 구분하는가
- [ ] 주문 멱등성·부분체결 delta·reconciliation·SAFE_MODE가 유지되는가
- [ ] SSH host key가 검증된 known_hosts에 고정돼 있는가
- [ ] 배포 전 DB snapshot과 자동 rollback이 가능한가
- [ ] config version 변경을 표준 deploy가 임의로 처리하지 않는가
