# JD_HOLDINGS Current Work

> 현재 작업·배포·검증 상태의 단일 기준이다. 전략 수치는 `strategy.yaml`과 `docs/JDSS_FINAL_SPEC.md`, 문서 역할은 `docs/README.md`를 따른다.

## 현재 작업

- 기준 브랜치: `main` (`70c96c9`에서 작업 시작)
- 활성 작업 브랜치: `agent/v3-monthly-h05`
- 작업 목표: PR #27의 보수적 기준선 `MONTHLY_H05`를 `JDSS-3.0.0-TWIN-H05`로 승격
- 범위: 코드·DB·Telegram·백테스트·문서·Oracle dry-run 배포
- 제외: live 적용. V3는 설정과 코드 양쪽에서 전체 live 모드를 거부한다.

## 전략·운영 기준

- 전략·설정·패키지: `JDSS-3.0.0-TWIN-H05` / `3.0.0`
- 총자금: `$20,000`
- 코어: QQQ·SOXX 완료 월말 종가와 10개월 이동평균, 대응 TQQQ·SOXL 목표 15%, 다음 거래일 조정
- 부스터: 기존 JDSS 2.2 규칙, 종목당 `$1,000`(초기 총자금 최대 5%)
- 현금: SGOV와 `$250` 버퍼
- 매수: 코어·부스터 모두 Telegram 2단계 승인
- 매도: 코어 위험축소 자동, 부스터 기존 TP1·TP2·잔여청산
- 공식 계약: `docs/JDSS_FINAL_SPEC.md`

## 구현 상태

- V3 설정·버전과 기존 V1/V2 백테스트 설정 호환 완료
- 월말 신호, 다음 거래일 코어 목표수량, 재시작 시 미처리 월말 복구 완료
- 코어·부스터 분리 원장, 주문 멱등성, 체결 누적 적용, 합산 Reconciliation 완료
- SGOV 목표자금에 코어·부스터 투자원가 합산 완료
- 코어 매수 2단계 승인, 코어 위험축소 자동매도, live 이중 차단 완료
- `/portfolio`, V3 대시보드·신호·최종승인·가이드 메시지 완료
- 운영과 동일한 공유계좌 V3 CLI·Telegram 백테스트 추가

## 검증 상태

- 로컬 Ruff, 설정 검증, Bash 구문, workflow YAML 검증 통과
- pytest 133개 통과, 전체 커버리지 69%
- 장기 백테스트는 로컬 Yahoo rate limit 때문에 GitHub의 최신 `main` ChatOps 재검증으로 확정한다.
- GitHub CI, V3 Backtest, Oracle 배포 결과는 완료 후 이 문서에 확정 기록한다.

## 배포 상태

- 현재 Oracle 마지막 확인본은 기존 V2 dry-run이다.
- V3는 아직 `main` 병합·릴리스·Oracle 배포 전이다.
- 배포는 GitHub Actions `Deploy Oracle Dry Run` 한 경로만 사용하며 서버 `.env`를 강제로 `dry_run`과 빈 `JDSS_LIVE_CONFIRMATION`으로 유지한다.
- 운영 SHA의 최종 확인 기준은 Oracle `/home/ubuntu/JD_HOLDINGS/current` 링크와 배포 Actions 결과다.

## 다음 작업

1. 장기 통합 백테스트와 전체 테스트를 확정한다.
2. 문서·워크플로의 V2 전용 문구와 중복 연구 Actions를 정리한다.
3. PR 병합 후 `v3.0.0` 태그·릴리스를 만들고 Oracle dry-run을 배포한다.
4. Telegram `/ping`, `/portfolio`, `/dashboard`, `/bt`, `/sgov`를 배포 환경에서 확인한다.

## 작업 종료 갱신 규칙

작업 종료 시 활성 브랜치, 마지막 커밋, 검증 결과, 배포 SHA와 다음 작업을 갱신한다. 완료 이력 전체를 누적하지 않고 다음 작업자가 바로 이어가는 데 필요한 현재 상태만 유지한다.
