# JDSS 2.0 Telegram Bot 운영 명세 및 가이드

## 목적

단일 관리자 Chat ID만 JDSS 상태 조회와 백테스트를 실행할 수 있다. Telegram은 전략을 직접 계산하지 않고, 동일한 `strategy.yaml`과 `BacktestEngine`의 결과만 표시한다. 백테스트 명령은 주문 서비스와 분리되어 있으며 실제 주문을 생성하지 않는다.

## 봇 메뉴 구성 (우선순위 순서)

1. `/dashboard` (`/d`): 통합 대시보드 (전체 요약 & 포지션 이모티콘 🟢🟡🟠🔴/☕ 표시)
2. `/account` (`/acct`): 토스 증권 실제 보유 종목 및 평가손익 조회
3. `/score` (`/sc` [종목]): JDSS 2.0 점수 및 4단계 진입 가드 상세
4. `/signal` (`/sg`): 활성 매매 신호 조회
5. `/status` (`/st` [종목]): 포지션 상태 및 매수단계 확인
6. `/indicator` (`/i` [종목]): 주요 기술적 지표 (CCI, RSI, EMA, BB, ATR)
7. `/backtest` (`/bt` [종목] [기간]): 임의 종목 백테스트 (최근 N거래일)
8. `/guide` (`/g`): 📖 JDSS 용어 & 지표 상세 설명서 (2개 카드로 100% 무결 발송)

## 백테스트 명령 (`/bt`)

```text
/backtest
/bt
```

인자 없이 `/backtest` 또는 `/bt`만 입력하면 SOXL 최근 300거래일을 실행한다.

```text
/bt NVDA 100
/bt TQQQ 250
/bt ALL 100
```

형식은 다음과 같다:

```text
/bt [ALL|임의의티커] [최근 거래일 수]
```

- 예: `/bt NVDA 100`은 엔비디아의 최근 100거래일을 백테스트한다.
- `/bt ALL 100`은 활성화된 기본 종목(TQQQ, SOXL)을 함께 백테스트한다.
- SOXL 백테스트 시 `SOXX`, `SMH` 섹터 데이터가 자동 동기화되어 섹터 가드가 실거래와 동일하게 적용된다.
- 매매 내역은 종목당 **최근 20건**을 `<code>` 고정폭 폰트 블록으로 깔끔하게 줄맞춤하여 발송한다.

## 보안 및 주문 격리

- `.env`의 `TELEGRAM_ALLOWED_CHAT_IDS`는 승인된 관리자만 허용한다.
- API 키, Telegram 토큰, 계좌번호는 메시지와 로그에 절대 출력하지 않는다.
- `JDSS_TRADING_MODE=dry_run`을 기본으로 유지한다.
- `/bt`는 `BacktestEngine`만 사용하며 주문 승인·주문 실행 코드를 호출하지 않는다.
