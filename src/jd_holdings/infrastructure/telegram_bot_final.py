from __future__ import annotations

from . import telegram_bot as telegram_bot_module
from .telegram_bot import TelegramBotApp


def _v31_guide_cards() -> tuple[str, ...]:
    """User-visible guide for the adopted JDSS 3.1 contract."""
    return (
        (
            "🌗 <b>[JDSS V3.1 TWIN-H40-S3 전략 개요]</b>\n\n"
            "• 총 전략자금 <code>$20,000</code>을 코어·부스터·SGOV로 함께 관리합니다.\n"
            "• <b>월간 코어</b>: QQQ·SOXX 월말 종가가 6개월 이동평균 위이면 "
            "TQQQ·SOXL을 첫 달 10%, 추세 유지 시 15%까지 운용합니다.\n"
            "• <b>JDSS 부스터</b>: 종목별 최대 <code>$8,000</code>, 총자금의 최대 40%입니다.\n"
            "• 코어·부스터 매수는 모두 2단계 Telegram 승인이 필요합니다.\n"
            "• live는 계속 잠겨 있으며 dry-run만 허용합니다."
        ),
        (
            "📖 <b>[JDSS 부스터 진입]</b>\n\n"
            "• 최소 점수 55점, 반등점수 5점 이상, RED 국면은 매수 차단\n"
            "• 3단계 분할: 40% → 70% → 90% 누적\n"
            "• 2차: 최초 체결가 -2%, 3차: 최초 체결가 -5%\n"
            "• 4차 추가매수는 연구 결과에 따라 제거했습니다.\n"
            "• SOXL은 SOXX·SMH EMA60 섹터 가드를 적용합니다."
        ),
        (
            "💰 <b>[익절·포지션 관리]</b>\n\n"
            "• TP1: 평단 +4%에서 약 30% 매도\n"
            "• TP2: 평단 +10%에서 나머지 매도\n"
            "• TP1 이후 20/40거래일 강제 잔량청산은 사용하지 않습니다.\n"
            "• 재매수와 자동손절은 비활성화 상태입니다.\n"
            "• 부분체결·TP 주문 복구·재시작 정합성 검증은 계속 동작합니다."
        ),
        (
            "📊 <b>[점수와 지표]</b>\n\n"
            "• CCI 5 / 10, RSI 5 / 14, 볼린저 하단으로 과매도를 봅니다.\n"
            "• EMA 5 / 20 / 60과 당일 종가 위치로 반등과 추세를 확인합니다.\n"
            "• 거래량과 ATR 비율을 함께 반영합니다.\n"
            "• /score는 현재 점수와 각 매수 게이트 충족 여부를 보여줍니다."
        ),
        (
            "🤖 <b>[주요 명령]</b>\n\n"
            "• /dashboard : 통합 계좌 요약\n"
            "• /portfolio : 코어·부스터 비중\n"
            "• /score, /history, /signal : 점수·신호 확인\n"
            "• /bt : V3.1 통합 백테스트\n"
            "• /sgov : JDSS 관리 SGOV 상태\n"
            "• /order, /errors : 주문·오류 확인\n"
            "• /guide : 이 전략 가이드 다시 보기"
        ),
    )


# TelegramBotApp methods resolve the guide function from telegram_bot's module globals.
# Patch that single presentation hook here so the final runtime guide follows V3.1
# without duplicating the full Telegram implementation.
telegram_bot_module._guide_cards = _v31_guide_cards


class FinalTelegramBotApp(TelegramBotApp):
    """Production entry point for the JDSS V3.1 Telegram application."""
