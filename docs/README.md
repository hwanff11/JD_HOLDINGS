# JD_HOLDINGS 문서 안내

이 디렉터리는 JDSS V3.1.1 TWIN-H40-S3의 개발·운영 문서와 과거 연구 기록을 함께 보관한다. 다른 환경의 Codex, ChatGPT, Antigravity는 아래 순서로 읽는다.

## 작업 시작 순서

1. [`../CURRENT_WORK.md`](../CURRENT_WORK.md): 현재 브랜치·배포·검증·다음 작업
2. [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md): 현재 전략·자금·주문의 공식 계약
3. [`../strategy.yaml`](../strategy.yaml): 코드가 실제로 읽는 숫자 설정
4. 작업 종류에 맞는 운영 문서
5. [`infra/DEVELOPMENT_WORKFLOW.md`](infra/DEVELOPMENT_WORKFLOW.md): 브랜치·검증·인수인계 절차

GitHub `main`이 소스의 기준이다. 동일 브랜치를 여러 환경에서 동시에 수정하지 않는다.

## 문서별 역할

| 문서 | 역할 | 기준 |
|---|---|---|
| [`../CURRENT_WORK.md`](../CURRENT_WORK.md) | 최근 인수인계·Oracle 상태 | 현재 상태 최종 기준 |
| [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md) | JDSS-3.1.1-TWIN-H40-S3 전략·자금·주문 계약 | 현재 전략 최종 기준 |
| [`STRATEGY_GUIDE.md`](STRATEGY_GUIDE.md) | 쉬운 전략·용어 설명 | 사용자 이해용 현재 기준 |
| [`BACKTEST_REPORT.md`](BACKTEST_REPORT.md) | production 백테스트 조건·성과·한계 | 검증 기준 |
| [`TELEGRAM_BOT_GUIDE.md`](TELEGRAM_BOT_GUIDE.md) | Telegram 명령·승인·SAFE_MODE | 현재 UI 기준 |
| [`infra/DEPLOYMENT.md`](infra/DEPLOYMENT.md) | Oracle dry-run 배포·검증·롤백 | 배포 기준 |
| [`infra/DEVELOPMENT_WORKFLOW.md`](infra/DEVELOPMENT_WORKFLOW.md) | Codex·ChatGPT·Antigravity 협업 절차 | 개발 기준 |
| [`infra/SECURITY.md`](infra/SECURITY.md) | 비밀정보·인증·주문·DB·배포 보안 | 보안 기준 |
| [`infra/DECISIONS.md`](infra/DECISIONS.md) | 구현 결정과 변경 이유 | 누적 결정 기록 |
| [`research/PR27_SIMPLE_STRATEGY_RESEARCH.md`](research/PR27_SIMPLE_STRATEGY_RESEARCH.md) | 과거 PR #27 연구 | 연구 이력 |
| [`archive/spec_v1/`](archive/spec_v1/) | v1 계열 명세 | Archive 전용 |
| [`../configs/strategy_v1.1.2.yaml`](../configs/strategy_v1.1.2.yaml) | v1.1.2 연구 재현 | Archive 전용 |

## V3.1.1에서 특히 바뀐 문서 계약

- JDSS 위험원금: 고정 $50,000
- 자체수익·월급·추가입금: 사이징에 재투자하지 않음
- SGOV: OFF, 유휴원금 USD 현금
- H40: 종목당 $20,000
- S3 최대 신규투입: 종목당 $18,000
- 개인 QQQ/QQQM·USD는 JDSS와 분리 가능
- 개인 TQQQ/SOXL은 같은 Toss 계좌 혼합 금지

## 변동 상태 기록 원칙

현재 브랜치, 최신 `main` SHA, Oracle 배포 SHA, 테스트 개수, 미완료 작업은 자주 바뀌므로 이 문서에 복제하지 않는다. 해당 정보는 [`../CURRENT_WORK.md`](../CURRENT_WORK.md)에서만 관리한다.

## 과거 문서 사용 규칙

`Archive`, `Legacy`, `v1.x`, `v2.x`, `V3.0`, `V3.1.0`으로 표시된 과거 값은 연구 재현용일 수 있다. 현재 수치와 충돌하면 루트 `strategy.yaml`과 `JDSS_FINAL_SPEC.md`를 우선한다. 둘이 서로 다르면 코드를 변경하기 전에 불일치로 보고한다.
