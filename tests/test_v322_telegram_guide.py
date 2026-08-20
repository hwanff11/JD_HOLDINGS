from __future__ import annotations

import jd_holdings.infrastructure.telegram_bot as telegram_bot_module
from jd_holdings.infrastructure.telegram_bot import _guide_cards
from jd_holdings.infrastructure.telegram_bot_v322 import (
    V322TelegramBotApp,
    _v322_runtime_text,
)


def test_final_runtime_guide_matches_v322_contract():
    assert V322TelegramBotApp is not None
    cards = _guide_cards()
    guide = "\n".join(cards)
    for expected in (
        "V3.2.2",
        "QQQ",
        "0.5 / 1.0 / 1.25 / 1.5x",
        "30%",
        "SOXX",
        "126거래일",
        "$50,000",
        "75%",
        "최대 5%",
        "2단계 승인",
        "forced dry-run",
        "SAFE_MODE",
    ):
        assert expected in guide
    for rejected in ("V3.1.1", "독립 매수전략입니다", "SGOV 자동", "live 주문"):
        assert rejected not in guide
    assert telegram_bot_module._guide_cards is _guide_cards
    assert len(cards) == 5
    assert all(len(card) < 4096 for card in cards)


def test_final_runtime_translates_v311_labels_and_hides_sgov_menu_line():
    legacy = (
        "JDSS V3.1.1 TWIN-H40-S3 | "
        "V3.1.1 TWIN-H40-S3 포트폴리오 | JDSS H40-S3 부스터\n"
        "• <code>/sgov</code> : 💵 JDSS SGOV 유휴자금\n"
        "SGOV 추정수익: $0.00"
    )
    rendered = _v322_runtime_text(legacy)
    for expected in (
        "JDSS V3.2.2 RS6M-HWM75",
        "V3.2.2 QQQ/TQQQ/SOXL 포트폴리오",
        "JDSS 5% 가상 오버레이",
        "SGOV 운용수익(OFF)",
    ):
        assert expected in rendered
    for rejected in (
        "V3.1.1",
        "H40-S3",
        "/sgov",
    ):
        assert rejected not in rendered
