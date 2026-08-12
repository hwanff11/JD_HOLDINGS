from __future__ import annotations

import jd_holdings.infrastructure.telegram_bot as telegram_bot_module
from jd_holdings.infrastructure.telegram_bot_final import (
    FinalTelegramBotApp,
    _v31_runtime_text,
)


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
        "$7,200",
        "36%",
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


def test_final_runtime_translates_shared_v30_labels():
    legacy = (
        "JDSS V3 MONTHLY_H05 | 월간 10개월 추세 · 종목당 15% | "
        "JDSS 점수 전략 · 종목당 최대 5% | JDSS 5% 부스터 | "
        "코어 규칙 : 10개월 추세 ON · 목표 15%"
    )
    rendered = _v31_runtime_text(legacy)
    for expected in (
        "JDSS V3.1 TWIN-H40-S3",
        "월간 6개월 추세 · 첫 ON 10%, 지속 15%",
        "자금 상한 40%, S3 최대 36%",
        "JDSS H40-S3 부스터",
        "코어 규칙 : 6개월 추세 ON · 첫 ON 10%, 지속 15%",
    ):
        assert expected in rendered
    for rejected in ("MONTHLY_H05", "종목당 최대 5%", "JDSS 5% 부스터", "10개월 추세 ON"):
        assert rejected not in rendered
