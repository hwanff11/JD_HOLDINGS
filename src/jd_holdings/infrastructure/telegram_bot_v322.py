from __future__ import annotations

import re
import threading

import telebot

from . import telegram_bot as telegram_bot_module
from .telegram_bot import TelegramBotApp


def _v322_guide_cards() -> tuple[str, ...]:
    return (
        (
            "📈 <b>[JDSS V3.2.2 전략 개요]</b>\n\n"
            "• 기본은 QQQ 시장에 계속 참여하고 위험도에 따라 "
            "<code>0.5 / 1.0 / 1.25 / 1.5x</code>로 노출을 조절합니다.\n"
            "• QQQ 20일 연환산 변동성이 30% 이상이면 0.5x로 방어합니다.\n"
            "• 새 달 첫 거래일 종가에서 추세와 반도체 상대강도를 다시 판정합니다.\n"
            "• 모든 위험증가 BUY는 Telegram 2단계 승인이 필요하며 위험축소 SELL은 자동입니다.\n"
            "• 실거래는 계속 잠겨 있고 forced dry-run만 허용합니다."
        ),
        (
            "🧭 <b>[추세·레버리지]</b>\n\n"
            "• SMA50/SMA200, 1·3·6개월 모멘텀, SMA200 기울기를 사용합니다.\n"
            "• 강한 상승장 1.5x, 중간 추세 1.25x, 일반 1.0x, 고변동 0.5x입니다.\n"
            "• 월중 고변동 브레이크는 감속만 하며 다음 월 reset 전에는 다시 가속하지 않습니다."
        ),
        (
            "💻 <b>[RS6M 반도체 슬리브]</b>\n\n"
            "• SOXX 126거래일 수익률이 양수이면서 QQQ보다 높으면 반도체 상대강도 ON입니다.\n"
            "• 레버리지 슬리브의 50%를 TQQQ, 50%를 SOXL로 나눕니다.\n"
            "• 월중 상대강도가 깨지면 SOXL 부분을 TQQQ로 한 번 후퇴하고 다음 달까지 SOXL 재진입을 금지합니다."
        ),
        (
            "💰 <b>[HWM75 통제복리]</b>\n\n"
            "• 시작 위험원금은 <code>$50,000</code>입니다.\n"
            "• 새 최고자산을 만들면 누적이익의 75%만 다음 위험예산 증가에 반영합니다.\n"
            "• 나머지 25%는 계좌 현금으로 남지만 위험예산 확대에는 쓰지 않습니다.\n"
            "• 손실이 나도 개인자금으로 자동 보충하지 않습니다."
        ),
        (
            "🎯 <b>[JDSS 5% 오버레이·안전장치]</b>\n\n"
            "• 기존 H40-S3 과매도 로직은 독립 매수전략이 아니라 오버레이 신호엔진으로 사용합니다.\n"
            "• 활성 시 QQQ 최대 5%를 TQQQ/SOXL로 교체합니다.\n"
            "• QQQ·TQQQ·SOXL을 같은 Toss 계좌에 개인물량과 섞어 보유하면 "
            "원장 구분이 불가능하므로 금지합니다.\n"
            "• 주문결과 불명확·수량불일치·레거시 direct 포지션 발견 시 SAFE_MODE로 신규매수를 막습니다."
        ),
    )


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


telegram_bot_module._guide_cards = _v322_guide_cards


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
