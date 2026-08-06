# JD_HOLDINGS 개발 협업 워크플로

## 기본 원칙

개발은 GitHub 원격 브랜치를 단일 기준점으로 사용한다.

- 집: 기존 PC의 Codex 환경에서 개발
- 외부: ChatGPT를 통해 GitHub 브랜치의 코드 검토·수정·문서화·리뷰 수행
- 양쪽 모두 동일한 원격 브랜치를 기준으로 이어서 작업
- `main`은 검증된 버전만 반영

## 현재 검토 브랜치

```text
codex/jdss-v1.3.1-review
```

이 브랜치는 `codex/jdss-v1.3`에서 분기되었으며 JDSS v1.3.1 전략 검증 수정본을 담는다.

## 집 PC에서 이어서 개발하기

```bash
git fetch origin
git switch codex/jdss-v1.3.1-review
git pull --ff-only
```

`git switch`를 지원하지 않는 Git 버전에서는:

```bash
git fetch origin
git checkout codex/jdss-v1.3.1-review
git pull --ff-only
```

Codex 작업 전에는 항상 `git status`와 `git pull --ff-only`로 최신 상태를 확인한다.

작업 완료 후:

```bash
git status
git add <변경파일>
git commit -m "변경 내용"
git push origin codex/jdss-v1.3.1-review
```

## 외부에서 ChatGPT로 이어서 개발하기

ChatGPT는 작업 시작 시 원격 브랜치 최신 상태를 다시 읽고 수정한다. 집 PC에서 push한 변경이 있다면 그 커밋을 기준으로 이어서 작업한다.

동일 파일을 집 PC와 ChatGPT에서 동시에 수정하지 않는다. 작업 장소를 전환할 때는 반드시 먼저 push하고, 다음 환경에서 pull 또는 원격 최신 상태 확인 후 작업한다.

## 충돌 방지 규칙

1. 작업 시작 전 최신 원격 상태 확인
2. 한 시점에는 한 환경에서만 동일 브랜치 수정
3. 큰 기능은 가능하면 별도 커밋으로 분리
4. 전략 변경은 문서 → 설정 → 테스트 → 코드 순으로 동기화
5. 실거래 활성화 변경은 별도 PR에서 검토
6. `main` 직접 수정은 지양

## 권장 브랜치 흐름

```text
main
  └─ codex/jdss-v1.3
       └─ codex/jdss-v1.3.1-review
```

v1.3.1 검증이 끝나면 PR을 통해 상위 브랜치 또는 `main`으로 병합한다.

## 인수인계 체크리스트

작업 장소를 바꾸기 전에 다음을 남긴다.

- 마지막 커밋 SHA
- 완료한 작업
- 미완료 작업
- 테스트 결과
- 다음 작업 우선순위
- 전략/설정 변경 여부

이 정보를 커밋 메시지, PR 설명 또는 개발 문서에 남겨 집 Codex와 ChatGPT가 같은 상태에서 이어서 작업할 수 있도록 한다.
