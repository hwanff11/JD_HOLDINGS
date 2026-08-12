from __future__ import annotations

import re
import threading

import telebot

from . import telegram_bot as telegram_bot_module
from .telegram_bot import TelegramBotApp


def _v311_guide_cards() -> tuple[str, ...]:
    """User-visible guide for the adopted JDSS 3.1.1 contract."""
    return (
        (
            "🌗 <b>[JDSS V3.1.1 TWIN-H40-S3 전략 개요]</b>\n\n"
            "• JDSS는 <code>$50,000</code>의 <b>고정 전략원금</b>만 사용합니다.\n"
            "• JDSS가 번 이익은 다음 매매 크기를 키우는 데 재사용하지 않습니다.\n"
            "• 월급·추가입금·개인 USD·개인 QQQ/QQQM은 JDSS 자금에서 제외합니다.\n"
            "• 같은 Toss 계좌의 개인 TQQQ·SOXL 혼합 보유는 지원하지 않습니다.\n"
            "• <b>SGOV는 사용하지 않고</b> 미투입 전략원금은 USD 현금으로 둡니다.\n"
            "• live는 계속 잠겨 있으며 dry-run만 허용합니다."
        ),
        (
            "📈 <b>[월간 쌍발 코어]</b>\n\n"
            "• TQQQ는 QQQ, SOXL은 SOXX의 월말 추세를 따라갑니다.\n"
            "• 월말 종가가 6개월 이동평균 위이면 코어를 ON으로 봅니다.\n"
            "• OFF→ON 첫 달 목표는 고정원금의 10%, 즉 종목당 최대 약 <code>$5,000</code>입니다.\n"
            "• 다음 월에도 ON이면 15%, 즉 종목당 최대 약 <code>$7,500</code>을 목표로 합니다.\n"
            "• 추세가 꺼지면 코어를 0%로 줄이며 위험축소 매도는 자동입니다."
        ),
        (
            "📖 <b>[JDSS H40-S3 부스터]</b>\n\n"
            "• 부스터 자금 상한 H40은 종목별 <code>$20,000</code>, 즉 고정원금의 40%입니다.\n"
            "• S3 분할매수는 40% → 70% → 90% 누적이므로 한 사이클 최대 신규투입은 "
            "<code>$18,000</code>, 즉 고정원금의 36%입니다.\n"
            "• 최소 점수 55점, 반등점수 5점 이상, RED 국면은 매수를 차단합니다.\n"
            "• 2차는 최초 체결가 -2%, 3차는 -5%에서 추가 조건을 확인합니다.\n"
            "• SOXL은 SOXX·SMH EMA60 섹터 가드를 적용합니다."
        ),
        (
            "💰 <b>[익절·자금관리]</b>\n\n"
            "• TP1: 평단 +4%에서 약 30% 매도\n"
            "• TP2: 평단 +10%에서 나머지 매도\n"
            "• 재매수·자동손절·기간 강제청산은 비활성화 상태입니다.\n"
            "• 모든 신규매수 크기는 <b>$50,000 고정 기준</b>으로 계산합니다.\n"
            "• 실현이익 때문에 현금이 $50,000을 넘어도 초과분은 JDSS가 다시 쓰지 못합니다.\n"
            "• 손실이 나도 개인자금을 자동으로 끌어와 원금을 채우지 않습니다."
        ),
        (
            "🎯 <b>[점수·승인·안전장치]</b>\n\n"
            "• CCI, RSI, 볼린저밴드로 과매도를 보고 EMA, 거래량, ATR로 반등과 위험을 확인합니다.\n"
            "• 코어·부스터 모든 매수는 Telegram 2단계 승인이 필요합니다.\n"
            "• 주문 결과가 불명확하거나 계좌 수량이 원장과 다르면 SAFE_MODE로 신규매수를 막습니다.\n"
            "• /dashboard, /portfolio, /score, /history, /signal, /bt, /order, /errors를 주로 사용합니다."
        ),
    )


def _v311_runtime_text(text: str) -> str:
    """Translate shared legacy presentation labels into the active V3.1.1 contract."""
    replacements = (
        ("JDSS V3.0 MONTHLY_H05", "JDSS V3.1.1 TWIN-H40-S3"),
        ("JDSS V3 MONTHLY_H05", "JDSS V3.1.1 TWIN-H40-S3"),
        ("JDSS V3.1 TWIN-H40-S3", "JDSS V3.1.1 TWIN-H40-S3"),
        ("V3 MONTHLY_H05 포트폴리오", "V3.1.1 TWIN-H40-S3 포트폴리오"),
        ("월간 10개월 추세 · 종목당 15%", "월간 6개월 추세 · 첫 ON 10%, 지속 15%"),
        (
            "JDSS 점수 전략 · 종목당 최대 5%",
            "JDSS H40-S3 · 종목당 cap $20k, S3 최대 $18k",
        ),
        ("JDSS 5% 부스터", "JDSS H40-S3 부스터"),
        (
            "코어 규칙 : 10개월 추세 ON · 목표 15%",
            "코어 규칙 : 6개월 추세 ON · 첫 ON 10%, 지속 15%",
        ),
        ("SGOV 운용이 비활성화되어 있습니다.", "SGOV는 V3.1.1 계약에서 사용하지 않습니다."),
        ("SGOV 추정수익", "SGOV 운용수익(OFF)"),
        ("SGOV수익", "SGOV수익(OFF)"),
        ("수수료·슬리피지·SGOV를 반영한", "수수료·슬리피지를 반영한"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    text = re.sub(r"^.*<code>/sgov</code>.*\n?", "", text, flags=re.MULTILINE)
    return text


telegram_bot_module._guide_cards = _v311_guide_cards


class FinalTelegramBotApp(TelegramBotApp):
    """Production entry point for the JDSS V3.1.1 Telegram application."""

    def _send(self, text: str, *, markup=None, chat_id: int | None = None) -> None:
        super()._send(_v311_runtime_text(text), markup=markup, chat_id=chat_id)

    def run(self) -> None:
        """Publish the active command menu without the retired SGOV command."""
        self.bot.set_my_commands(
            [
                telebot.types.BotCommand("dashboard", "☀️ 통합 대시보드"),
                telebot.types.BotCommand("portfolio", "📊 V3.1.1 코어·부스터 현황"),
                telebot.types.BotCommand("account", "💰 토스 계좌 잔고"),
                telebot.types.BotCommand("status", "✨ 종목별 포지션 상세"),
                telebot.types.BotCommand("score", "🎯 JDSS 지표 분석"),
                telebot.types.BotCommand("history", "📈 최근 점수 이력"),
                telebot.types.BotCommand("signal", "🚨 활성 매수 신호"),
                telebot.types.BotCommand("backtest", "🌞 백테스트 실행"),
                telebot.types.BotCommand("guide", "📖 JDSS 용어 및 전략 설명서"),
                telebot.types.BotCommand("order", "🌟 미체결 주문 현황"),
                telebot.types.BotCommand("errors", "🛡 최근 시스템 기록"),
                telebot.types.BotCommand("ping", "🏓 봇 상태 확인"),
                telebot.types.BotCommand("help", "🤖 메뉴 안내"),
            ]
        )
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        telegram_bot_module.LOGGER.info("JDSS V3.1.1 Telegram polling 시작")
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
