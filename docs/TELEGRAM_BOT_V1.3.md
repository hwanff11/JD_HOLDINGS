# JDSS v1.3 Telegram Bot 운영 명세

## 목적

단일 관리자 Chat ID만 JDSS 상태 조회와 백테스트를 실행할 수 있다. Telegram은 전략을
계산하지 않고, 동일한 `strategy.yaml`과 `BacktestEngine`의 결과만 표시한다. 백테스트
명령은 주문 서비스와 분리되어 있으며 실제 주문을 생성하지 않는다.

## 백테스트 명령

```text
/backtest
/bt
```

기본값은 TQQQ와 SOXL을 합산해 `strategy.yaml`의 기본 시작일부터 최신 완결 미국
거래일까지 실행한다.

```text
/bt ALL 2025-01-01
/bt SOXL 2021-01-01
/bt TQQQ 2021-01-01 2024-12-31
```

형식은 다음과 같다.

```text
/bt [ALL|TQQQ|SOXL] [시작일] [종료일]
```

- 날짜는 `YYYY-MM-DD`만 허용한다.
- 시작일은 설정의 `backtest.default_start`보다 이를 수 없다.
- 종료일은 최신 완결 거래일을 넘을 수 없다.
- 시작일만 입력하면 종료일은 최신 완결 거래일이다.
- 동시에 하나의 백테스트만 실행한다.
- 실행 중 다른 요청은 새 작업을 만들지 않고 대기 안내를 전송한다.
- yfinance 수정주가 일봉과 `JDSS_CACHE_PATH`를 사용한다. Oracle에서는 릴리스 밖의
  `shared/data/cache`를 사용한다.

## Telegram 결과

합산 포트폴리오는 다음을 표시한다.

- 대상과 실제 분석 기간
- 초기자금과 최종자산
- 총수익률
- 연복리수익률(CAGR)
- 최대낙폭(MDD)
- 샤프지수와 소르티노지수

종목별로 다음을 표시한다.

- 총수익률과 CAGR
- MDD와 최악 MAE
- 완료 사이클과 승률
- Profit Factor와 거래당 기대수익
- 평균·최대 보유일
- TP1·TP2 도달률
- 연평균 신호수와 평균 자금 활용률

결과 하단에는 수수료, 슬리피지, 종목당 고정 10,000달러, 수익 재투자 없음 조건을
표시한다.

## 보안과 주문 격리

- `.env`의 `TELEGRAM_ALLOWED_CHAT_IDS`는 정확히 한 명만 허용한다.
- 메시지 Chat ID와 Callback 사용자 ID를 모두 확인한다.
- 허용되지 않은 사용자의 메시지에는 응답하지 않는다.
- API 키, Telegram 토큰, 계좌번호는 메시지와 로그에 출력하지 않는다.
- `JDSS_TRADING_MODE=dry_run`과 비어 있는 `JDSS_LIVE_CONFIRMATION`을 유지한다.
- `/bt`는 `BacktestEngine`만 사용하며 주문 승인·주문 실행 코드를 호출하지 않는다.
- 토스 인증정보가 있어도 `dry_run`에서는 토스 클라이언트를 계좌 조회에만 사용한다.
- `/account`는 실제 보유종목과 USD 주문가능금액을 조회하며 주문 API를 호출하지 않는다.

## 운영 명령

```text
/dashboard /d                 통합 대시보드
/account /acct                토스 실제 계좌 조회
/score /sc [종목]             JDSS 점수
/signal /sg                   활성 매매신호
/status /st [종목]            포지션 상태
/indicator /i [종목]          기술지표
/backtest /bt [...]           백테스트
/order /o                     미체결 주문
/errors /err                  최근 이벤트
/ping /p                      봇·실주문 잠금 상태
/help /h                      도움말
```

## 장애 처리

- 데이터 다운로드나 계산 실패 시 예외 원문에 비밀값을 포함하지 않고 실패 메시지만
  관리자에게 보낸다.
- 백테스트 작업은 별도 스레드에서 실행해 Telegram polling을 멈추지 않는다.
- 성공·실패 여부와 관계없이 실행 잠금을 해제해 다음 요청을 받을 수 있게 한다.
- 봇 재시작 후에는 진행 중이던 백테스트를 복구하지 않으며 사용자가 다시 요청한다.
