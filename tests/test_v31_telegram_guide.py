from __future__ import annotations

import jd_holdings.infrastructure.telegram_bot as telegram_bot_module
from jd_holdings.infrastructure.telegram_bot_final import FinalTelegramBotApp


def test_final_runtime_guide_matches_v31_contract():
    assert FinalTelegramBotApp is not None
    guide = "\n".join(telegram_bot_module._guide_cards())
    for expected in (
        "V3.1",
        "6개월",
        "첫 달 10%",
        "15%",
        "$8,000",
        "40%",
        "-2%",
        "-5%",
        "+4%",
        "+10%",
        "재매수와 자동손절은 비활성화",
        "SGOV",
    ):
        assert expected in guide
    for rejected in ("10개월 이동평균", "최대 5%", "-7%", "+6%", "20거래일 경과"):
        assert rejected not in guide
    assert len(telegram_bot_module._guide_cards()) == 5
    assert all(len(card) < 4096 for card in telegram_bot_module._guide_cards())
