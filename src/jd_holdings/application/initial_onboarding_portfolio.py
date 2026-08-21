from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from jd_holdings import __version__
from jd_holdings.core.initial_onboarding import (
    STATUS_ACTIVE,
    STATUS_BYPASSED,
    STATUS_COMPLETED,
    STATUS_DISABLED,
    STATUS_NOT_STARTED,
    InitialOnboardingPolicy,
    scaled_target_quantity,
    sessions_elapsed,
)
from jd_holdings.core.v322_allocation import ALLOCATION_SYMBOLS

from .managed_account import committed_core_buy_quantity
from .portfolio_service import (
    TARGET_QTY_GENERATION_KEY,
    PortfolioRunResult,
    PortfolioService,
)

ONBOARDING_STATUS_KEY = "v322_initial_onboarding_status"
ONBOARDING_STAGE_KEY = "v322_initial_onboarding_stage"
ONBOARDING_STAGE_STARTED_KEY = "v322_initial_onboarding_stage_started_trade_date"
ONBOARDING_STAGE_FILLED_KEY = "v322_initial_onboarding_stage_filled_trade_date"

_VALID_STATUSES = {
    STATUS_NOT_STARTED,
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    STATUS_BYPASSED,
    STATUS_DISABLED,
}


class InitialOnboardingPortfolioService(PortfolioService):
    """Apply a one-time 50→75→100% exposure cap to the first portfolio entry.

    The underlying V3.2.2 target weights and HWM75 risk budget are unchanged. Only
    risk-increasing BUY quantity is capped while onboarding is active. Risk-reducing
    SELLs still execute automatically against the capped target.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.onboarding_policy = InitialOnboardingPolicy.from_config(self.config)
        self._bootstrap_onboarding_state()

    def _bootstrap_onboarding_state(self) -> None:
        persisted = self.repository.get_system_value(ONBOARDING_STATUS_KEY)
        if persisted is not None:
            if persisted not in _VALID_STATUSES:
                raise RuntimeError(f"알 수 없는 initial onboarding 상태: {persisted}")
            return
        if not self.onboarding_policy.enabled:
            self.repository.set_system_value(ONBOARDING_STATUS_KEY, STATUS_DISABLED)
            return
        if not self._managed_portfolio_is_flat():
            self.repository.set_system_value(ONBOARDING_STATUS_KEY, STATUS_BYPASSED)
            self.repository.log_event(
                "WARNING",
                "INITIAL_ONBOARDING_BYPASSED",
                "기존 관리 포지션이 있어 최초진입 분할 로직을 자동 적용하지 않았습니다",
            )

    def onboarding_status(self) -> str:
        value = self.repository.get_system_value(ONBOARDING_STATUS_KEY)
        if value is not None:
            if value not in _VALID_STATUSES:
                raise RuntimeError(f"알 수 없는 initial onboarding 상태: {value}")
            return value
        if self.onboarding_policy.enabled:
            return STATUS_NOT_STARTED
        return STATUS_DISABLED

    def onboarding_stage(self) -> int:
        raw = self.repository.get_system_value(ONBOARDING_STAGE_KEY)
        if raw in (None, ""):
            return 0
        stage = int(raw)
        if not 0 <= stage <= self.onboarding_policy.total_stages:
            raise RuntimeError(f"initial onboarding 저장 단계 오류: {stage}")
        return stage

    def start_onboarding(self, now: datetime | None = None) -> dict[str, Any]:
        if not self.onboarding_policy.enabled:
            raise RuntimeError("최초진입 분할 기능이 비활성화되어 있습니다")
        status = self.onboarding_status()
        if status == STATUS_COMPLETED:
            raise RuntimeError("최초진입 분할은 이미 완료되었습니다")
        if status == STATUS_ACTIVE:
            return self.onboarding_snapshot(now)
        reason = self._start_block_reason()
        if reason is not None:
            raise RuntimeError(reason)
        completed = self._latest_completed(now)
        self._invalidate_active_core_buy_signals("INITIAL_ONBOARDING_STARTED")
        self.repository.set_system_value(ONBOARDING_STATUS_KEY, STATUS_ACTIVE)
        self.repository.set_system_value(ONBOARDING_STAGE_KEY, "1")
        self.repository.set_system_value(
            ONBOARDING_STAGE_STARTED_KEY, completed.isoformat()
        )
        self.repository.set_system_value(ONBOARDING_STAGE_FILLED_KEY, "")
        self.repository.log_event(
            "INFO",
            "INITIAL_ONBOARDING_STARTED",
            (
                "V3.2.2 최초진입 1단계를 시작했습니다 "
                f"(누적 {self.onboarding_policy.fraction_for_stage(1) * 100:.0f}%)"
            ),
            context={"trade_date": completed.isoformat(), "stage": 1},
        )
        return self.onboarding_snapshot(now)

    def advance_onboarding(self, now: datetime | None = None) -> dict[str, Any]:
        if self.onboarding_status() != STATUS_ACTIVE:
            raise RuntimeError("진행 중인 최초진입 분할이 없습니다")
        completed = self._latest_completed(now)
        self._refresh_onboarding_progress(completed)
        snapshot = self.onboarding_snapshot(now)
        if snapshot["status"] == STATUS_COMPLETED:
            return snapshot
        stage = int(snapshot["stage"])
        if stage >= self.onboarding_policy.total_stages:
            raise RuntimeError("이미 마지막 최초진입 단계입니다")
        if not bool(snapshot["next_stage_ready"]):
            remaining = int(snapshot["sessions_remaining"])
            if not bool(snapshot["stage_filled"]):
                raise RuntimeError("현재 단계 목표수량이 아직 모두 체결되지 않았습니다")
            raise RuntimeError(
                f"다음 단계까지 미국 거래일 {remaining}일을 더 기다려야 합니다"
            )

        next_stage = stage + 1
        self._invalidate_active_core_buy_signals("INITIAL_ONBOARDING_STAGE_ADVANCED")
        self.repository.set_system_value(ONBOARDING_STAGE_KEY, str(next_stage))
        self.repository.set_system_value(
            ONBOARDING_STAGE_STARTED_KEY, completed.isoformat()
        )
        self.repository.set_system_value(ONBOARDING_STAGE_FILLED_KEY, "")
        self.repository.log_event(
            "INFO",
            "INITIAL_ONBOARDING_STAGE_ADVANCED",
            (
                f"V3.2.2 최초진입 {next_stage}단계를 열었습니다 "
                f"(누적 {self.onboarding_policy.fraction_for_stage(next_stage) * 100:.0f}%)"
            ),
            context={"trade_date": completed.isoformat(), "stage": next_stage},
        )
        return self.onboarding_snapshot(now)

    def run_allocation(self, now: datetime | None = None) -> PortfolioRunResult | None:
        result = super().run_allocation(now)
        if self.onboarding_status() != STATUS_ACTIVE:
            return result
        if self._portfolio_is_safe():
            return result

        completed = self._latest_completed(now)
        progress_events = self._refresh_onboarding_progress(completed)
        if not progress_events:
            return result
        if result is None:
            return PortfolioRunResult(
                trade_date=completed.isoformat(),
                signals=(),
                events=tuple(progress_events),
            )
        return PortfolioRunResult(
            trade_date=result.trade_date,
            signals=result.signals,
            events=tuple(result.events) + tuple(progress_events),
        )

    def run_month_end(self, now: datetime | None = None) -> PortfolioRunResult | None:
        return self.run_allocation(now)

    def _apply_target(
        self,
        symbol: str,
        evaluation_date,
        *,
        allow_buy: bool,
        current: datetime,
    ) -> tuple[int | None, str | None]:
        status = self.onboarding_status()
        if status in {STATUS_COMPLETED, STATUS_BYPASSED, STATUS_DISABLED}:
            return super()._apply_target(
                symbol, evaluation_date, allow_buy=allow_buy, current=current
            )

        core = self.repository.get_core_position(symbol)
        target = self._effective_target_quantity(core)
        difference = target - int(core["qty"])
        if difference < 0 and not allow_buy:
            price = self.broker.get_price(symbol)
            receipt = self._sell_core(
                symbol,
                -difference,
                price,
                str(core["signal_trade_date"]),
            )
            return (
                None,
                f"{symbol} 최초진입 위험축소 {-difference}주 "
                f"({receipt.status}, {receipt.filled_quantity}/{receipt.quantity}주)",
            )
        if difference <= 0 or not allow_buy:
            return None, None

        committed = committed_core_buy_quantity(self.repository, symbol)
        difference = max(0, difference - committed)
        if difference <= 0 or self._symbol_is_safe(symbol):
            return None, None

        price = self.broker.get_price(symbol)
        planned_budget = (
            Decimal(difference)
            * price
            * (Decimal("1") + self.config.global_.buy_limit_buffer)
            * (Decimal("1") + self.config.global_.buy_fee)
        )
        signal_id, created = self.repository.create_core_buy_signal(
            symbol=symbol,
            trade_date=datetime.fromisoformat(
                str(core["signal_trade_date"])
            ).date(),
            signal_close=price,
            planned_budget=planned_budget,
            valid_until=self.market_clock.next_session_close(evaluation_date),
            code_version=__version__,
            reactivate_existing=True,
        )
        if not created:
            return None, None
        stage = self.onboarding_stage()
        fraction = self._effective_fraction()
        weight = Decimal(str(core["target_weight"]))
        return (
            signal_id,
            (
                f"{symbol} 최초진입 {stage}/{self.onboarding_policy.total_stages} "
                f"(누적 {fraction * 100:.0f}%) · 전략 목표 {weight * 100:.2f}% · "
                f"{difference}주 매수 승인 대기"
            ),
        )

    def _effective_fraction(self) -> Decimal:
        status = self.onboarding_status()
        if status == STATUS_NOT_STARTED:
            return Decimal("0")
        if status == STATUS_ACTIVE:
            stage = self.onboarding_stage()
            if stage < 1:
                return Decimal("0")
            return self.onboarding_policy.fraction_for_stage(stage)
        return Decimal("1")

    def _effective_target_quantity(self, core: dict[str, Any]) -> int:
        full_target = int(core["target_qty"])
        current_qty = int(core["qty"])
        status = self.onboarding_status()
        if status == STATUS_NOT_STARTED:
            # Do not increase risk before the operator explicitly starts stage 1.
            # Still allow a stale position to be reduced if the strategy target fell.
            return min(current_qty, full_target)
        if status == STATUS_ACTIVE:
            return scaled_target_quantity(full_target, self._effective_fraction())
        return full_target

    def _refresh_onboarding_progress(self, completed: date) -> list[str]:
        if self.onboarding_status() != STATUS_ACTIVE:
            return []
        stage = self.onboarding_stage()
        if stage < 1:
            return []
        filled = self._stage_is_filled()
        filled_raw = self.repository.get_system_value(ONBOARDING_STAGE_FILLED_KEY) or ""
        events: list[str] = []

        if not filled:
            if filled_raw:
                self.repository.set_system_value(ONBOARDING_STAGE_FILLED_KEY, "")
            return events

        if not filled_raw:
            self.repository.set_system_value(
                ONBOARDING_STAGE_FILLED_KEY, completed.isoformat()
            )
            filled_raw = completed.isoformat()
            events.append(
                f"최초진입 {stage}/{self.onboarding_policy.total_stages} 목표수량 체결 완료"
            )

        if stage == self.onboarding_policy.total_stages:
            self.repository.set_system_value(ONBOARDING_STATUS_KEY, STATUS_COMPLETED)
            self.repository.log_event(
                "INFO",
                "INITIAL_ONBOARDING_COMPLETED",
                "V3.2.2 최초진입 3단계 분할매수를 완료하고 일반 운용으로 전환했습니다",
                context={"trade_date": completed.isoformat(), "stage": stage},
            )
            events.append("최초진입 3단계 완료 · 이후 V3.2.2 일반 운용으로 전환")
        return events

    def _stage_is_filled(self) -> bool:
        if not self.repository.get_system_value(TARGET_QTY_GENERATION_KEY):
            return False
        return all(
            int(core["qty"]) >= self._effective_target_quantity(core)
            for core in self.repository.core_positions()
            if str(core["symbol"]) in ALLOCATION_SYMBOLS
        )

    def onboarding_snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        completed = self._latest_completed(now)
        status = self.onboarding_status()
        stage = self.onboarding_stage()
        if status == STATUS_COMPLETED:
            stage = self.onboarding_policy.total_stages
        fraction = self._effective_fraction()
        filled_raw = self.repository.get_system_value(ONBOARDING_STAGE_FILLED_KEY) or ""
        filled_date = date.fromisoformat(filled_raw) if filled_raw else None
        elapsed = sessions_elapsed(self.market_clock.calendar, filled_date, completed)
        stage_filled = status == STATUS_ACTIVE and stage >= 1 and self._stage_is_filled()
        next_ready = (
            status == STATUS_ACTIVE
            and stage < self.onboarding_policy.total_stages
            and stage_filled
            and filled_date is not None
            and elapsed >= self.onboarding_policy.minimum_sessions_between_stages
        )
        remaining = 0
        if status == STATUS_ACTIVE and stage < self.onboarding_policy.total_stages:
            if filled_date is None or not stage_filled:
                remaining = self.onboarding_policy.minimum_sessions_between_stages
            else:
                remaining = max(
                    0,
                    self.onboarding_policy.minimum_sessions_between_stages - elapsed,
                )

        cores = {
            str(core["symbol"]): core
            for core in self.repository.core_positions()
            if str(core["symbol"]) in ALLOCATION_SYMBOLS
        }
        effective_targets = {
            symbol: self._effective_target_quantity(cores[symbol])
            for symbol in ALLOCATION_SYMBOLS
            if symbol in cores
        }
        full_targets = {
            symbol: int(cores[symbol]["target_qty"])
            for symbol in ALLOCATION_SYMBOLS
            if symbol in cores
        }
        reason = (
            self._start_block_reason()
            if status in {STATUS_NOT_STARTED, STATUS_BYPASSED}
            else None
        )
        return {
            "enabled": self.onboarding_policy.enabled,
            "status": status,
            "stage": stage,
            "total_stages": self.onboarding_policy.total_stages,
            "fraction": fraction,
            "next_fraction": (
                self.onboarding_policy.fraction_for_stage(stage + 1)
                if status == STATUS_ACTIVE and stage < self.onboarding_policy.total_stages
                else None
            ),
            "minimum_sessions_between_stages": (
                self.onboarding_policy.minimum_sessions_between_stages
            ),
            "stage_started_trade_date": self.repository.get_system_value(
                ONBOARDING_STAGE_STARTED_KEY
            )
            or "",
            "stage_filled_trade_date": filled_raw,
            "stage_filled": stage_filled,
            "sessions_elapsed": elapsed,
            "sessions_remaining": remaining,
            "next_stage_ready": next_ready,
            "can_start": status in {STATUS_NOT_STARTED, STATUS_BYPASSED} and reason is None,
            "start_block_reason": reason,
            "effective_targets": effective_targets,
            "full_targets": full_targets,
            "completed_trade_date": completed.isoformat(),
        }

    def snapshot(self) -> dict[str, object]:
        snapshot = super().snapshot()
        snapshot["initial_onboarding"] = self.onboarding_snapshot()
        return snapshot

    def _latest_completed(self, now: datetime | None = None) -> date:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        return self.market_clock.latest_completed_session(
            current, delay_minutes=self.config.scheduler.signal_delay_minutes
        )

    def _managed_portfolio_is_flat(self) -> bool:
        if any(
            int(core["qty"]) > 0
            for core in self.repository.core_positions()
            if str(core["symbol"]) in ALLOCATION_SYMBOLS
        ):
            return False
        return not any(
            self.repository.get_position(symbol).quantity > 0
            for symbol in self.config.enabled_symbols
        )

    def _start_block_reason(self) -> str | None:
        if not self._managed_portfolio_is_flat():
            return "기존 JDSS 관리 포지션이 있어 최초진입을 시작할 수 없습니다"
        if any(
            str(order.get("purpose")) in {"CORE_REBALANCE_BUY", "CORE_REBALANCE_SELL"}
            for order in self.repository.open_orders()
        ):
            return "기존 allocation 미체결 주문이 있어 최초진입을 시작할 수 없습니다"
        if self._portfolio_is_safe():
            return "SAFE_MODE에서는 최초진입을 시작할 수 없습니다"
        return None

    def _invalidate_active_core_buy_signals(self, reason: str) -> None:
        for signal in self.repository.active_signals():
            if str(signal.get("action")) != "CORE_REBALANCE_BUY":
                continue
            self.repository.mark_signal(
                int(signal["signal_id"]),
                status="INVALID",
                processed=True,
                reason=reason,
            )
