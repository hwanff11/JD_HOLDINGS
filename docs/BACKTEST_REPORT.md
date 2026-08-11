# JDSS V3 백테스트 보고서

현재 개발 계약은 [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md)의 **JDSS-3.0.0-TWIN-H05**다. 재현 가능한 최종 수치는 `jdss backtest --symbol ALL`의 `v3_portfolio` 결과와 `JDSS V3 Backtest` Actions Artifact를 기준으로 한다.

## 선택 근거

PR #27은 2011-01-01~2026-08-10 조정 일봉에서 월간 쌍발 코어, JDSS 5%·10% 부스터, 반월·격주·주간·밴드 리밸런싱을 비교했다. 공통 가정은 완료봉 신호, 다음 거래일 시가, 매수·매도 수수료 각 0.1%, SGOV, `$250` 현금 버퍼였다.

더 복잡한 최종 후보 `SEMIMONTHLY_BAND_H05`는 기준 슬리피지 0.10%에서 누적 +657.61%, CAGR 13.86%, MDD -30.44%, Sharpe 0.790이었지만 월간 H05 대비 5년 수익 우위 5/11, 부트스트랩 수익 우위 31.8%, MDD 우위 36.8%에 그쳐 사전 기준을 통과하지 못했다. `BAND_A_H05`는 수익이 더 높았지만 격주 시작 위상 A/B 차이가 커 선택 편향 위험이 남았다.

따라서 V3는 탈락 후보를 채택하지 않고 비교 기준이었던 `MONTHLY_H05`를 선택한다. 월 1회 완료봉만 사용해 시작 주 위상이 없고, 규칙·주문·복구 경로가 가장 단순하기 때문이다. 이것은 수익 극대화 후보의 승격이 아니라 dry-run에서 검증할 보수적 구조의 승격이다.

## V3 운영 동등 엔진

PR #27 연구 코드를 운영 패키지의 `PortfolioBacktestEngine`으로 옮기면서 다음을 계약 테스트로 고정했다.

- 코어와 부스터가 현금을 공유하는 `$20,000` 단일 계좌
- 월말 QQQ·SOXX 10개월 추세와 다음 거래일 코어 체결
- 코어 종목당 15%, 부스터 종목당 `$1,000` 절대 상한
- 운영 JDSS 엔진이 생성한 정수주 부스터 체결을 같은 날짜·가격으로 재생
- 코어·부스터 수량 분리와 해당 슬리브만 조정
- 수수료·슬리피지·SGOV 수익·`$250` 버퍼·잔여 매도비용 반영
- 누적수익, CAGR, MDD, Sharpe, Sortino, 연도별 수익률, 평균노출, 슬리브별 체결수

## 최종 재검증 절차

```bash
jdss backtest \
  --symbol ALL \
  --start 2011-01-01 \
  --end 2026-08-10 \
  --slippage 0.001 \
  --output reports/v3-backtest.json
```

GitHub에서는 저장소 소유자가 `[backtest-v3]`로 시작하는 이슈를 열면 최신 `main`으로 같은 검증을 실행하고 JSON·Markdown Artifact와 이슈 댓글을 남긴다. 최종 병합 뒤 생성된 수치와 Actions run은 [`../CURRENT_WORK.md`](../CURRENT_WORK.md)에 기록한다.

## 해석 제한

- 과거 성과는 미래 성과를 보장하지 않는다.
- TQQQ·SOXL은 레버리지 ETF라 경로 의존성, 급락, 변동성 소모가 크다.
- 부스터의 무손절 규칙은 장기 고착과 큰 미실현손실을 만들 수 있다.
- yfinance 조정 일봉, 시가 체결과 일별 SGOV 수익률은 실제 호가·세금·환전·승인 지연을 완전히 재현하지 않는다.
- V3.0.0은 이 한계 때문에 live가 아니라 Oracle dry-run으로만 승격한다.

PR #27의 후보별 상세 결과와 당시 판정은 [`research/PR27_SIMPLE_STRATEGY_RESEARCH.md`](research/PR27_SIMPLE_STRATEGY_RESEARCH.md)에 보존한다.
