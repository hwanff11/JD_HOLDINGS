from __future__ import annotations

from decimal import Decimal

import pytest

from jd_holdings.application.broker import MarketDataDryRunBroker


class FailingCurrentPriceSource:
    def current_price(self, symbol: str):
        raise RuntimeError(f"quote unavailable: {symbol}")


def test_holdings_snapshot_does_not_require_live_quote() -> None:
    broker = MarketDataDryRunBroker(FailingCurrentPriceSource())
    broker.holdings["SGOV"] = {
        "quantity": 17,
        "averagePurchasePrice": Decimal("100.23"),
    }

    holdings = broker.get_holdings()

    assert holdings == [
        {
            "symbol": "SGOV",
            "name": "SGOV",
            "marketCountry": "US",
            "currency": "USD",
            "quantity": "17",
            "lastPrice": "100.23",
            "averagePurchasePrice": "100.23",
        }
    ]


def test_market_price_operations_still_require_live_quote() -> None:
    broker = MarketDataDryRunBroker(FailingCurrentPriceSource())

    with pytest.raises(RuntimeError, match="quote unavailable: SGOV"):
        broker.get_price("SGOV")
