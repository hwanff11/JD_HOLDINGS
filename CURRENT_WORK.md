# JD_HOLDINGS Current Work

> 현재 작업·배포·검증 상태의 단일 기준이다. 전략 수치는 `strategy.yaml`과 `docs/JDSS_FINAL_SPEC.md`, 문서 역할은 `docs/README.md`를 따른다.

## 현재 작업

- 운영 안정 기준선: `main`
- 최신 확인 `main` SHA: `1d0031c737a9c09fdf836949215e86a5cc824cb0`
- 활성 작업 브랜치: `agent/code-quality-security-docs-audit`
- 작업 목표: 동작 보존형 코드 정리, 보안 점검·강화, Markdown 중복 제거와 역할 명확화
- 전략 변경: 없음
- 실거래 승격: 금지

## 전략·운영 기준

- 전략·설정·패키지: `JDSS-2.2.2-SGOV` / `2.2.2`
- 공식 계약: `docs/JDSS_FINAL_SPEC.md`
- Oracle: 마지막 검증 상태는 `dry_run`, 빈 `JDSS_LIVE_CONFIRMATION`
- 마지막 검증 Oracle 릴리스: `a98b6717f70d7adca0f118b93de452fccc342dc1`
- 최신 `main`은 Oracle에 아직 배포되지 않음
- 운영 SHA의 최종 확인 기준: Oracle `/home/ubuntu/JD_HOLDINGS/current` 링크

## 최근 main 반영

- PR #18: dry-run SGOV 알림을 `🧪 모의체결`·`🧪 모의처리`로 구분
- PR #20: ChatOps 배포 결과에 Actions run ID와 링크 추가
- PR #18과 #20의 변경은 `main`에 병합됐으나 Oracle에는 미배포

## Oracle ChatOps 상태

- owner 이슈 기반 워크플로 기동과 결과 댓글 작성은 정상 동작
- Actions run `31436429350`: Ruff, pytest 100개, 설정 검증 통과
- 배포 중단 지점: `Prepare SSH`
- 원인: GitHub Environment `oracle-dry-run`의 `ORACLE_SSH_KEY`, `ORACLE_HOST` 미등록
- 운영 서버에는 접속하지 않았으므로 기존 Oracle 서비스와 DB는 변경되지 않음
- SSH 키는 로컬에만 보관 중이며 GitHub 등록·재배포는 보류

## 현재 브랜치 완료 작업

- Toss 성공 응답 JSON 객체 검증과 주문 입력 경계 검증 추가
- Toss 오류·입력 테스트 추가
- systemd UMask·capability·device·kernel·주소 패밀리 제한 강화
- 사용하지 않는 전체 계좌번호 환경변수 예시 제거
- Dependabot 주간 Python·Actions 점검 설정 추가
- Telegram 스케줄러의 Toss 08:50~08:59 KST 유지보수 구간 판정을 표준 `zoneinfo` 기반 함수로 분리
- `AGENTS.md`에 기준 문서 우선순위, 변경 영향표, 코드 품질·보안 규칙 추가
- 문서 안내와 개발 워크플로에서 변동 상태 중복 제거
- `docs/infra/SECURITY.md` 보안 기준 추가

## 검증 상태

- 브랜치 전체 GitHub Actions: 아직 실행 전
- 필수 검증: Ruff, pytest, 설정 검증, JDSS Dry Run, Markdown 링크, 배포 계약
- 기준선: `main` 배포 시도에서 pytest 100개와 Ruff·설정 검증 통과

## 다음 작업

1. 코드 중복·예외처리·모듈 책임 추가 점검
2. Telegram 권한 검사와 오류 노출 회귀 점검
3. Markdown 링크·현행/Archive 경계와 중복 내용 교차 점검
4. 전체 검증 후 발견사항과 잔여 위험 정리
5. PR 생성 전 전략 동작 변경이 없는지 최종 diff 검토

## 작업 종료 갱신 규칙

작업 종료 시 활성 브랜치, 마지막 커밋, 검증 결과, 완료 작업과 다음 작업을 갱신한다. 완료 이력 전체를 누적하지 않고 다음 작업자가 바로 이어가는 데 필요한 현재 상태만 유지한다.
