from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

from jd_holdings.core.models import OrderReceipt, OrderRequest
from jd_holdings.infrastructure.market_data import YFinanceDataSource


class Broker(Protocol):
    def get_price(self, symbol: str) -> Decimal: ...

    def get_holdings(self, symbol: str | None = None) -> list[dict[str, Any]]: ...

    def get_buying_power(self, currency: str = "USD") -> Decimal: ...

    def get_orderbook(self, symbol: str) -> dict[str, Any]: ...

    def place_order(self, request: OrderRequest) -> OrderReceipt: ...

    def get_order(self, order_id: str) -> dict[str, Any]: ...

    def cancel_order(self, order_id: str) -> str: ...

    def list_orders(
        self, *, status: str, symbol: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]: ...


class DryRunBroker:
    """Deterministic in-memory broker. It never reaches an external trading system."""

    def __init__(
        self,
        prices: dict[str, Decimal] | None = None,
        buying_power: Decimal = Decimal("1000000"),
    ) -> None:
        self.prices = {key.upper(): value for key, value in (prices or {}).items()}
        self.buying_power = buying_power
        self.orders: dict[str, dict[str, Any]] = {}
        self.holdings: dict[str, dict[str, Decimal | int | str]] = {}
        self.sequence = 0

    def set_price(self, symbol: str, price: Decimal) -> None:
        self.prices[symbol.upper()] = price

    def get_price(self, symbol: str) -> Decimal:
        try:
            return self.prices[symbol.upper()]
        except KeyError as exc:
            raise KeyError(f"dry-run 현재가가 없습니다: {symbol}") from exc

    def get_holdings(self, symbol: str | None = None) -> list[dict[str, Any]]:
        symbols = [symbol.upper()] if symbol else list(self.holdings)
        result = []
        for item_symbol in symbols:
            item = self.holdings.get(item_symbol)
            if item and int(item["quantity"]) > 0:
                result.append(
                    {
                        "symbol": item_symbol,
                        "name": item_symbol,
                        "marketCountry": "US",
                        "currency": "USD",
                        "quantity": str(item["quantity"]),
                        "lastPrice": str(self.get_price(item_symbol)),
                        "averagePurchasePrice": str(item["averagePurchasePrice"]),
                    }
                )
        return result

    def get_buying_power(self, currency: str = "USD") -> Decimal:
        return self.buying_power

    def get_orderbook(self, symbol: str) -> dict[str, Any]:
        price = self.get_price(symbol)
        return {
            "asks": [{"price": str(price), "volume": "1000000"}],
            "bids": [{"price": str(price), "volume": "1000000"}],
        }

    def place_order(self, request: OrderRequest) -> OrderReceipt:
        existing = next(
            (
                value
                for value in self.orders.values()
                if value.get("clientOrderId") == request.client_order_id
            ),
            None,
        )
        if existing:
            return self._receipt(existing, request.client_order_id)
        self.sequence += 1
        order_id = f"DRY-{self.sequence:08d}"
        market_price = self.get_price(request.symbol)
        fillable = request.order_type == "MARKET"
        if request.order_type == "LIMIT" and request.price is not None:
            fillable = (
                market_price <= request.price
                if request.side.upper() == "BUY"
                else market_price >= request.price
            )
        status = "FILLED" if fillable else "PENDING"
        filled = request.quantity if fillable else 0
        fill_price = market_price if fillable else None
        order = {
            "orderId": order_id,
            "clientOrderId": request.client_order_id,
            "symbol": request.symbol.upper(),
            "side": request.side.upper(),
            "orderType": request.order_type.upper(),
            "timeInForce": "DAY",
            "status": status,
            "price": str(request.price) if request.price is not None else None,
            "quantity": str(request.quantity),
            "execution": {
                "filledQuantity": str(filled),
                "averageFilledPrice": str(fill_price) if fill_price is not None else None,
                "filledAmount": str(fill_price * filled) if fill_price is not None else None,
                "commission": "0",
                "tax": "0",
                "filledAt": None,
                "settlementDate": None,
            },
            "_appliedFilledQuantity": "0",
            "_appliedFilledAmount": "0",
        }
        self.orders[order_id] = order
        if fillable:
            self._apply_fill(order)
        return self._receipt(order, request.client_order_id)

    def get_order(self, order_id: str) -> dict[str, Any]:
        return dict(self.orders[order_id])

    def cancel_order(self, order_id: str) -> str:
        order = self.orders[order_id]
        if order["status"] in {"PENDING", "PARTIAL_FILLED"}:
            order["status"] = "CANCELED"
        return order_id

    def list_orders(
        self, *, status: str, symbol: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        open_statuses = {"PENDING", "PARTIAL_FILLED", "PENDING_CANCEL", "PENDING_REPLACE"}
        values = list(self.orders.values())
        values = [
            order
            for order in values
            if ((order["status"] in open_statuses) == (status.upper() == "OPEN"))
            and (symbol is None or order["symbol"] == symbol.upper())
        ]
        return [dict(order) for order in values[:limit]]

    def fill_open_orders(self, symbol: str) -> None:
        current = self.get_price(symbol)
        for order in self.orders.values():
            if order["symbol"] != symbol.upper() or order["status"] not in {
                "PENDING",
                "PARTIAL_FILLED",
            }:
                continue
            limit = Decimal(str(order["price"]))
            fillable = current <= limit if order["side"] == "BUY" else current >= limit
            if fillable:
                self._fill_remaining_order(order, current)

    def _fill_remaining_order(self, order: dict[str, Any], price: Decimal) -> None:
        if order["status"] not in {"PENDING", "PARTIAL_FILLED"}:
            return
        total_quantity = int(Decimal(str(order["quantity"])))
        prior_quantity = int(Decimal(str(order["execution"]["filledQuantity"])))
        remaining = total_quantity - prior_quantity
        if remaining <= 0:
            order["status"] = "FILLED"
            return
        prior_average_raw = order["execution"].get("averageFilledPrice")
        prior_average = (
            Decimal(str(prior_average_raw))
            if prior_average_raw is not None and prior_quantity > 0
            else Decimal("0")
        )
        cumulative_notional = prior_average * prior_quantity + price * remaining
        cumulative_average = cumulative_notional / total_quantity
        order["status"] = "FILLED"
        order["execution"]["filledQuantity"] = str(total_quantity)
        order["execution"]["averageFilledPrice"] = str(cumulative_average)
        order["execution"]["filledAmount"] = str(cumulative_notional)
        self._apply_fill(order)

    def _apply_fill(self, order: dict[str, Any]) -> None:
        """Apply only the un-applied delta from a cumulative execution snapshot."""
        symbol = order["symbol"]
        cumulative_quantity = int(Decimal(str(order["execution"]["filledQuantity"])))
        average_raw = order["execution"].get("averageFilledPrice")
        cumulative_average = (
            Decimal(str(average_raw))
            if average_raw is not None and cumulative_quantity > 0
            else Decimal("0")
        )
        amount_raw = order["execution"].get("filledAmount")
        cumulative_notional = (
            Decimal(str(amount_raw))
            if amount_raw is not None
            else cumulative_average * cumulative_quantity
        )
        applied_quantity = int(Decimal(str(order.get("_appliedFilledQuantity", "0"))))
        applied_notional = Decimal(str(order.get("_appliedFilledAmount", "0")))
        delta_quantity = cumulative_quantity - applied_quantity
        delta_notional = cumulative_notional - applied_notional
        if delta_quantity < 0 or delta_notional < 0:
            raise RuntimeError("dry-run 누적 체결값이 이전 적용값보다 작습니다")
        if delta_quantity == 0:
            return

        existing = self.holdings.get(
            symbol, {"quantity": 0, "averagePurchasePrice": Decimal("0")}
        )
        old_qty = int(existing["quantity"])
        if order["side"] == "BUY":
            new_qty = old_qty + delta_quantity
            old_cost = Decimal(str(existing["averagePurchasePrice"])) * old_qty
            average = (old_cost + delta_notional) / new_qty
            self.holdings[symbol] = {
                "quantity": new_qty,
                "averagePurchasePrice": average,
            }
            self.buying_power -= delta_notional
        else:
            if delta_quantity > old_qty:
                raise RuntimeError("dry-run 보유수량보다 많은 누적 매도 체결입니다")
            new_qty = old_qty - delta_quantity
            self.holdings[symbol] = {
                "quantity": new_qty,
                "averagePurchasePrice": (
                    existing["averagePurchasePrice"] if new_qty else Decimal("0")
                ),
            }
            self.buying_power += delta_notional

        order["_appliedFilledQuantity"] = str(cumulative_quantity)
        order["_appliedFilledAmount"] = str(cumulative_notional)

    @staticmethod
    def _receipt(order: dict[str, Any], client_order_id: str) -> OrderReceipt:
        execution = order["execution"]
        price = execution.get("averageFilledPrice")
        raw = {key: value for key, value in order.items() if not key.startswith("_")}
        return OrderReceipt(
            client_order_id=client_order_id,
            broker_order_id=order["orderId"],
            status=order["status"],
            quantity=int(Decimal(order["quantity"])),
            filled_quantity=int(Decimal(execution["filledQuantity"])),
            average_fill_price=Decimal(price) if price is not None else None,
            raw=raw,
        )


class MarketDataDryRunBroker(DryRunBroker):
    def __init__(
        self,
        data_source: YFinanceDataSource,
        buying_power: Decimal = Decimal("1000000"),
    ) -> None:
        super().__init__(buying_power=buying_power)
        self.data_source = data_source

    def get_price(self, symbol: str) -> Decimal:
        _, price = self.data_source.current_price(symbol)
        value = Decimal(str(price))
        self.prices[symbol.upper()] = value
        return value

    def get_order(self, order_id: str) -> dict[str, Any]:
        order = self.orders[order_id]
        if order["status"] in {"PENDING", "PARTIAL_FILLED"} and order["orderType"] == "LIMIT":
            current = self.get_price(order["symbol"])
            limit = Decimal(str(order["price"]))
            fillable = current <= limit if order["side"] == "BUY" else current >= limit
            if fillable:
                self._fill_remaining_order(order, current)
        return dict(order)
