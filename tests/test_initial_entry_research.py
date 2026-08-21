from __future__ import annotations

import pandas as pd

from jd_holdings.research.initial_entry import (
    EntryScenario,
    simulate_entry_window,
)


def _frames(*, rising: bool) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    index = pd.bdate_range("2025-01-02", periods=80)
    if rising:
        qqq = [100 * (1.01**i) for i in range(len(index))]
    else:
        qqq = [100 * (0.99**i) for i in range(len(index))]
    frames = {
        "QQQ": pd.DataFrame({"open": qqq, "close": qqq}, index=index),
        "TQQQ": pd.DataFrame({"open": [100.0] * len(index), "close": [100.0] * len(index)}, index=index),
        "SOXL": pd.DataFrame({"open": [100.0] * len(index), "close": [100.0] * len(index)}, index=index),
    }
    targets = pd.DataFrame(
        {
            "QQQ": [1.0] * len(index),
            "TQQQ": [0.0] * len(index),
            "SOXL": [0.0] * len(index),
            "leverage": [1.0] * len(index),
        },
        index=index,
    )
    return frames, targets


def test_entry_scenario_stage_schedule():
    scenario = EntryScenario("CURRENT", (0.50, 0.75, 1.0), 3)
    assert [scenario.fraction_for_offset(i) for i in range(8)] == [
        0.50,
        0.50,
        0.50,
        0.75,
        0.75,
        0.75,
        1.0,
        1.0,
    ]


def test_staged_entry_trails_lump_sum_in_persistent_rise():
    frames, targets = _frames(rising=True)
    lump = simulate_entry_window(
        frames,
        targets,
        start_position=1,
        scenario=EntryScenario("LUMP", (1.0,), 0),
        horizons=(21,),
    )
    staged = simulate_entry_window(
        frames,
        targets,
        start_position=1,
        scenario=EntryScenario("STAGED", (0.50, 0.75, 1.0), 3),
        horizons=(21,),
    )
    assert staged["return_21"] < lump["return_21"]


def test_staged_entry_protects_capital_in_persistent_decline():
    frames, targets = _frames(rising=False)
    lump = simulate_entry_window(
        frames,
        targets,
        start_position=1,
        scenario=EntryScenario("LUMP", (1.0,), 0),
        horizons=(21,),
    )
    staged = simulate_entry_window(
        frames,
        targets,
        start_position=1,
        scenario=EntryScenario("STAGED", (0.50, 0.75, 1.0), 3),
        horizons=(21,),
    )
    assert staged["return_21"] > lump["return_21"]
    assert staged["mdd_21"] > lump["mdd_21"]
