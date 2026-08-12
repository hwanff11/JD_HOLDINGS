from __future__ import annotations

import jd_holdings.infrastructure.telegram_bot as telegram_bot_module
from jd_holdings.infrastructure.telegram_bot_final import (
    FinalTelegramBotApp,
    _v311_runtime_text,
)


def test_final_runtime_guide_matches_v311_contract():
    assert FinalTelegramBotApp is not None
    guide = "\n".join(telegram_bot_module._guide_cards())
    for expected in (
        "V3.1.1",
        "$50,000",
        "6개월",
        "10%",
        "15%",
        "$20,000",
        "40%",
        "$18,000",
        "36%",
        "-2%",
        "-5%",
        "+4%",
        "+10%",
        "재투자하지 않습니다",
        "SGOV는 사용하지 않고",
        "QQQ/QQQM",
    ):
        assert expected in guide
    for rejected in ("10개월 이동평균", "최대 5%", "$8,000", "$7,200", "-7%", "+6%"):
        assert rejected not in guide
    assert len(telegram_bot_module._guide_cards()) == 5
    assert all(len(card) < 4096 for card in telegram_bot_module._guide_cards())


def test_final_runtime_translates_shared_v30_labels_and_hides_sgov_menu_line():
    legacy = (
        "JDSS V3 MONTHLY_H05 | 월간 10개월 추세 · 종목당 15% | "
        "JDSS 점수 전략 · 종목당 최대 5% | JDSS 5% 부스터 | "
        "코어 규칙 : 10개월 추세 ON · 목표 15%\n"
        "• <code>/sgov</code> : 💵 JDSS SGOV 유휴자금\n"
        "SGOV 추정수익: $0.00"
    )
    rendered = _v311_runtime_text(legacy)
    for expected in (
        "JDSS V3.1.1 TWIN-H40-S3",
        "월간 6개월 추세 · 첫 ON 10%, 지속 15%",
        "종목당 cap $20k, S3 최대 $18k",
        "JDSS H40-S3 부스터",
        "코어 규칙 : 6개월 추세 ON · 첫 ON 10%, 지속 15%",
        "SGOV 운용수익(OFF)",
    ):
        assert expected in rendered
    for rejected in (
        "MONTHLY_H05",
        "종목당 최대 5%",
        "JDSS 5% 부스터",
        "10개월 추세 ON",
        "/sgov",
    ):
        assert rejected not in rendered
