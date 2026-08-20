from __future__ import annotations

import re
import threading

import telebot

from . import telegram_bot as telegram_bot_module
from .telegram_bot import TelegramBotApp


def _v322_runtime_text(text: str) -> str:
    replacements = (
        ("JDSS V3.0 MONTHLY_H05", "JDSS V3.2.2 RS6M-HWM75"),
        ("JDSS V3 MONTHLY_H05", "JDSS V3.2.2 RS6M-HWM75"),
        ("JDSS V3.1.1 TWIN-H40-S3", "JDSS V3.2.2 RS6M-HWM75"),
        ("V3.1.1 TWIN-H40-S3 포트폴리오", "V3.2.2 QQQ/TQQQ/SOXL 포트폴리오"),
        ("JDSS H40-S3 부스터", "JDSS 5% 가상 오버레이"),
        ("SGOV는 V3.1.1 계약에서 사용하지 않습니다.", "SGOV는 V3.2.2 계약에서도 사용하지 않습니다."),
        ("SGOV 추정수익", "SGOV 운용수익(OFF)"),
        ("SGOV수익", "SGOV수익(OFF)"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    text = re.sub(r"^.*<code>/sgov</code>.*\n?", "", text, flags=re.MULTILINE)
    return text


class V322TelegramBotApp(TelegramBotApp):
    """Production entry point for the JDSS V3.2.2 Telegram application."""

    def _send(self, text: str, *, markup=None, chat_id: int | None = None) -> None:
        super()._send(_v322_runtime_text(text), markup=markup, chat_id=chat_id)

    def run(self) -> None:
        self.bot.set_my_commands(
            [
                telebot.types.BotCommand("dashboard", "통합 대시보드"),
                telebot.types.BotCommand("portfolio", "V3.2.2 배분·HWM75 현황"),
                telebot.types.BotCommand("account", "토스 계좌 잔고"),
                telebot.types.BotCommand("status", "종목별 allocation 상세"),
                telebot.types.BotCommand("score", "JDSS 5% 오버레이 분석"),
                telebot.types.BotCommand("history", "최근 점수 이력"),
                telebot.types.BotCommand("signal", "활성 매수 승인 신호"),
                telebot.types.BotCommand("backtest", "V3.2.2 백테스트 실행"),
                telebot.types.BotCommand("guide", "V3.2.2 전략 설명서"),
                telebot.types.BotCommand("order", "미체결 주문 현황"),
                telebot.types.BotCommand("errors", "최근 시스템 기록"),
                telebot.types.BotCommand("ping", "봇 상태 확인"),
                telebot.types.BotCommand("help", "메뉴 안내"),
            ]
        )
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        telegram_bot_module.LOGGER.info("JDSS V3.2.2 Telegram polling 시작")
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
