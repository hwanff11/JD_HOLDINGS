# JDSS 대표 버전과 연구 역사

이 문서는 과거 문서·연구 브랜치 수십 개를 대신하는 짧은 역사 색인입니다. **현재 전략을 결정하는 문서가 아닙니다.** 현재 기준은 [`strategy.yaml`](../strategy.yaml), [`JDSS_FINAL_SPEC.md`](JDSS_FINAL_SPEC.md), 현재 작업 상태는 [`CURRENT_WORK.md`](../CURRENT_WORK.md)입니다.

## 대표 버전

| 버전 | 핵심 변화 | 보존 위치 |
|---|---|---|
| v1.1.2 | 초기 점수·분할매수 계약의 회귀 기준 | [`configs/strategy_v1.1.2.yaml`](../configs/strategy_v1.1.2.yaml) |
| v2.2.2 | SGOV 현금화 재개와 2단계 승인까지 완성한 v2 대표판 | Git tag `v2.2.2` |
| v3.0.0 | 월간 쌍발 코어 + 5% 부스터를 처음 도입한 v3 기준선 | Git tag `v3.0.0` |
| v3.1.1 | $50,000 고정원금·SGOV OFF를 도입한 전환판 | Git history와 병합 PR |
| v3.2.2 | QQQ 동적노출·RS6M·HWM75·5% virtual overlay 현행판 | Git tag `v3.2.2` |

태그는 해당 시점의 코드·설정·문서를 함께 보존합니다. 따라서 현재 `main`에 옛 문서를 복제해 둘 필요가 없습니다.

## 왜 V3.2.2가 되었나

V3.1.1은 최대낙폭이 비교적 낮았지만 평균노출 약 21%로 자금 활용과 장기 수익이 낮았습니다. 후속 연구는 단순 고정 레버리지 대신 QQQ 추세에 따라 0.5/1.0/1.25/1.5배를 오가고, 반도체 상대강도가 있을 때만 SOXL을 섞는 구조를 비교했습니다.

최종 V3.2.2는 다음 이유로 선택됐습니다.

- production-equivalent 기준 CAGR 22.37%, MDD -30.93%, Sharpe 1.004
- QQQ 참고값 18.95%, -35.12%, 0.941 대비 위험조정 성과 개선
- SOXL 비중·RS 기간·대체 proxy·월 reset 날짜 변화에서 완만한 결과
- 월중 고변동 감속과 SOXL→TQQQ one-way exit로 가속보다 후퇴를 쉽게 설계
- 이익의 25%를 추가 위험에서 제외하는 HWM75

## 대표적인 미채택 연구

PR #27의 `SEMIMONTHLY_BAND_H05`와 격주 밴드형 쌍발엔진은 일부 구간 성과가 좋았지만 시작 위상에 민감했고, 5년 순환구간·paired bootstrap·MDD 승격 기준을 통과하지 못했습니다. 복잡한 후보 대신 더 단순한 `MONTHLY_H05` 기준선을 선택했습니다.

V3.2.2 후보 연구에서도 SOXL 슬리브, RS lookback, 월 reset 날짜, 실행비용을 폭넓게 비교했습니다. 최종값은 단일 최고점만 좇기보다 주변 값에서도 성과가 급격히 무너지지 않는 조합으로 동결했습니다.

## 남아 있는 연구 경고

- 2023+ 데이터는 후보 선택 중 반복 관찰되어 pristine OOS가 아닙니다.
- CSCV-style PBO 추정은 약 64.29%입니다.
- 2025년처럼 QQQ에 뒤지는 해가 있습니다.
- 이 경고 때문에 전략을 더 복잡하게 튜닝하지 않고 V3.2.2를 동결했으며 live는 잠가 두었습니다.

새 연구는 [`research/RESEARCH_PROTOCOL.md`](research/RESEARCH_PROTOCOL.md)를 따르고, 일회성 스크립트와 결과물은 채택되지 않으면 `main`에 누적하지 않습니다.
