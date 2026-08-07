# JD_HOLDINGS

JDSS(JH Dynamic Score Swing Strategy) v2.0.0 A안을 TQQQ와 SOXL 및 미국주식에 적용하는 독립형 Telegram 반자동 매매 봇입니다. 기존 `cci_nvdl`과 코드, DB, 서비스, 배포 경로를 공유하지 않습니다.

> 현재 상태: **JDSS 2.0 A안 적용·Oracle 서버 dry-run 배포 완료**. 
> 진입 점수 50점, 4단계 분할매수(40%:30%:20%:10%), 목표 익절 TP1(+4%)/TP2(+8%) 및 텔레그램 자유 티커 백테스트가 적용되었습니다.

## 구현 범위

- yfinance 수정주가 일봉과 완결 거래일 검증
- JDSS 점수, 시장 국면, 고정 10,000달러 한도의 4단계 분할매수, 고정 TP
- 다음 거래일 시가 체결 대용 모델을 사용하는 노룩어헤드 백테스트
- 텔레그램 임의 주식 티커 백테스트 (예: `/bt NVDA 100`, `/bt TSLA`, `/bt ALL`)
- 백테스트 UI 최적화 (`[점수|수량|단가]` 통합 포맷) 및 데이터 부족 예외 처리
- 코어 엔진 및 백테스트 알고리즘 전면 상세 한글 주석(Docstring) 문서화
- SQLite WAL, 상태 전이, 낙관적 잠금, 신호/주문 멱등성 및 정적 분석(Bandit) 보안 패치 적용
- Telegram 관리자 1명 제한 및 매수 검토 → 최종 실행의 2단계 승인
- dry-run 기본값과 실주문 이중 잠금
- Toss Securities OAuth2/OpenAPI 어댑터
- 부분체결, TP 취소·재생성, 시작/주기 정합성 검사와 SAFE_MODE
- 별도 `jd_holdings_bot.service`와 버전 디렉터리 기반 Oracle 배포

전략 명세는 [`docs/STRATEGY_V2.0_SWING.md`](docs/STRATEGY_V2.0_SWING.md), 백테스트 리포트는 [`docs/BACKTEST_HIGH_RETURN_V2.0.md`](docs/BACKTEST_HIGH_RETURN_V2.0.md), 개발 협업 워크플로는 [`docs/DEVELOPMENT_WORKFLOW.md`](docs/DEVELOPMENT_WORKFLOW.md)에 있습니다.

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

최신 완결 일봉 분석과 백테스트:

```bash
.venv/bin/jdss analyze
.venv/bin/jdss backtest --symbol ALL --start 2011-01-01 \
  --output reports/baseline.json
```

Telegram 봇은 `.env`에 봇 토큰과 관리자 개인 Chat ID 하나를 넣은 뒤 실행합니다.

```bash
.venv/bin/jdss-bot
```

Telegram에서 다음 명령으로 실제 주문 없이 백테스트를 실행할 수 있습니다.

```text
/backtest          # 기본: SOXL 최근 300거래일
/bt NVDA 100       # NVDA 최근 100거래일 (자유 티커 지원)
/bt TSLA 2025-01-01# 2025년부터 테슬라 백테스트
/bt ALL 250        # 기본 TQQQ+SOXL 합산 최근 250거래일
```

결과에는 총수익률·CAGR·MDD와 함께 전략이 포착한 모든 매수 신호, 매수·추가매수·
재매수, TP1·TP2 매도를 표시합니다. 가독성을 위해 종목당 최근 15건만 보여줍니다.

`/account`는 토스 실제 계좌의 미국주식만 조회하며, 수수료 반영 평가손익과 수익률을
소수점 둘째 자리까지 표시합니다.

`JDSS_TRADING_MODE=dry_run`이 기본입니다. dry-run은 Toss 주문 API를 호출하지 않습니다.

## 실주문 잠금

실주문은 다음 두 값이 모두 정확히 설정된 경우에만 열립니다.

```dotenv
JDSS_TRADING_MODE=live
JDSS_LIVE_CONFIRMATION=ENABLE_JDSS_LIVE_ORDERS
```

이 잠금은 기준선 수정, 재백테스트, dry-run 관찰, Toss 조회 전용 smoke test 및 운영자
승인까지 완료된 뒤에만 해제해야 합니다. 주문 직전에도 신호 만료, 세션, 추격 상한,
수량, 매수가능금액을 다시 확인합니다.

## Toss 조회 전용 점검

`toss-smoke`는 인증, 현재가, 미국 장 캘린더만 조회하며 주문을 만들지 않습니다.

```bash
.venv/bin/jdss toss-smoke
```

Oracle 서버 공인 IP는 Toss OpenAPI 허용 IP에 등록해야 합니다. API 자격증명과 Telegram
토큰은 `.env`에만 보관하며 Git에 커밋하지 않습니다.

## 배포

상세 절차는 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)를 참고하세요. `deploy.sh`는
깨끗한 `main` 브랜치와 통과한 테스트를 요구하고, 먼저 GitHub에 push한 뒤 별도 릴리스
경로로 전송합니다. 기존 프로세스를 광범위하게 종료하지 않고
`jd_holdings_bot.service`만 재시작합니다.

## 면책

이 저장소는 투자 성과를 보장하지 않습니다. 레버리지 ETF와 무손절 전략은 원금 손실,
장기 자금 고착, 급격한 변동 위험이 큽니다. 실거래 전 전략 유효성과 주문 동작을 직접
확인해야 합니다.
