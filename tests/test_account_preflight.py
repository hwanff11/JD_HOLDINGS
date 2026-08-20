from __future__ import annotations

from decimal import Decimal

from jd_holdings.application.account_preflight import (
    REAL_ACCOUNT_PREFLIGHT_AT_KEY,
    REAL_ACCOUNT_PREFLIGHT_SAFE_MODE_KEY,
    RealAccountPreflight,
)
from jd_holdings.application.database import SQLiteRepository


class AccountClient:
    def __init__(self, *, holdings=None, orders=None, buying_power="50000") -> None:
        self.holdings = holdings or []
        self.orders = orders or []
        self.buying_power = Decimal(buying_power)

    def get_holdings(self):
        return self.holdings

    def list_orders(self, *, status):
        assert status == "OPEN"
        return self.orders

    def get_buying_power(self, currency):
        assert currency == "USD"
        return self.buying_power


def test_real_account_preflight_accepts_empty_managed_tickers(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "preflight.db", config)
    client = AccountClient(
        holdings=[{"symbol": "QQQM", "quantity": "3"}],
        orders=[{"symbol": "NVDA", "status": "PENDING"}],
    )

    result = RealAccountPreflight(repository, client).run()

    assert result.safe
    assert result.buying_power == Decimal("50000")
    assert repository.get_system_value(REAL_ACCOUNT_PREFLIGHT_SAFE_MODE_KEY) == "0"
    assert repository.get_system_value(REAL_ACCOUNT_PREFLIGHT_AT_KEY)


def test_real_account_preflight_blocks_managed_holdings_and_orders(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "preflight.db", config)
    client = AccountClient(
        holdings=[
            {"symbol": "QQQ", "quantity": "2"},
            {"symbol": "SOXL", "quantity": "0.5"},
        ],
        orders=[{"symbol": "TQQQ", "status": "PENDING"}],
    )

    result = RealAccountPreflight(repository, client).run()

    assert not result.safe
    assert set(result.issues) == {
        "REAL_ACCOUNT_MANAGED_SYMBOL_PRESENT:QQQ",
        "REAL_ACCOUNT_FRACTIONAL_HOLDING:SOXL",
        "REAL_ACCOUNT_MANAGED_SYMBOL_PRESENT:SOXL",
        "REAL_ACCOUNT_OPEN_ORDER_PRESENT:TQQQ",
    }
    assert repository.get_system_value(REAL_ACCOUNT_PREFLIGHT_SAFE_MODE_KEY) == "1"
    assert repository.recent_events(1)[0]["event_type"] == "REAL_ACCOUNT_PREFLIGHT_FAILED"


def test_real_account_preflight_fails_closed_when_toss_is_unavailable(tmp_path, config):
    repository = SQLiteRepository(tmp_path / "preflight.db", config)

    class FailingClient(AccountClient):
        def get_holdings(self):
            raise TimeoutError("temporary")

    result = RealAccountPreflight(repository, FailingClient()).run()

    assert result.issues == ("REAL_ACCOUNT_LOOKUP_FAILED:TimeoutError",)
    assert repository.get_system_value(REAL_ACCOUNT_PREFLIGHT_SAFE_MODE_KEY) == "1"
