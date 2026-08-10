from __future__ import annotations

from decimal import Decimal

from jd_holdings.core.models import OrderRequest
from jd_holdings.infrastructure.toss_client import TossClient


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return FakeResponse({"access_token": "test-token", "expires_in": 86400})

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        if url.endswith("/api/v1/orders") and method == "POST":
            return FakeResponse(
                {
                    "result": {
                        "orderId": "broker-1",
                        "clientOrderId": kwargs["json"]["clientOrderId"],
                    }
                }
            )
        if url.endswith("/api/v1/prices"):
            return FakeResponse({"result": [{"symbol": "TQQQ", "lastPrice": "100.25"}]})
        if url.endswith("/api/v1/orderbook"):
            return FakeResponse(
                {
                    "result": {
                        "asks": [{"price": "100.02", "volume": "100"}],
                        "bids": [{"price": "100.01", "volume": "200"}],
                    }
                }
            )
        raise AssertionError((method, url, kwargs))


def test_current_official_order_schema_and_idempotency_key():
    session = FakeSession()
    client = TossClient(
        client_id="client", client_secret="secret", account_seq="1", session=session
    )
    receipt = client.place_order(
        OrderRequest(
            client_order_id="JDSS-TQQQ-E1-abc123",
            symbol="TQQQ",
            side="BUY",
            order_type="LIMIT",
            quantity=8,
            price=Decimal("100.50"),
            purpose="ENTRY_1",
            signal_id=1,
        )
    )
    assert receipt.broker_order_id == "broker-1"
    order_call = session.requests[-1][2]
    assert order_call["json"]["clientOrderId"] == "JDSS-TQQQ-E1-abc123"
    assert order_call["json"]["quantity"] == "8"
    assert order_call["json"]["price"] == "100.50"
    assert client.get_price("TQQQ") == Decimal("100.25")
    assert client.get_orderbook("SGOV")["asks"][0]["price"] == "100.02"


def test_market_order_omits_price_for_toss_api():
    session = FakeSession()
    client = TossClient(
        client_id="client", client_secret="secret", account_seq="1", session=session
    )
    client.place_order(
        OrderRequest(
            client_order_id="JDSS-SGOV-SELL-market",
            symbol="SGOV",
            side="SELL",
            order_type="MARKET",
            quantity=2,
            price=None,
            purpose="SGOV_ENTRY_RELEASE",
        )
    )
    payload = session.requests[-1][2]["json"]
    assert payload["orderType"] == "MARKET"
    assert "price" not in payload
