from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from jd_holdings.config import StrategyConfig
from jd_holdings.core.models import IdleCashState, OrderReceipt, OrderRequest
from jd_holdings.infrastructure.market_clock import MarketClock

from .broker import Broker
from .database import ApprovalError, SQLiteRepository
from .order_manager import OrderManager, build_client_order_id

TERMINAL_STATUSES = {"FILLED", "CANCELED", "REJECTED", "REPLACED"}


class IdleCashReleasePending(ApprovalError):
    """The SGOV sale was submitted, but cash is not available yet."""

    def __init__(self, message: str, signal_id: int | None = None) -> None:
        super().__init__(message)
        self.signal_id = signal_id


@dataclass(frozen=True)
class IdleCashSnapshot:
    state: IdleCashState
    broker_quantity: int
    price: Decimal
    market_value: Decimal
    buying_power: Decimal
    target_value: Decimal
    safe_mode: bool


class IdleCashManager:
    """Keep only JDSS-owned idle capital in SGOV and release it before entries."""

    def __init__(
        self,
        config: StrategyConfig,
        repository: SQLiteRepository,
        broker: Broker,
        order_manager: OrderManager,
        market_clock: MarketClock | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.broker = broker
        self.order_manager = order_manager
        self.market_clock = market_clock or MarketClock()

    @property
    def enabled(self) -> bool:
        return self.config.idle_cash.enabled

    def snapshot(self) -> IdleCashSnapshot:
        state = self.repository.get_idle_cash_state()
        price = self.broker.get_price(state.symbol) if self.enabled else Decimal("0")
        holdings = self.broker.get_holdings(state.symbol) if self.enabled else []
        broker_qty = sum(
            int(Decimal(str(item.get("quantity", "0"))))
            for item in holdings
            if str(item.get("symbol", "")).upper() == state.symbol
        )
        target = self._target_value()
        return IdleCashSnapshot(
            state=state,
            broker_quantity=broker_qty,
            price=price,
            market_value=price * state.managed_quantity,
            buying_power=self.broker.get_buying_power("USD"),
            target_value=target,
            safe_mode=self.repository.get_system_value("idle_cash_safe_mode") == "1",
        )

    def run_once(self, now: datetime | None = None) -> list[str]:
        if not self.enabled:
            return []
        events = self.refresh_orders()
        if self.market_clock.classify_session(now) == "closed":
            return events
        if self.repository.get_system_value("idle_cash_safe_mode") == "1":
            return events
        if self._open_cash_orders():
            return events
        if self.repository.has_active_approvals():
            return events
        if self.repository.has_active_cash_release_intents(now):
            return events
        if any(
            order["side"] == "BUY" and order["symbol"] in self.config.enabled_symbols
            for order in self.repository.open_orders()
        ):
            return events

        snapshot = self.snapshot()
        target_qty = self._target_quantity(snapshot.target_value, snapshot.price)
        difference = target_qty - snapshot.state.managed_quantity
        if difference > 0:
            available = max(Decimal("0"), snapshot.buying_power - self.config.idle_cash.cash_buffer)
            limit = self._buy_limit(snapshot.price)
            affordable = int(
                (available / (limit * (Decimal("1") + self.config.global_.buy_fee))).to_integral_value(
                    rounding=ROUND_DOWN
                )
            )
            quantity = min(difference, affordable)
            if quantity > 0 and quantity * limit >= self.config.idle_cash.minimum_order_amount:
                receipt = self._submit("BUY", quantity, limit, "SGOV_SWEEP_BUY", target_qty)
                events.append(f"SGOV 유휴자금 예치 {quantity}주 ({receipt.status})")
        return events

    def ensure_buying_power(
        self,
        required: Decimal,
        *,
        signal_id: int | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        """Release JDSS-managed SGOV and block the entry until proceeds are available."""
        if not self.enabled:
            return
        self.refresh_orders()
        self._cancel_open_sweep_buys()
        required_with_buffer = required + self.config.idle_cash.cash_buffer
        reserved = self.repository.reserved_cash_release_amount(signal_id)
        buying_power = max(Decimal("0"), self.broker.get_buying_power("USD") - reserved)
        if buying_power >= required_with_buffer:
            return
        if self.repository.get_system_value("idle_cash_safe_mode") == "1":
            raise ApprovalError("SGOV 정합성 SAFE_MODE로 전략 매수를 중단했습니다")
        state = self.repository.get_idle_cash_state()
        if state.managed_quantity <= 0:
            raise ApprovalError("전략 매수에 필요한 달러 매수가능금액이 부족합니다")
        if signal_id is not None and expires_at is not None:
            self.repository.upsert_cash_release_intent(signal_id, required, expires_at)
        if self._open_cash_orders():
            raise IdleCashReleasePending(
                "SGOV 현금화 주문이 아직 체결 대기 중입니다", signal_id
            )
        price = self.broker.get_price(state.symbol)
        limit = self._sell_limit(price)
        deficiency = required_with_buffer - buying_power
        net_price = limit * (Decimal("1") - self.config.global_.sell_fee)
        quantity = int((deficiency / net_price).to_integral_value(rounding=ROUND_UP))
        quantity = min(quantity, state.managed_quantity)
        receipt = self._submit(
            "SELL",
            quantity,
            limit,
            "SGOV_ENTRY_RELEASE",
            state.managed_quantity - quantity,
            signal_id=signal_id,
        )
        if receipt.status in TERMINAL_STATUSES and receipt.filled_quantity > 0:
            self.repository.apply_idle_cash_fill(receipt.client_order_id)
        available_after = max(
            Decimal("0"), self.broker.get_buying_power("USD") - reserved
        )
        if available_after < required_with_buffer:
            if receipt.status in {"REJECTED", "CANCELED"}:
                if signal_id is not None:
                    self.repository.update_cash_release_intent(signal_id, status="CANCELED")
                raise ApprovalError("SGOV 현금화 주문이 거절되어 전략 매수를 중단했습니다")
            raise IdleCashReleasePending(
                "SGOV 현금화 주문을 제출했습니다. 체결되면 최종 승인 버튼을 보내드릴게요",
                signal_id,
            )

    def refresh_orders(self) -> list[str]:
        events: list[str] = []
        for order in self._open_cash_orders():
            if not order.get("broker_order_id"):
                continue
            receipt = self.order_manager.refresh_order(str(order["client_order_id"]))
            self.repository.apply_idle_cash_fill(receipt.client_order_id)
            created_at = datetime.fromisoformat(str(order["created_at"]))
            age = (datetime.now(UTC) - created_at.astimezone(UTC)).total_seconds()
            if (
                receipt.status not in TERMINAL_STATUSES
                and age >= self.config.idle_cash.reprice_after_seconds
            ):
                self.broker.cancel_order(receipt.broker_order_id)
                receipt = self.order_manager.refresh_order(str(order["client_order_id"]))
                self.repository.apply_idle_cash_fill(receipt.client_order_id)
                events.append(f"SGOV {order['purpose']} 미체결 잔량 재가격 준비")
            if receipt.status in TERMINAL_STATUSES:
                events.append(
                    f"SGOV {order['purpose']} {receipt.filled_quantity}주 체결 반영"
                )
        return events

    def cancel_entry_release_orders(self, signal_id: int) -> None:
        for order in self._open_cash_orders():
            if (
                order["purpose"] != "SGOV_ENTRY_RELEASE"
                or int(order.get("signal_id") or 0) != signal_id
                or not order.get("broker_order_id")
            ):
                continue
            self.broker.cancel_order(str(order["broker_order_id"]))
            receipt = self.order_manager.refresh_order(str(order["client_order_id"]))
            self.repository.apply_idle_cash_fill(receipt.client_order_id)

    def _cancel_open_sweep_buys(self) -> None:
        for order in self._open_cash_orders():
            if (
                order["purpose"] != "SGOV_SWEEP_BUY"
                or not order.get("broker_order_id")
            ):
                continue
            self.broker.cancel_order(str(order["broker_order_id"]))
            receipt = self.order_manager.refresh_order(str(order["client_order_id"]))
            self.repository.apply_idle_cash_fill(receipt.client_order_id)

    def _submit(
        self,
        side: str,
        quantity: int,
        price: Decimal,
        purpose: str,
        target_qty: int,
        *,
        signal_id: int | None = None,
    ) -> OrderReceipt:
        state = self.repository.get_idle_cash_state()
        attempt = self.repository.next_idle_cash_order_attempt(state.symbol, purpose)
        client_order_id = build_client_order_id(
            symbol=state.symbol,
            purpose=purpose,
            signal_id=signal_id,
            unique_context=f"v{state.version}-target{target_qty}-a{attempt}",
        )
        receipt = self.order_manager.submit(
            OrderRequest(
                client_order_id=client_order_id,
                symbol=state.symbol,
                side=side,
                order_type="LIMIT",
                quantity=quantity,
                price=price,
                purpose=purpose,
                signal_id=signal_id,
            ),
            cycle_id=None,
        )
        if receipt.filled_quantity > 0 or receipt.status in TERMINAL_STATUSES:
            self.repository.apply_idle_cash_fill(receipt.client_order_id)
        return receipt

    def _target_value(self) -> Decimal:
        allocated = self.config.total_strategy_capital
        invested = self.repository.strategy_invested_capital()
        return max(
            Decimal("0"), allocated - invested - self.config.idle_cash.cash_buffer
        )

    def _target_quantity(self, target_value: Decimal, price: Decimal) -> int:
        if price <= 0:
            return 0
        return int((target_value / self._buy_limit(price)).to_integral_value(rounding=ROUND_DOWN))

    def _open_cash_orders(self) -> list[dict]:
        return [
            order
            for order in self.repository.open_orders(self.config.idle_cash.symbol)
            if str(order["purpose"]).startswith("SGOV_")
        ]

    def _buy_limit(self, price: Decimal) -> Decimal:
        best_ask = self._best_orderbook_price("asks")
        if best_ask is not None:
            return (best_ask + self.config.idle_cash.orderbook_limit_offset).quantize(
                Decimal("0.01"), rounding=ROUND_UP
            )
        return (price * (Decimal("1") + self.config.idle_cash.buy_limit_buffer)).quantize(
            Decimal("0.01"), rounding=ROUND_UP
        )

    def _sell_limit(self, price: Decimal) -> Decimal:
        best_bid = self._best_orderbook_price("bids")
        if best_bid is not None:
            return max(
                Decimal("0.01"),
                (best_bid - self.config.idle_cash.orderbook_limit_offset).quantize(
                    Decimal("0.01"), rounding=ROUND_DOWN
                ),
            )
        return (price * (Decimal("1") - self.config.idle_cash.sell_limit_buffer)).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )

    def _best_orderbook_price(self, side: str) -> Decimal | None:
        try:
            levels = self.broker.get_orderbook(self.config.idle_cash.symbol).get(side) or []
            if not levels:
                return None
            prices = [
                Decimal(str(level["price"]))
                for level in levels
                if Decimal(str(level["price"])) > 0
            ]
            if not prices:
                return None
            return min(prices) if side == "asks" else max(prices)
        except Exception:
            return None
