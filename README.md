# JD_HOLDINGS

JD_HOLDINGS의 현재 전략은 **JDSS-3.2.2-RS6M-ONEWAY-HWM75**입니다. QQQ를 기본 시장참여 자산으로 사용하고, 추세·변동성에 따라 0.5/1.0/1.25/1.5배로 노출을 조절하며, 반도체 상대강도가 좋을 때 레버리지 슬리브 일부를 SOXL로 분산합니다. 기존 JDSS H40-S3는 독립 자금전략이 아니라 최대 5% 오버레이 신호엔진으로 사용합니다.

> V3.2.2는 정식 production 전략으로 승격하지만 **실거래는 별도 승인 전까지 잠금**입니다. Oracle과 Telegram은 forced dry-run으로 운영합니다.

## V3.2.2 핵심 계약

- 시작 위험원금: **$50,000**
- HWM75 통제복리: 새 최고자산 누적이익의 **75%만 위험예산 증가에 반영**
- 손실 시 외부자금 자동보충 없음
- SGOV OFF, 미투입 자금은 USD
- QQQ 20일 연환산 변동성 30% 이상: **0.5x**
- 일반: **1.0x**
- 중간 추세: **1.25x**
- 강한 추세: **1.5x**
- 월간 reset: 새 달 첫 거래일 종가 판정 → 다음 세션 반영
- 반도체 RS6M: SOXX 126거래일 수익률이 양수이면서 QQQ보다 높을 때 ON
- RS ON 시 레버리지 슬리브: TQQQ 50% + SOXL 50%
- 월중 RS 이탈: SOXL → TQQQ one-way exit, 다음 달까지 SOXL 재진입 금지
- 기존 JDSS H40-S3 활성 시 QQQ 최대 5%를 TQQQ/SOXL로 교체
- 모든 위험증가 BUY: Telegram 2단계 승인
- 위험축소 SELL: 자동
- QQQ/TQQQ/SOXL 개인물량을 같은 Toss 계좌에 혼합 보유 금지
- `portfolio.live_enabled: false`, 애플리케이션 live hard-lock 유지

## Canonical 백테스트

2011-01-01~최신 완결 거래일, 초기 $50,000, 매수/매도 수수료 0.1%, 슬리피지 0.1%, SGOV OFF 기준입니다.

| 지표 | V3.2.2 | QQQ B&H 참고 |
|---|---:|---:|
| CAGR | **약 22.4%** | 18.95% |
| MDD | **약 -30.9%** | -35.12% |
| Sharpe | **약 1.00** | 0.941 |
| 평균 노출 | 약 80.6% | 100% |

정식 CI canonical gate는 provider 데이터의 소폭 수정 가능성을 감안해 CAGR 22.0~22.8%, MDD -31.6~-30.3%, Sharpe 0.94~1.07 범위를 요구합니다.

## 연구 한계

- 2023+ 데이터는 후보 선택 과정에서 반복 관찰되어 **pristine OOS가 아닙니다**.
- 후보군 CSCV-style PBO가 약 **64.29%**로 과최적화 경고가 남아 있습니다.
- 따라서 과거 초과수익을 미래 승리 보장으로 해석하지 않습니다.
- 대표 승인으로 production 전략은 V3.2.2로 승격하되 live는 계속 잠급니다.

## 문서

1. `CURRENT_WORK.md` — 현재 작업/배포 상태
2. `docs/JDSS_FINAL_SPEC.md` — 공식 전략 계약
3. `docs/STRATEGY_GUIDE.md` — 쉬운 전략 설명
4. `docs/BACKTEST_REPORT.md` — 백테스트 및 한계
5. `docs/TELEGRAM_BOT_GUIDE.md` — Telegram 운영
6. `docs/infra/DEPLOYMENT.md` — Oracle 배포
7. `docs/releases/V3.2.2.md` — V3.2.2 릴리즈 노트

## 빠른 시작

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
.venv/bin/jdss validate-config
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/jdss backtest --symbol ALL --start 2011-01-01 --output reports/baseline.json
```

Oracle 배포 workflow는 정확한 최신 `main`만 배포하고 서버 `.env`를 `JDSS_TRADING_MODE=dry_run`, 빈 `JDSS_LIVE_CONFIRMATION`으로 강제합니다. V3.1.1에서 V3.2.2로 원장 모델이 바뀌는 최초 배포에서는 기존 dry-run SQLite를 `v322-migration` 백업으로 보존한 뒤 새 V3.2.2 원장으로 시작합니다.
