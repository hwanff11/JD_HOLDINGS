# JD_HOLDINGS

JDSS V3.1은 QQQ·SOXX의 월간 추세를 따르는 TQQQ·SOXL 코어와 과매도·반등 JDSS 부스터를 결합하고, 나머지 자금을 SGOV로 운용하는 Telegram 승인형 반자동 매매 봇입니다. 현재 개발 기준은 **JDSS-3.1.0-TWIN-H40-S3**입니다.

> 현재 브랜치·Oracle 배포·검증 상태는 [`CURRENT_WORK.md`](CURRENT_WORK.md)에서만 관리한다. 실거래 승격은 별도 승인 전까지 금지한다.

## JDSS V3.1 전략 요약

- 총 전략자금 `$20,000`
- 완료된 월말 QQQ·SOXX 종가가 각 6개월 이동평균 위이면 다음 거래일 대응 TQQQ·SOXL 코어 활성화
- 코어 목표: 추세가 새로 ON 된 첫 달 10%, 다음 월말에도 유지되면 15%
- JDSS 부스터: 종목당 자금 상한 `$8,000`(초기 총자금의 40%); 현재 3단계 누적비중은 90%라 한 사이클의 정상 최대 투입액은 `$7,200`(총자금의 36%)
- 부스터 매수: Score 55 이상, Reversal Score 5 이상, `Regime != RED`
- 분할매수: 3단계 40% / 30% / 20%, 누적 40% / 70% / 90%; 최초 체결가 대비 0% / -2% / -5%
- 익절: 평단 +4%에서 약 30%, +10%에서 잔량
- TP1 이후 기간 기준 잔여청산 없음; 자동손절·재매수 없음
- SOXL 섹터 가드: SOXX/SMH EMA60 기준으로 1·3차 차단
- 코어·부스터 매수는 2단계 사용자 승인, 코어 위험축소 매도는 자동
- TQQQ/SOXL에 쓰지 않은 배정금은 SGOV로 운용하고 계좌에 최소 `$250`를 남김
- 전략 매수 전 필요한 SGOV 관리분을 먼저 매도하고, 체결 후 `/signal` 재실행 없이 TQQQ/SOXL 최종 승인 버튼을 자동 재개
- SGOV는 매수 최우선 매도호가 `+$0.01`, 매도 최우선 매수호가 `-$0.01`의 시장가성 지정가를 사용하고 60초 미체결 시 취소·재가격
- 기존 개인 SGOV는 JDSS 관리분으로 자동 편입하거나 매도하지 않음

처음 저장소를 인수하는 환경은 [문서 안내](docs/README.md)와 [현재 작업 상태](CURRENT_WORK.md)를 먼저 읽으세요. 정식 계약은 [JDSS V3.1 사양](docs/JDSS_FINAL_SPEC.md), 운영 이력은 [전략 가이드](docs/STRATEGY_GUIDE.md), 검증 기록은 [백테스트 보고서](docs/BACKTEST_REPORT.md), 협업 절차는 [개발 워크플로](docs/infra/DEVELOPMENT_WORKFLOW.md), 보안 기준은 [보안 기준](docs/infra/SECURITY.md)을 참고합니다.

## 구현 범위

- 완결 미국 거래일 검증과 yfinance 수정주가 일봉 분석
- 완료 월말·다음 거래일 원칙을 적용한 통합 계좌 백테스트
- 코어 10→15% 단계형 목표와 6개월 월간 추세
- 코어·부스터 분리 SQLite 원장과 합산 Reconciliation
- SQLite WAL, 상태 전이, 낙관적 잠금, 신호·주문 멱등성
- Telegram 관리자 1명 제한과 검토 → 최종 실행의 2단계 매수 승인
- Toss Securities OAuth2/OpenAPI 어댑터와 실주문 이중 잠금
- 부분체결, TP 자동복구, 재시작 Reconciliation과 SAFE_MODE
- JDSS 관리 SGOV 전용 원장, 자동 예치·선현금화·현금화 의도 영속화·부분체결·정합성 SAFE_MODE
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

Telegram의 `/bt`는 V3.1 전체 포트폴리오 최근 300거래일을 실행합니다. `/bt TQQQ 100`처럼 단일 종목 부스터 백테스트도 가능합니다.

## 주문 안전장치

V3.1.0은 `portfolio.live_enabled: false`와 애플리케이션 시작 잠금으로 전체 live 모드를 거부합니다. 아래 과거 이중 잠금 값이 모두 설정되어도 V3.1 코드에서는 실주문이 열리지 않습니다.

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
