# JD_HOLDINGS

JDSS(JH Dynamic Score Swing Strategy)는 TQQQ와 SOXL의 일봉 과매도·반등을 이용하고 유휴 전략자금을 SGOV로 운용하는 Telegram 승인형 반자동 매매 봇입니다. 현재 개발 기준은 **JDSS-2.2.1-SGOV**이다.

> 현재 상태: Oracle에는 **JDSS-2.2.0-SGOV `598a550`**가 `dry_run` 배포되어 있다. **JDSS-2.2.1-SGOV는 `/sgov` 명령 전환과 신호 DB 검증 수정까지 포함한 다음 배포본이다.** 실거래 승격은 금지된 상태다. 변동 가능한 최신 상태는 [`CURRENT_WORK.md`](CURRENT_WORK.md)를 확인한다.

## JDSS 2.2 전략 요약

- 대상: TQQQ, SOXL / 종목당 전략자금 $10,000
- 모든 매수 단계: Score 55 이상, Reversal Score 5 이상, `Regime != RED`
- 분할매수: 40% / 30% / 20% / 10%, 최초 체결가 대비 0% / -2% / -5% / -7%
- 익절: 평단 +4%에서 약 50%, +6%에서 잔량
- TP1 완전체결 후 20개 완결 거래일 동안 TP2 미체결 시 잔량을 평단 +2% 주문으로 전환
- SOXL 섹터 가드: SOXX/SMH EMA60 기준으로 1·3·4차 차단
- 자동손절·재매수 없음, 모든 매수는 2단계 사용자 승인 필수
- TQQQ/SOXL에 쓰지 않은 배정금은 SGOV로 운용하고 계좌에 최소 `$250`를 남김
- 전략 매수 전 필요한 SGOV 관리분을 먼저 매도하며, 현금화 미완료 시 본 주문 차단
- 기존 개인 SGOV는 JDSS 관리분으로 자동 편입하거나 매도하지 않음

처음 저장소를 인수하는 환경은 [문서 안내](docs/README.md)와 [현재 작업 상태](CURRENT_WORK.md)를 먼저 읽으세요. 정식 계약은 [JDSS 2.2 사양](docs/JDSS_FINAL_SPEC.md), 운영 이력은 [전략 가이드](docs/STRATEGY_GUIDE.md), 검증 기록은 [백테스트 보고서](docs/BACKTEST_REPORT.md), 협업 절차는 [개발 워크플로](docs/infra/DEVELOPMENT_WORKFLOW.md)를 참고합니다.

## 구현 범위

- 완결 미국 거래일 검증과 yfinance 수정주가 일봉 분석
- 노룩어헤드 백테스트와 실거래 공용 JDSS 2.2 전략 규칙
- SQLite WAL, 상태 전이, 낙관적 잠금, 신호·주문 멱등성
- Telegram 관리자 1명 제한과 검토 → 최종 실행의 2단계 매수 승인
- Toss Securities OAuth2/OpenAPI 어댑터와 실주문 이중 잠금
- 부분체결, TP 자동복구, `REMAINDER_EXIT`, 재시작 Reconciliation과 SAFE_MODE
- JDSS 관리 SGOV 전용 원장, 자동 예치·선현금화·부분체결·정합성 SAFE_MODE
- Telegram `/sgov`와 SGOV 수익을 반영하는 CLI·Telegram 백테스트
- 별도 `jd_holdings_bot.service`와 commit별 Oracle 릴리스 배포

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

Telegram에서는 `/bt NVDA 100`, `/bt TSLA`, `/bt ALL 250`처럼 실제 주문 없이 임의 티커 백테스트를 실행할 수 있습니다.

## 주문 안전장치

기본값인 `JDSS_TRADING_MODE=dry_run`에서는 Toss 주문 API를 호출하지 않습니다. 실주문은 다음 두 값을 모두 정확히 설정해야만 열립니다.

```dotenv
JDSS_TRADING_MODE=live
JDSS_LIVE_CONFIRMATION=ENABLE_JDSS_LIVE_ORDERS
```

`toss-smoke`는 인증, 현재가, 미국 장 상태만 조회하며 주문을 만들지 않습니다.

```bash
.venv/bin/jdss toss-smoke
```

## 배포

상세 절차와 롤백은 [Oracle 배포 가이드](docs/infra/DEPLOYMENT.md)를 따릅니다. `.github/workflows/deploy-oracle-dry-run.yml`은 지정한 `main` 커밋을 재검증하고 서버를 강제로 `dry_run`에 잠근 뒤 조회 전용 smoke test까지 실행합니다. 로컬 `deploy.sh`를 사용할 때도 깨끗하고 원격과 동기화된 `main`에서만 실행합니다.

## 면책

이 저장소는 투자 성과를 보장하지 않습니다. 레버리지 ETF와 무손절 전략은 원금 손실, 장기 자금 고착, 급격한 변동 위험이 큽니다. 실거래 전 전략 유효성과 주문·복구 동작을 직접 확인해야 합니다.
