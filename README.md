# JD_HOLDINGS

JDSS V3.1.1은 QQQ·SOXX의 월간 추세를 따르는 TQQQ·SOXL 코어와 과매도·반등 H40-S3 부스터를 결합한 Telegram 승인형 반자동 매매 봇입니다. 현재 개발 기준은 **JDSS-3.1.1-TWIN-H40-S3**입니다.

> 현재 브랜치·Oracle 배포·검증 상태는 [`CURRENT_WORK.md`](CURRENT_WORK.md)에서만 관리한다. 실거래 승격은 별도 승인 전까지 금지한다.

## V3.1.1 핵심 계약

- JDSS 고정 전략원금 **$50,000**
- JDSS 자체수익은 다음 매매 사이징에 재투자하지 않음
- 월급·추가입금·개인 USD·QQQ/QQQM은 JDSS 자금에서 제외
- 손실 시 개인자금 자동보충 없음
- 개인 TQQQ/SOXL의 같은 Toss 계좌 혼합 보유 금지
- SGOV **OFF**, 미투입 JDSS 원금은 USD 현금
- 코어: QQQ/SOXX 완료 월말 종가와 6개월 이동평균
- 코어 목표: 첫 ON 10%($5,000), 지속 ON 15%($7,500) — 종목별
- 부스터 H40 cap: 종목당 **$20,000**, S3 실제 최대 신규투입 **$18,000**
- S3 분할: 40% / 30% / 20%, 누적 40% / 70% / 90%
- 부스터 진입: Score 55 이상, Reversal Score 5 이상, `Regime != RED`
- 추가매수: 최초 체결가 대비 -2% / -5%
- 익절: TP1 +4%에서 약 30%, TP2 +10%에서 잔량
- 자동손절·재매수·기간강제청산 OFF
- SOXL 섹터가드: SOXX/SMH EMA60 기준 1·3차 차단
- 모든 BUY는 Telegram 2단계 승인
- 위험축소 코어 SELL과 TP SELL은 자동
- forced dry-run, live 잠금 유지

## 현재 production 백테스트

고정 $50,000 / 수익 재투자 OFF / SGOV OFF 계약 기준:

| 지표 | 결과 |
|---|---:|
| Total Return | +414.80% |
| CAGR | 11.07% |
| MDD | -22.97% |
| Sharpe | 0.839 |
| 평균 노출 | 21.28% |
| 코어 체결 | 319 |
| 부스터 체결 | 443 |
| 최대 원가투입 | $45,951.75 |

상세 조건과 한계는 [`docs/BACKTEST_REPORT.md`](docs/BACKTEST_REPORT.md)를 따른다.

## 문서 읽는 순서

1. [`CURRENT_WORK.md`](CURRENT_WORK.md) — 현재 배포·검증 상태
2. [`docs/STRATEGY_GUIDE.md`](docs/STRATEGY_GUIDE.md) — 쉬운 전략 설명
3. [`docs/JDSS_FINAL_SPEC.md`](docs/JDSS_FINAL_SPEC.md) — 공식 전략·자금·주문 계약
4. [`docs/TELEGRAM_BOT_GUIDE.md`](docs/TELEGRAM_BOT_GUIDE.md) — Telegram 운영
5. [`docs/BACKTEST_REPORT.md`](docs/BACKTEST_REPORT.md) — 검증 결과
6. [`docs/infra/DEVELOPMENT_WORKFLOW.md`](docs/infra/DEVELOPMENT_WORKFLOW.md) — 협업 절차
7. [`docs/infra/DEPLOYMENT.md`](docs/infra/DEPLOYMENT.md) — Oracle 배포
8. [`docs/infra/SECURITY.md`](docs/infra/SECURITY.md) — 보안 기준

## 구현 범위

- 완료 미국 거래일·완료 월말 데이터만 사용하는 분석
- 6개월 월간 쌍발 코어
- JDSS 점수 기반 H40-S3 부스터
- 고정원금 $50,000 주문게이트와 비복리 회계
- 코어·부스터 분리 SQLite 원장과 브로커 합산 Reconciliation
- Telegram `검토 → 최종 실행` 2단계 BUY 승인
- 부분체결·UNKNOWN·재시작 SAFE_MODE
- Toss Securities OAuth2/OpenAPI 어댑터
- GitHub Actions CI/Security/Backtest/Oracle dry-run 배포

SGOV 레거시 컴포넌트 코드는 회귀·이력 호환을 위해 일부 남아 있지만 production 설정에서는 비활성화하며 Bot 런타임에서 manager를 만들지 않는다.

## 빠른 시작

Python 3.11 이상이 필요합니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
chmod 600 .env
.venv/bin/jdss validate-config
.venv/bin/pytest
.venv/bin/ruff check .
```

```bash
.venv/bin/jdss analyze
.venv/bin/jdss backtest --symbol ALL --start 2011-01-01 --output reports/baseline.json
.venv/bin/jdss-bot
```

## 주문 안전장치

V3.1.1은 `portfolio.live_enabled: false`와 애플리케이션 시작 잠금으로 전체 live 모드를 거부합니다. 과거 live 환경변수가 설정돼 있어도 V3.1.1에서는 실주문이 열리지 않습니다.

`toss-smoke`는 인증·현재가·미국 장 상태만 조회하고 주문을 만들지 않습니다.

```bash
.venv/bin/jdss toss-smoke
```

## 배포

상세 절차는 [`docs/infra/DEPLOYMENT.md`](docs/infra/DEPLOYMENT.md)를 따른다. `.github/workflows/deploy-oracle-dry-run.yml`은 정확한 최신 `main`을 재검증한 뒤 Oracle 서버에 배포하고 forced dry-run과 조회 전용 Toss smoke test를 확인한다.
