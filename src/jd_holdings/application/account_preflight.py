from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from .database import SQLiteRepository

REAL_ACCOUNT_PREFLIGHT_SAFE_MODE_KEY = "v322_real_account_preflight_safe_mode"
REAL_ACCOUNT_PREFLIGHT_AT_KEY = "last_real_account_preflight_at"


@dataclass(frozen=True)
class AccountPreflightResult:
    checked_at: datetime
    issues: tuple[str, ...]
    buying_power: Decimal | None

    @property
    def safe(self) -> bool:
        return not self.issues


class RealAccountPreflight:
    """Read-only guard that keeps a real Toss account separate from the dry-run ledger.

    V3.2.2 starts from an empty set of managed tickers. Existing QQQ/TQQQ/SOXL
    holdings or open orders are never adopted into the JDSS ledger automatically.
    """

    def __init__(self, repository: SQLiteRepository, account_client: Any) -> None:
        self.repository = repository
        self.account_client = account_client

    def run(self) -> AccountPreflightResult:
        checked_at = datetime.now(UTC)
        try:
            holdings = self.account_client.get_holdings()
            open_orders = self.account_client.list_orders(status="OPEN")
            buying_power = self.account_client.get_buying_power("USD")
            issues = self._inspect(holdings, open_orders, buying_power)
        except Exception as exc:
            issues = (f"REAL_ACCOUNT_LOOKUP_FAILED:{type(exc).__name__}",)
            buying_power = None

        self.repository.set_system_value(
            REAL_ACCOUNT_PREFLIGHT_SAFE_MODE_KEY,
            "0" if not issues else "1",
        )
        self.repository.set_system_value(
            REAL_ACCOUNT_PREFLIGHT_AT_KEY,
            checked_at.isoformat(),
        )
        if issues:
            self.repository.log_event(
                "SAFE_MODE",
                "REAL_ACCOUNT_PREFLIGHT_FAILED",
                ";".join(issues),
                context={"check": "read_only_empty_managed_symbols"},
            )
        return AccountPreflightResult(checked_at, issues, buying_power)

    @staticmethod
    def _inspect(
        holdings: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
        buying_power: Decimal,
    ) -> tuple[str, ...]:
        issues: list[str] = []
        managed = set(ALLOCATION_SYMBOLS)
        seen_holdings: set[str] = set()

        for item in holdings:
            symbol = str(item.get("symbol") or item.get("stockCode") or "").upper()
            if symbol not in managed:
                continue
            seen_holdings.add(symbol)
            raw_quantity = item.get("quantity", item.get("holdingQuantity", "0"))
            try:
                quantity = Decimal(str(raw_quantity))
            except (InvalidOperation, ValueError):
                issues.append(f"REAL_ACCOUNT_INVALID_QUANTITY:{symbol}")
                continue
            if not quantity.is_finite() or quantity < 0:
                issues.append(f"REAL_ACCOUNT_INVALID_QUANTITY:{symbol}")
                continue
            if quantity != quantity.to_integral_value():
                issues.append(f"REAL_ACCOUNT_FRACTIONAL_HOLDING:{symbol}")
            if quantity > 0:
                issues.append(f"REAL_ACCOUNT_MANAGED_SYMBOL_PRESENT:{symbol}")

        for item in open_orders:
            symbol = str(item.get("symbol") or item.get("stockCode") or "").upper()
            if symbol in managed:
                issues.append(f"REAL_ACCOUNT_OPEN_ORDER_PRESENT:{symbol}")

        try:
            normalized_buying_power = Decimal(str(buying_power))
        except (InvalidOperation, ValueError):
            issues.append("REAL_ACCOUNT_INVALID_BUYING_POWER")
        else:
            if not normalized_buying_power.is_finite() or normalized_buying_power < 0:
                issues.append("REAL_ACCOUNT_INVALID_BUYING_POWER")

        # Multiple API rows for one managed ticker make the account response ambiguous.
        managed_rows = [
            str(item.get("symbol") or item.get("stockCode") or "").upper()
            for item in holdings
            if str(item.get("symbol") or item.get("stockCode") or "").upper() in managed
        ]
        for symbol in seen_holdings:
            if managed_rows.count(symbol) > 1:
                issues.append(f"REAL_ACCOUNT_DUPLICATE_HOLDING_ROWS:{symbol}")

        return tuple(dict.fromkeys(issues))
