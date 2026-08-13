# JD_HOLDINGS

JD_HOLDINGS의 현재 전략은 **JDSS-3.2.2-RS6M-ONEWAY-HWM75**입니다. QQQ를 기본으로 보유하고 추세·변동성에 따라 0.5/1.0/1.25/1.5배로 속도를 바꾸며, 반도체 상대강도가 좋을 때 레버리지 부분의 절반을 SOXL로 나눕니다. 기존 H40-S3는 독립 매수전략이 아니라 최대 5% 오버레이입니다.

> V3.2.2는 정식 production 전략이지만 **실거래는 별도 승인 전까지 잠금**입니다. Oracle과 Telegram은 forced dry-run으로 운영합니다.

## 먼저 읽기

- [한 장 보고서](docs/ONE_PAGE_REPORT.md) — 용어, QQQ 비교, 장점·한계, 거래 사이클
- [쉬운 상세 가이드](docs/STRATEGY_GUIDE.md) — 규칙, 예시, 흐름도, 백테스트
- [공식 최종 사양](docs/JDSS_FINAL_SPEC.md) — 구현이 따라야 하는 계약
- [현재 작업 상태](CURRENT_WORK.md) — 브랜치·배포·검증·다음 작업의 단일 기준
- [전체 문서 안내](docs/README.md)

현재 브랜치·Oracle 배포·검증 상태처럼 바뀌는 정보는 `CURRENT_WORK.md`에만 기록합니다.

## 안전 계약

- 시작 위험원금 $50,000, 새 최고자산 이익의 75%만 위험예산에 반영
- SGOV OFF, 손실 시 개인자금 자동보충 없음
- 위험증가 BUY는 Telegram 2단계 승인, 위험축소 SELL은 자동
- 주문 UNKNOWN·원장 불일치·위험축소 미완료 시 SAFE_MODE
- 같은 Toss 계좌에 개인 QQQ/TQQQ/SOXL 혼합보유 금지
- `portfolio.live_enabled: false`와 애플리케이션 live hard lock 유지

## 빠른 시작

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
.venv/bin/jdss validate-config
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/jdss backtest --symbol ALL --start 2011-01-01 --output reports/baseline.json
~~~

Oracle 배포는 원격과 일치하는 최신 `main`만 허용하며 서버 환경을 `dry_run`과 빈 live 확인값으로 강제합니다.
