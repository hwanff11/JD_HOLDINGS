# JH_HOLDINGS

JH_HOLDINGS는 시장 상태에 맞춰 QQQ·TQQQ·SOXL의 목표비중을 조절하고, 사람의 승인과 원장 검증을 거쳐 운용하는 JDSS 전략 시스템입니다.

현재 릴리즈, Oracle 배포 동기화 여부와 운용 모드는 [`CURRENT_WORK.md`](CURRENT_WORK.md)에서 확인합니다. 실거래 활성화 여부도 이 상태판과 공식 계약을 확인하기 전에는 추정하지 않습니다.

## 먼저 읽기

1. [현재 작업·운영 상태](CURRENT_WORK.md) — 릴리즈·배포·검증·다음 작업
2. [한 장 보고서](docs/ONE_PAGE_REPORT.md) — 용어, QQQ 비교, 장점·한계, 거래 사이클
3. [쉬운 상세 가이드](docs/STRATEGY_GUIDE.md) — 전략 규칙, 예시, 흐름도, 기준 백테스트
4. [공식 사양](docs/JDSS_FINAL_SPEC.md) — 구현이 따라야 하는 전략·자금·주문 계약
5. [전체 문서 안내](docs/README.md) — 문서별 소유권과 수명주기

현재 브랜치·Oracle 배포·검증 상태처럼 바뀌는 정보는 `CURRENT_WORK.md`에만 기록합니다. 과거 버전은 새 Markdown을 만들지 않고 [대표 역사](docs/HISTORY.md)와 Git tag에서 찾습니다.

## 안전 원칙

위험을 늘리는 BUY는 Telegram에서 검토와 최종 실행을 각각 확인하는 반자동 방식이고, 목표비중을 낮추는 위험축소 SELL은 자동화 대상입니다. 현재 운용 모드에서는 이 계약이 모의주문에 적용되며, 실제 계좌 조회와 실주문 활성화는 별개의 검증·승인 단계입니다.

정확한 주문·자금 계약은 [공식 사양](docs/JDSS_FINAL_SPEC.md), 운영 방법은 [Telegram 가이드](docs/TELEGRAM_BOT_GUIDE.md), 안전 경계는 [보안 기준](docs/infra/SECURITY.md)을 따릅니다.

## 빠른 시작

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
.venv/bin/jdss validate-config
.venv/bin/pytest
.venv/bin/ruff check .
mkdir -p reports
.venv/bin/jdss backtest --symbol ALL --start 2011-01-01 --output reports/baseline.json
~~~

백테스트 입력과 결과의 의미는 [전략 가이드](docs/STRATEGY_GUIDE.md), Telegram 명령 문법은 [Telegram Bot 운영 가이드](docs/TELEGRAM_BOT_GUIDE.md)를 확인합니다.
