from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

profit_lock = importlib.import_module("scripts.research_profit_lock_parity")
_booster_quantity = profit_lock._booster_quantity
split_positive_profit = profit_lock.split_positive_profit


def test_split_positive_profit_25_percent() -> None:
    reinvest, reserve = split_positive_profit(1000.0, 0.25)
    assert reinvest == 250.0
    assert reserve == 750.0


def test_split_positive_profit_30_percent() -> None:
    reinvest, reserve = split_positive_profit(1000.0, 0.30)
    assert reinvest == 300.0
    assert reserve == 700.0


def test_split_non_positive_profit_does_not_reinvest_or_lock() -> None:
    assert split_positive_profit(0.0, 0.30) == (0.0, 0.0)
    assert split_positive_profit(-500.0, 0.30) == (0.0, 0.0)


def test_tp1_quantity_matches_v31_ceil_30_percent_rule() -> None:
    assert _booster_quantity(10, "TP1") == 3
    assert _booster_quantity(11, "TP1") == 4
    assert _booster_quantity(1, "TP1") == 1


def test_non_tp1_sells_all_remaining_quantity() -> None:
    assert _booster_quantity(17, "TP2") == 17
