# JD_HOLDINGS Current Work

> 현재 작업·배포·검증 상태의 단일 기준이다. 전략 수치는 `strategy.yaml`과 `docs/JDSS_FINAL_SPEC.md`, 문서 역할은 `docs/README.md`를 따른다.

## 현재 작업

- 운영 안정 기준선: `main`
- PR #22 병합 반영 기준 SHA: `065cd0c22a4313d809b91174b4215ac0b1435d82`
- PR #24 병합: GitHub Actions 백테스트·보안검사 자동화 (`8f1e970bb77fc6901bcbbea8bae36f735d3d4e44`)
- PR #25 병합: Telegram 최근 점수 이력 조회 (`5e9090ee803030be8a713bd2486ce56b4f83e944`)
- 활성 작업 브랜치: `agent/chatops-latest-main-deploy`
- 작업 목표: ChatGPT의 단일 이슈 요청으로 최신 `main` Oracle dry-run 배포
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
- PR #24: 수동 JDSS 백테스트(입력·Artifact·Issue 댓글)와 Security(CodeQL·Gitleaks·pip-audit·Bandit) 자동화
- PR #25: `/history`, `/h` 최근 1~90거래일 점수 조회 추가. 기존 도움말 별칭은 `/menu`로 변경

## Oracle ChatOps 상태

- owner 이슈 기반 워크플로 기동과 결과 댓글 작성은 정상 동작한다.
- Issue #28은 Actions run `31489316669`를 정상 기동했으며, 트리거가 아니라 pytest 2건 실패로 Oracle 접속 전에 중단됐다.
- ChatOps 이슈에는 SHA를 넣지 않고, Actions가 실행 시점의 최신 원격 `main`을 직접 확정하도록 변경 중이다.
- GitHub Environment `oracle-dry-run`의 Oracle secret은 Actions에서만 사용하고 저장소·이슈·ChatGPT에 노출하지 않는다.

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
- Telegram 취소 콜백의 권한 거부와 미지원 유형을 fail-closed로 처리
- 로컬 SSH·개인키 형식을 `.gitignore`에 추가
- Markdown 링크·문서 역할·변동 상태 단일 기준·레거시 설정 경계를 계약 테스트로 고정
- GitHub Actions PR 검증 121개 테스트 통과, JDSS Dry Run 성공
- `/history` 점수 이력 입력·출력 테스트 추가

## 검증 상태

- PR #22·#24·#25: `main` 병합 완료
- CI run `31439294132`: Ruff 통과, pytest 114개 통과, 전체 커버리지 69%, 설정 검증 통과
- JDSS 2.2 Dry Run run `31439294213`: 성공
- Markdown 링크·문서 역할·변동 상태 단일 기준·레거시 설정 경계 테스트 통과
- 배포 계약과 systemd 보안 옵션 테스트 통과
- 전략 수치와 실거래 잠금 변경 없음

## 다음 작업

1. ChatOps 수정 PR의 CI와 JDSS Dry Run을 통과시켜 `main`에 병합
2. SHA 없는 owner 이슈로 최신 `main` 배포를 실행하고 systemd active·강제 dry-run·Toss smoke 결과 확인

## 작업 종료 갱신 규칙

작업 종료 시 활성 브랜치, 마지막 커밋, 검증 결과, 완료 작업과 다음 작업을 갱신한다. 완료 이력 전체를 누적하지 않고 다음 작업자가 바로 이어가는 데 필요한 현재 상태만 유지한다.
