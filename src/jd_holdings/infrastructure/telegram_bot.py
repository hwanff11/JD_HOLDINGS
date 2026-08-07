# Telegram HTML templates are intentionally kept as complete message lines.
# ruff: noqa: E501

from __future__ import annotations

import html
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from jd_holdings import __version__
from jd_holdings.application.analysis_service import AnalysisResult, AnalysisService
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.order_monitor import OrderMonitor
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.trading_service import QuoteChangedError, TradingService
from jd_holdings.backtest.engine import BacktestEngine, BacktestResult
from jd_holdings.backtest.performance import maximum_drawdown
from jd_holdings.config import StrategyConfig
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.infrastructure.toss_client import TossClient
from jd_holdings.settings import RuntimeSettings

LOGGER = logging.getLogger(__name__)


class BacktestCommandError(ValueError):
    """Raised when a Telegram /backtest command is not safe or valid."""


@dataclass(frozen=True)
class TelegramBacktestRequest:
    symbols: tuple[str, ...]
    start: date
    end: date


def parse_backtest_request(
    text: str,
    enabled_symbols: tuple[str, ...],
    default_start: str,
    latest_completed: date,
) -> TelegramBacktestRequest:
    """Parse `/bt [ALL|SYMBOL] [START] [END]` without accepting arbitrary input."""
    parts = (text or "").split()[1:]
    selected = enabled_symbols
    if not parts:
        if "SOXL" not in enabled_symbols:
            raise BacktestCommandError("기본 종목 SOXL이 활성화되지 않았습니다.")
        parts = ["SOXL", "300"]
    if parts and not _looks_like_iso_date(parts[0]):
        requested = parts.pop(0).upper()
        if requested == "ALL":
            selected = enabled_symbols
        elif requested.replace("-", "").replace(".", "").isalnum() and len(requested) <= 10:
            selected = (requested,)
        else:
            raise BacktestCommandError("유효한 종목 티커를 입력해 주세요. (예: /bt NVDA 100, /bt ALL)")
    if len(parts) > 2:
        raise BacktestCommandError("형식: /bt [ALL|종목] [시작일] [종료일]")
    if len(parts) == 1 and parts[0].isdigit():
        trading_days = int(parts[0])
        if not 1 <= trading_days <= 5000:
            raise BacktestCommandError("거래일은 1~5000 사이로 입력해 주세요.")
        calendar = MarketClock().calendar
        lookback_start = latest_completed - timedelta(days=trading_days * 2 + 30)
        sessions = calendar.sessions_in_range(lookback_start, latest_completed)
        if len(sessions) < trading_days:
            raise BacktestCommandError("요청한 거래일 기간을 계산하지 못했습니다.")
        start = pd.Timestamp(sessions[-trading_days]).date()
        if start < date.fromisoformat(default_start):
            raise BacktestCommandError(f"시작일은 {default_start} 이후여야 합니다.")
        return TelegramBacktestRequest(symbols=selected, start=start, end=latest_completed)
    try:
        minimum_start = date.fromisoformat(default_start)
        start = date.fromisoformat(parts[0]) if parts else minimum_start
        end = date.fromisoformat(parts[1]) if len(parts) == 2 else latest_completed
    except ValueError as exc:
        raise BacktestCommandError("날짜는 YYYY-MM-DD 형식이어야 합니다.") from exc
    if start < minimum_start:
        raise BacktestCommandError(f"시작일은 {minimum_start.isoformat()} 이후여야 합니다.")
    if start > end:
        raise BacktestCommandError("시작일은 종료일보다 늦을 수 없습니다.")
    if end > latest_completed:
        raise BacktestCommandError(
            f"종료일은 최신 완결 거래일 {latest_completed.isoformat()} 이하여야 합니다."
        )
    return TelegramBacktestRequest(symbols=selected, start=start, end=end)


def _looks_like_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _money(value: object) -> str:
    return f"${Decimal(str(value)):,.2f}"


def _won(value: object) -> str:
    return f"₩{Decimal(str(value)):,.0f}"


def _quantity(value: object) -> str:
    return f"{Decimal(str(value)):,.2f}".rstrip("0").rstrip(".")


def _profit_loss(value: object) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    amount = value.get("amountAfterCost", value.get("amount"))
    rate = value.get("rateAfterCost", value.get("rate"))
    if amount is None or rate is None:
        return None
    return _money(amount), f"{Decimal(str(rate)) * 100:+.2f}%"


def _regime_label(value: str) -> str:
    return {
        "GREEN": "🟢 강세장",
        "YELLOW": "🟡 중립장",
        "RED": "🔴 약세장",
        "BULLISH": "🟢 강세장",
        "NEUTRAL": "🟡 중립장",
        "BEARISH": "🔴 약세장",
    }.get(value, value)


def _grade_label(value: str) -> str:
    return {
        "EXCELLENT": "강력 매수",
        "GOOD": "매수 관심",
        "FAIR": "중립",
        "WEAK": "관망",
        "POOR": "매수 보류",
    }.get(value, value)


def _state_label(value: str) -> str:
    return {
        "EMPTY": "☕ 관망 중",
        "WATCH": "☀️ 신호 감시 중",
        "CYCLING": "🔄 분할매수 진행 중",
        "HOLDING": "📦 보유 중",
        "SAFE_MODE": "🚨 안전 점검 필요",
    }.get(value, value.replace("_", " "))


def _format_position_state(position) -> str:
    state_val = (
        position.state.value
        if hasattr(position.state, "value")
        else str(position.state)
    )
    if state_val == "EMPTY":
        return "☕ 관망 중"
    if state_val == "WATCH":
        return "☀️ 신호 감시 중"
    if state_val in {"CYCLING", "HOLDING"}:
        count = getattr(position, "entry_count", 0)
        if count == 1:
            return "🟢 1차 진입 완료"
        elif count == 2:
            return "🟡 2차 분할매수"
        elif count == 3:
            return "🟠 3차 분할매수"
        elif count >= 4:
            return "🔴 4차 풀매수 완료"
        return "🔄 분할매수 진행 중"
    if state_val == "SAFE_MODE":
        return "🚨 안전 점검 필요"
    return _state_label(state_val)


def _action_label(value: str) -> str:
    return {
        "WAIT": "지금은 관망",
        "WATCH": "신호 관찰",
        "STAGE_BUY": "분할매수 검토",
        "REBUY": "재매수 검토",
        "HOLD": "보유 유지",
    }.get(value, value.replace("_", " "))


def _is_us_holding(item: dict) -> bool:
    country = str(item.get("marketCountry") or item.get("country") or "").upper()
    currency = str(item.get("currency") or "").upper()
    return country in {"US", "USA", "UNITED_STATES"} or currency == "USD"


class TelegramBotApp:
    def __init__(
        self,
        config: StrategyConfig,
        settings: RuntimeSettings,
        repository: SQLiteRepository,
        analysis_service: AnalysisService,
        trading_service: TradingService,
        order_monitor: OrderMonitor,
        reconciliation_service: ReconciliationService,
        data_source: YFinanceDataSource,
        market_clock: MarketClock,
        account_client: TossClient | None = None,
    ) -> None:
        if not settings.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN이 설정되지 않았습니다")
        if len(settings.allowed_chat_ids) != 1:
            raise ValueError("JDSS Telegram은 정확히 1개의 관리자 Chat ID만 허용합니다")
        self.config = config
        self.settings = settings
        self.repository = repository
        self.analysis_service = analysis_service
        self.trading_service = trading_service
        self.order_monitor = order_monitor
        self.reconciliation_service = reconciliation_service
        self.data_source = data_source
        self.market_clock = market_clock
        self.account_client = account_client
        self.allowed_chat_id = settings.allowed_chat_ids[0]
        self.bot = telebot.TeleBot(settings.telegram_bot_token, threaded=True)
        self._stop = threading.Event()
        self._backtest_lock = threading.Lock()
        self._last_monitor = 0.0
        self._register_handlers()

    def _authorized_message(self, message) -> bool:
        return int(message.chat.id) == self.allowed_chat_id

    def _authorized_callback(self, call) -> bool:
        return (
            int(call.message.chat.id) == self.allowed_chat_id
            and int(call.from_user.id) == self.allowed_chat_id
        )

    def _send(self, text: str, *, markup=None, chat_id: int | None = None) -> None:
        self.bot.send_message(
            chat_id or self.allowed_chat_id,
            text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )

    def _send_long(self, text: str, limit: int = 3500) -> None:
        chunk: list[str] = []
        size = 0
        for line in text.splitlines():
            added = len(line) + (1 if chunk else 0)
            if chunk and size + added > limit:
                self._send("\n".join(chunk))
                chunk = []
                size = 0
            chunk.append(line)
            size += len(line) + (1 if len(chunk) > 1 else 0)
        if chunk:
            self._send("\n".join(chunk))

    def _dashboard_markup(self) -> InlineKeyboardMarkup:
        markup = InlineKeyboardMarkup(row_width=2)
        for symbol in self.config.enabled_symbols:
            markup.row(
                InlineKeyboardButton(f"✨ {symbol} 상태", callback_data=f"status|{symbol}"),
                InlineKeyboardButton(f"🎯 {symbol} 점수", callback_data=f"score|{symbol}"),
            )
        return markup

    @staticmethod
    def _format_score_message(result: AnalysisResult) -> str:
        snapshot = result.snapshot
        score = result.score
        return (
            f"🎯 <b>[{result.symbol} 지표 상세 분석]</b>\n\n"
            f"📊 <b>총점</b> : <code>{score.total}점</code> / 100점 ({_grade_label(score.grade.value)})\n"
            f"🌐 <b>시장 국면</b> : {_regime_label(score.regime.value)}\n\n"
            "✨ <b>점수 구성</b>\n"
            f"• 시장 국면 : <code>{score.regime_score:2d} / 25점</code>\n"
            f"• 과매도 점수 : <code>{score.oversold_score:2d} / 40점</code>\n"
            f"• 반등 점수 : <code>{score.reversal_score:2d} / 20점</code>\n"
            f"• 거래량 점수 : <code>{score.volume_score:2d} / 10점</code>\n"
            f"• ATR 변동성 : <code>{score.atr_score:2d} / 5점</code>\n\n"
            "🌞 <b>보조 지표 현황</b>\n"
            f"• CCI (5 / 10) : <code>{snapshot.cci5:.1f} / {snapshot.cci10:.1f}</code>\n"
            f"• RSI (5 / 14) : <code>{snapshot.rsi5:.1f} / {snapshot.rsi14:.1f}</code>\n"
            f"• ATR 비율 : <code>{snapshot.atr_pct * 100:.2f}%</code>\n"
            f"• 거래량 비율 : <code>{snapshot.volume_ratio:.2f}배</code>\n"
            f"• 종가 위치 : <code>{snapshot.close_position:.2f}</code>\n\n"
            f"💡 <b>최종 전략 판단</b> : <b>{_action_label(result.decision.action.value)}</b>"
        )

    def _format_status_message(self, symbol: str) -> str:
        position = self.repository.get_position(symbol)
        plan = self.repository.active_tp_plan(symbol)
        lines = [
            f"✨ <b>[{symbol} 상세 포지션]</b>",
            "",
            f"• <b>상태</b> : {_state_label(position.state.value)}",
            f"• <b>보유 수량</b> : <code>{_quantity(position.quantity)}주</code>",
            f"• <b>평균 단가</b> : <code>{_money(position.average_price)}</code>",
            f"• <b>매수 원가</b> : <code>{_money(position.current_cost_basis)}</code>",
            f"• <b>누적 매수금</b> : <code>{_money(position.staged_entry_capital)}</code>",
            f"• <b>1차 진입가</b> : <code>{_money(position.anchor_price)}</code>",
        ]
        if plan:
            lines.extend(
                [
                    "",
                    "🎯 <b>자동 익절 목표</b>",
                    f"• <b>1차 익절 (TP1)</b> : <code>{_money(Decimal(plan['tp1_price']))}</code> (<code>{plan['tp1_target_qty']}주</code>)",
                    f"• <b>2차 익절 (TP2)</b> : <code>{_money(Decimal(plan['tp2_price']))}</code> (<code>{plan['tp2_target_qty']}주</code>)",
                ]
            )
        return "\n".join(lines)

    def _send_account(self) -> None:
        if self.account_client is None:
            self._send("🔐 토스 계좌 연결 정보를 확인해 주세요.")
            return
        try:
            buying_power = self.account_client.get_buying_power("USD")
            krw_buying_power = self.account_client.get_buying_power("KRW")
            holdings = [item for item in self.account_client.get_holdings() if _is_us_holding(item)]
            lines = [
                "☀️ <b>[JH홀딩스 미국주식 계좌]</b>",
                "",
                "💰 <b>계좌 요약</b>",
                f"• <b>달러 주문가능</b> : <code>{_money(buying_power)}</code>",
                f"• <b>원화 주문가능</b> : <code>{_won(krw_buying_power)}</code>",
                f"• <b>보유 종목</b> : <code>{len(holdings)}개</code>",
            ]
            if holdings:
                lines.append("\n━━━━━━━━━━━━━━━━━━━━")
            for item in holdings:
                symbol = str(item.get("symbol") or item.get("stockCode") or "-").upper()
                quantity = item.get("quantity") or item.get("holdingQuantity") or "0"
                average = item.get("averagePrice") or item.get("averagePurchasePrice")
                current = item.get("currentPrice") or item.get("lastPrice")
                detail = f"✨ <b>{html.escape(symbol)}</b>"
                detail += f"\n• <b>보유 수량</b> : <code>{_quantity(quantity)}주</code>"
                if average is not None:
                    detail += f"\n• <b>평균 단가</b> : <code>{_money(average)}</code>"
                if current is not None:
                    detail += f"\n• <b>현재 가격</b> : <code>{_money(current)}</code>"
                profit = _profit_loss(item.get("profitLoss"))
                if profit:
                    detail += f"\n• <b>평가 손익</b> : <code>{profit[0]}</code> (<code>{profit[1]}</code>)"
                lines.append(detail)
            if not holdings:
                lines.append("현재 보유 중인 미국주식 종목이 없습니다.")

            lines.extend(
                [
                    "━━━━━━━━━━━━━━━━━━━━",
                    "💡 <i>안전을 위해 조회 전용 모드로 작동하고 있습니다.</i>",
                ]
            )
            self._send("\n".join(lines))
        except Exception as exc:
            LOGGER.exception("계좌 조회 실패")
            self._send(f"❌ 토스 계좌 조회 중 오류 발생:\n<code>{html.escape(str(exc))}</code>")

    def _get_account_lines(self) -> list[str]:
        if self.account_client is None:
            return ["💰 <b>[토스 실시간 계좌 잔고]</b>", "💡 <i>토스 계좌 정보 연결 대기 중 (조회 전용)</i>"]
        try:
            buying_power = self.account_client.get_buying_power("USD")
            holdings = [item for item in self.account_client.get_holdings() if _is_us_holding(item)]
            lines = [
                "💰 <b>[토스 실시간 계좌 잔고]</b>",
                f"• <b>주문가능 달러</b> : <code>{_money(buying_power)}</code>",
            ]
            for item in holdings:
                symbol = str(item.get("symbol") or item.get("stockCode") or "-").upper()
                quantity = item.get("quantity") or item.get("holdingQuantity") or "0"
                average = item.get("averagePrice") or item.get("averagePurchasePrice")
                current = item.get("currentPrice") or item.get("lastPrice")
                detail = f"\n✨ <b>{html.escape(symbol)}</b>"
                detail += f"\n• <b>수량 / 평단</b> : <code>{_quantity(quantity)}주</code> (평단 <code>{_money(average) if average else '$0.00'}</code>)"
                profit = _profit_loss(item.get("profitLoss"))
                if current is not None and profit:
                    detail += f"\n• <b>현재가 / 손익</b> : <code>{_money(current)}</code> (<code>{profit[0]}</code>, <code>{profit[1]}</code>)"
                elif current is not None:
                    detail += f"\n• <b>현재가</b> : <code>{_money(current)}</code>"
                lines.append(detail)
            if not holdings:
                lines.append("현재 보유 중인 미국주식 종목이 없습니다.")
            return lines
        except Exception:
            return ["💰 <b>[토스 실시간 계좌 잔고]</b>", "💡 <i>실시간 계좌 정보 조회 중...</i>"]

    def _register_handlers(self) -> None:
        bot = self.bot

        @bot.message_handler(commands=["ping", "p"])
        def ping(message):
            if not self._authorized_message(message):
                return
            lock_text = (
                "🟢 해제 (실거래 가능)"
                if self.settings.live_trading_enabled
                else "🔒 잠금 (모의 전용)"
            )
            mode_label = (
                "🧪 모의투자"
                if self.settings.trading_mode == "dry_run"
                else "🔥 실거래"
            )
            self._send(
                "✨ <b>[JH홀딩스 JDSS 봇 상태]</b>\n\n"
                f"• <b>버전</b> : v{__version__}\n"
                f"• <b>운영 모드</b> : {mode_label}\n"
                f"• <b>실주문 잠금</b> : {lock_text}\n\n"
                "✅ <b>정상 가동 중입니다.</b>"
            )

        @bot.message_handler(commands=["dashboard", "d"])
        def dashboard(message):
            if not self._authorized_message(message):
                return
            try:
                results = self.analysis_service.analyze_all()
                mode_label = (
                    "🧪 모의투자" if self.settings.trading_mode == "dry_run" else "🔥 실거래"
                )
                lines = [
                    "🌟 <b>[JH홀딩스 통합 대시보드]</b>",
                    "",
                ]
                if results:
                    lines.append(
                        f"🟢 <b>시장 국면</b> : {_regime_label(results[0].score.regime.value)}"
                    )
                    lines.append(f"📅 <b>분석 기준일</b> : {results[0].trade_date.isoformat()}")
                lines.append(f"⚙️ <b>운영 모드</b> : {mode_label}")
                lines.append("━━━━━━━━━━━━━━━━━━━━")

                for result in results:
                    position = self.repository.get_position(result.symbol)
                    cap = (
                        position.cycle_exposure_cap
                        if position.cycle_exposure_cap > 0
                        else self.config.global_.capital_per_symbol
                    )
                    lines.extend(
                        [
                            f"✨ <b>{result.symbol}</b>",
                            f"• <b>포지션 상태</b> : {_format_position_state(position)}",
                            f"• <b>JDSS 점수</b> : <code>{result.score.total}점</code> ({_grade_label(result.score.grade.value)})",
                            f"• <b>보유 잔고</b> : <code>{_quantity(position.quantity)}주</code> (평단 <code>{_money(position.average_price)}</code>)",
                            f"• <b>누적 매수금</b> : <code>{_money(position.staged_entry_capital)}</code> / <code>{_money(cap)}</code>",
                            f"• <b>전략 판단</b> : <b>{_action_label(result.decision.action.value)}</b>",
                            "",
                        ]
                    )
                lines.extend(
                    [
                        "━━━━━━━━━━━━━━━━━━━━",
                        "💡 <i>/status [종목] 및 /score [종목] 명령어로 세부 정보를 확인하실 수 있습니다.</i>",
                    ]
                )
                self._send("\n".join(lines), markup=None)
            except Exception as exc:
                LOGGER.exception("dashboard 실패")
                self._send(f"❌ 대시보드 수집 실패:\n<code>{html.escape(str(exc))}</code>")

        @bot.message_handler(commands=["account", "acct", "balance"])
        def account(message):
            if not self._authorized_message(message):
                return
            self._send_account()

        @bot.message_handler(commands=["score", "sc", "indicator", "i"])
        def score(message):
            if not self._authorized_message(message):
                return
            requested = self._requested_symbol(message.text)
            try:
                results = self.analysis_service.analyze_all()
                for result in results:
                    if requested and result.symbol != requested:
                        continue
                    self._send(self._format_score_message(result))
                self.notify_new_signals(results)
            except Exception as exc:
                LOGGER.exception("score 실패")
                self._send(f"❌ 점수 계산 중 오류 발생:\n<code>{html.escape(str(exc))}</code>")

        @bot.message_handler(commands=["signal", "sg"])
        def signal(message):
            if not self._authorized_message(message):
                return
            signals = self.repository.active_signals()
            if not signals:
                self._send(
                    "🤖 <b>현재 대기 중인 JDSS 매수 신호가 없습니다.</b>\n차분하게 다음 타점을 기다립니다. ☕"
                )
                return
            for item in signals:
                self._send_signal(item)

        @bot.message_handler(commands=["status", "st"])
        def status(message):
            if not self._authorized_message(message):
                return
            requested = self._requested_symbol(message.text)
            for symbol in self.config.enabled_symbols:
                if requested and symbol != requested:
                    continue
                self._send(self._format_status_message(symbol))

        @bot.message_handler(commands=["guide", "g", "info", "explain"])
        def guide(message):
            if not self._authorized_message(message):
                return
            try:
                card1 = (
                    "📖 <b>[JDSS v2.0 지표 및 점수 체계 가이드]</b>\n\n"
                    "🎯 <b>1. JDSS 종합 점수 체계 (100점 만점)</b>\n"
                    "💡 <i>(단일 지표가 아닌 아래 모든 지표들의 총 합산 점수입니다!)</i>\n"
                    "• <b>50점 이상</b> : 여러 하락/반등 지표를 복합 통과한 1차 매수 타점 🟢\n"
                    "• <b>82점 이상</b> : 강력 매수 구간 (상승 파동 고승률)\n"
                    "• <b>90점 이상</b> : 극상의 최적 매수 조건 (S등급)\n\n"
                    "🟢 <b>2. 시장 국면 지표 (25점 만점)</b>\n"
                    "• <b>20일 / 60일 이동평균선(EMA)</b> 교차 상태 분석\n"
                    "• <b>🟢 강세장 (25점)</b> : 20일 EMA &gt; 60일 EMA (상승 추세)\n"
                    "• <b>🟡 중립장 (15점)</b> : 20일/60일 EMA 횡보 구간\n"
                    "• <b>🔴 약세장 ( 0점)</b> : 20일 EMA &lt; 60일 EMA (하락 추세)\n\n"
                    "📉 <b>3. 과매도 지표 (40점 만점)</b>\n"
                    "• <b>CCI (5 / 10)</b> : -200 이하 시 기술적 바닥권 진입\n"
                    "• <b>RSI (5 / 14)</b> : 20~30 이하 시 과도한 투매 상태\n"
                    "• <b>볼린저 밴드</b> : 하단선(0.98배) 이탈 시 깊은 저점\n\n"
                    "🔄 <b>4. 반등 확정 지표 (20점 만점)</b>\n"
                    "• 추락하는 칼날을 방지하기 위해 당일 캔들 양봉 전환 및 종가 위치(0.5 이상) 확인 시 안전 반등 점수 부여"
                )
                card2 = (
                    "🛒 <b>[JDSS 4단계 분할 매수 및 익절 메커니즘]</b>\n\n"
                    "📊 <b>5. 거래량 및 변동성 (15점 만점)</b>\n"
                    "• <b>거래량 비율</b> : 20일 평균 대비 1.5~2.0배 수급 분출\n"
                    "• <b>ATR 변동성</b> : 적정 변동성 폭 유지 확인\n\n"
                    "🛒 <b>6. 4단계 분할 매수 시스템</b>\n"
                    "• <b>1차 매수 (40%)</b> : JDSS 50점 및 반등 5점 충족 🟢\n"
                    "• <b>2차 매수 (30%)</b> : 1차 진입가 대비 <b>-2% 하락</b> 🟡\n"
                    "• <b>3차 매수 (20%)</b> : 1차 진입가 대비 <b>-4% 하락</b> 🟠\n"
                    "• <b>4차 매수 (10%)</b> : 1차 진입가 대비 <b>-7% 하락</b> 🔴\n\n"
                    "💰 <b>7. 목표 익절 메커니즘 (Take Profit)</b>\n"
                    "• <b>1차 익절 (TP1)</b> : 평단 대비 <b>+4.0%</b> 도달 시 수량 50% 분할 매도\n"
                    "• <b>2차 익절 (TP2)</b> : 평단 대비 <b>+8.0%</b> 도달 시 전량 매도 및 사이클 종료\n\n"
                    "💡 <i>/score [종목] 명령어로 세부 지표를 실시간 확인하실 수 있습니다.</i>"
                )
                self._send(card1, chat_id=message.chat.id)
                self._send(card2, chat_id=message.chat.id)
            except Exception as exc:
                LOGGER.exception("guide 실패")
                self._send(
                    f"❌ 가이드 출력 실패:\n<code>{html.escape(str(exc))}</code>",
                    chat_id=message.chat.id,
                )

        @bot.message_handler(commands=["order", "o"])
        def orders(message):
            if not self._authorized_message(message):
                return
            values = self.repository.open_orders()
            if not values:
                self._send("✅ <b>[미체결 주문]</b>\n\n현재 대기 중인 주문이 없습니다.")
                return
            lines = ["✨ <b>[JDSS 미체결 주문]</b>", ""]
            for item in values:
                price_str = _money(item["price"]) if item["price"] else "시장가"
                lines.append(
                    f"🌟 <b>{item['symbol']}</b> · {item['purpose']}\n"
                    f"<code>├ 방향 : {'매수' if item['side'] == 'BUY' else '매도'}</code>\n"
                    f"<code>├ 수량 : {_quantity(item['qty'])}주</code>\n"
                    f"<code>├ 가격 : {price_str}</code>\n"
                    f"<code>└ 상태 : {item['status']}</code>\n"
                )
            self._send("\n".join(lines))

        @bot.message_handler(commands=["errors", "err"])
        def errors(message):
            if not self._authorized_message(message):
                return
            events = self.repository.recent_events(10)
            if not events:
                self._send("✅ <b>[시스템 이벤트]</b>\n\n최근 기록된 이벤트가 없습니다.")
                return
            lines = ["✨ <b>[최근 JDSS 시스템 이벤트]</b>", ""]
            for event in events:
                lines.append(
                    f"<code>[{event['severity']}] {event['created_at'][11:19]}</code>\n"
                    f"<b>{html.escape(event['event_type'])}</b>: {html.escape(event['message'])}\n"
                )
            self._send("\n".join(lines))

        @bot.message_handler(commands=["backtest", "bt"])
        def backtest(message):
            """
            텔레그램 봇의 /bt (또는 /backtest) 명령어를 처리합니다.
            
            사용자로부터 종목명과 테스트 기간(일수)을 입력받아 별도의 스레드에서 백테스트 시뮬레이션을 구동합니다.
            결과가 생성되면, 타임라인 포맷팅 및 요약 통계를 텔레그램 메시지로 응답합니다.
            백테스트는 매우 무거운 작업이므로 `_backtest_lock`을 통해 중복 실행을 방지합니다.
            """
            if not self._authorized_message(message):
                return
            try:
                completed = self.market_clock.latest_completed_session()
                request = parse_backtest_request(
                    message.text,
                    self.config.enabled_symbols,
                    self.config.backtest.default_start,
                    completed,
                )
            except BacktestCommandError as exc:
                self._send(
                    f"⚠️ <b>{html.escape(str(exc))}</b>\n\n"
                    "💡 <b>사용 예시</b>\n"
                    "<code>/bt</code> (SOXL 최근 300거래일)\n"
                    "<code>/bt TQQQ 100</code>\n"
                    "<code>/bt ALL 250</code>"
                )
                return
            if not self._backtest_lock.acquire(blocking=False):
                self._send(
                    "⏳ <b>다른 백테스트가 이미 실행 중입니다.</b>\n완료 알림 후 다시 시도해 주세요."
                )
                return
            symbols_text = " + ".join(request.symbols)
            self._send(
                f"🌞 <b>[{symbols_text} 백테스트 시작]</b>\n\n"
                f"• <b>시작일</b> : <code>{request.start}</code>\n"
                f"• <b>종료일</b> : <code>{request.end}</code>\n\n"
                "💡 <i>요청 기간 내 과거 데이터가 부족할 경우,\n"
                "   데이터가 존재하는 기간까지만 진행됩니다.</i>\n\n"
                "💡 <i>실제 계좌 및 주문에는 전혀 영향을 주지 않습니다.</i>"
            )
            try:
                threading.Thread(
                    target=self._run_backtest_and_send,
                    args=(request,),
                    daemon=True,
                ).start()
            except Exception:
                self._backtest_lock.release()
                raise

        @bot.message_handler(commands=["help", "h", "start"])
        def help_handler(message):
            if not self._authorized_message(message):
                return
            self._send(
                "☀️ <b>[JH홀딩스 JDSS 메뉴]</b>\n\n"
                "• <code>/dashboard</code> : 통합 대시보드\n"
                "• <code>/account</code> : 💰 토스 계좌 잔고\n"
                "• <code>/status</code> : 종목별 상세 포지션\n"
                "• <code>/score</code> : JDSS 세부 지표 분석\n"
                "• <code>/signal</code> : 활성 매수 신호\n"
                "• <code>/backtest</code> : 자유 종목 백테스트\n"
                "• <code>/guide</code> : 📖 JDSS 용어 및 지표 가이드\n"
                "• <code>/order</code> : 미체결 주문 현황\n"
                "• <code>/errors</code> : 최근 시스템 기록\n"
                "• <code>/ping</code> : 봇 상태 확인\n\n"
                "✨ <b>사용 예시</b> : <code>/score TQQQ</code> · <code>/bt NVDA 100</code>\n\n"
                "💡 <i>모든 매수는 2단계 안전 승인을 거쳐 실행됩니다.</i>"
            )

        @bot.callback_query_handler(
            func=lambda call: call.data.startswith("score|")
            or call.data.startswith("status|")
        )
        def dashboard_detail_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            try:
                command, symbol = call.data.split("|", 1)
                if symbol not in self.config.enabled_symbols:
                    raise ValueError("지원하지 않는 종목입니다.")
                if command == "score":
                    result = next(
                        item
                        for item in self.analysis_service.analyze_all()
                        if item.symbol == symbol
                    )
                    self._send(self._format_score_message(result))
                    answer = f"{symbol} 점수를 불러왔습니다."
                else:
                    self._send(self._format_status_message(symbol))
                    answer = f"{symbol} 포지션을 불러왔습니다."
                bot.answer_callback_query(call.id, answer)
            except Exception as exc:
                LOGGER.exception("대시보드 세부 조회 실패")
                bot.answer_callback_query(call.id, str(exc), show_alert=True)

        @bot.callback_query_handler(func=lambda call: call.data.startswith("rv|"))
        def review_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            try:
                _, approval_id, token = call.data.split("|", 2)
                quote = self.trading_service.consume_review(int(approval_id), token)
                markup = InlineKeyboardMarkup()
                markup.add(
                    InlineKeyboardButton(
                        "✅ 최종 매수 실행",
                        callback_data=(f"ex|{quote.execution_approval_id}|{quote.execution_token}"),
                    )
                )
                markup.add(InlineKeyboardButton("❌ 취소", callback_data="cancel|review"))
                self._send(
                    f"🌞 <b>[{quote.symbol} 최종 매수 승인]</b>\n\n"
                    f"• <b>현재 가격</b> : <code>{_money(quote.current_price)}</code>\n"
                    f"• <b>지정 가격</b> : <code>{_money(quote.limit_price)}</code>\n"
                    f"• <b>주문 수량</b> : <code>{_quantity(quote.quantity)}주</code>\n"
                    f"• <b>투자 금액</b> : <code>{_money(quote.planned_budget)}</code>\n"
                    f"• <b>예상 수수료</b> : <code>{_money(quote.estimated_fee)}</code>\n\n"
                    f"⏰ <i>{self.config.global_.execution_token_ttl_seconds}초 안에 아래 실행 버튼을 눌러주세요.</i>",
                    markup=markup,
                )
                bot.answer_callback_query(call.id, "최종 주문조건을 계산했습니다.")
            except Exception as exc:
                LOGGER.exception("매수 검토 실패")
                bot.answer_callback_query(call.id, str(exc), show_alert=True)

        @bot.callback_query_handler(func=lambda call: call.data.startswith("ex|"))
        def execute_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            try:
                _, approval_id, token = call.data.split("|", 2)
                receipt = self.trading_service.execute(int(approval_id), token)
                mode_text = "모의주문" if self.settings.trading_mode == "dry_run" else "실주문"
                self._send(
                    f"✅ <b>[{mode_text} 전송 완료]</b> ✨\n\n"
                    f"• <b>주문 번호</b> : <code>{html.escape(receipt.broker_order_id)}</code>\n"
                    f"• <b>처리 상태</b> : <code>{html.escape(receipt.status)}</code>\n"
                    f"• <b>체결 수량</b> : <code>{_quantity(receipt.filled_quantity)} / {_quantity(receipt.quantity)}주</code>"
                )
                bot.answer_callback_query(call.id, f"{mode_text}이 처리되었습니다.")
            except QuoteChangedError as exc:
                bot.answer_callback_query(call.id, str(exc), show_alert=True)
            except Exception as exc:
                LOGGER.exception("최종 주문 실행 실패")
                bot.answer_callback_query(call.id, str(exc), show_alert=True)

        @bot.callback_query_handler(func=lambda call: call.data.startswith("cancel|"))
        def cancel_callback(call):
            if self._authorized_callback(call):
                self._send(
                    "❌ <b>매수 승인 요청을 취소했습니다.</b>\n다음 좋은 타점을 기다릴게요. ☕"
                )
                bot.answer_callback_query(call.id, "취소했습니다.")

    def notify_new_signals(self, results: list[AnalysisResult]) -> None:
        for result in results:
            if result.signal_created and result.signal_id is not None:
                self._send_signal(self.repository.get_signal(result.signal_id))

    def _send_signal(self, signal: dict) -> None:
        approval_id, token = self.trading_service.create_review_approval(signal["signal_id"])
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                "👀 실시간 시세로 매수 검토",
                callback_data=f"rv|{approval_id}|{token}",
            )
        )
        markup.add(InlineKeyboardButton("❌ 무시 / 취소", callback_data="cancel|signal"))
        self._send(
            f"🌟 <b>[{signal['symbol']} 매수 신호 포착]</b>\n\n"
            f"<code>├ JDSS 점수 : {signal['score']:>3}점 · {_grade_label(signal['grade'])}</code>\n"
            f"<code>├ 시장 국면 : {_regime_label(signal['regime'])}</code>\n"
            f"<code>├ 투자 금액 : {_money(signal['planned_budget']):>11}</code>\n"
            f"<code>├ 신호 가격 : {_money(signal['signal_close']):>11}</code>\n"
            f"<code>└ 추격 상한 : {_money(signal['max_chase_price']):>11}</code>\n\n"
            "💡 아래 버튼을 누르면 실시간 시세로 한 번 더 확인합니다.",
            markup=markup,
        )

    def _run_backtest_and_send(self, request: TelegramBacktestRequest) -> None:
        try:
            start = request.start.isoformat()
            end = request.end.isoformat()
            warmup_start = (request.start - timedelta(days=400)).isoformat()
            spy = self.data_source.daily("SPY", warmup_start, end)
            qqq = self.data_source.daily("QQQ", warmup_start, end)
            
            # 🚀 섹터 가드용 데이터 (실거래와 동일하게 SOXX, SMH 등 벤치마크 데이터를 백테스트 엔진에 전달)
            sector_data = {}
            guard_config = self.config.market_regime.get("soxl_sector_guard", {})
            if guard_config.get("enabled", False):
                benchmarks = guard_config.get("benchmark_candidates", ["SOXX", "SMH"])
                for bench in benchmarks:
                    sector_data[bench] = self.data_source.daily(bench, warmup_start, end)
            
            engine = BacktestEngine(self.config)
            results = {}
            for symbol in request.symbols:
                target = self.data_source.daily(symbol, warmup_start, end)
                results[symbol] = engine.run(
                    symbol, 
                    target, 
                    spy, 
                    qqq, 
                    start=start, 
                    end=end,
                    sector_data=sector_data if sector_data else None
                )
            self._send_long(self._format_backtest_results(results))
        except Exception as exc:
            LOGGER.exception("Telegram 백테스트 실패")
            error_msg = str(exc)
            if "지표 계산 데이터 부족" in error_msg:
                self._send(
                    f"⚠️ <b>[데이터 부족]</b>\n\n"
                    f"해당 종목은 상장 기간이 짧거나 주가 데이터가 부족(최소 60거래일 필요)하여 "
                    f"백테스트를 수행할 수 없습니다.\n"
                    f"<code>({html.escape(error_msg)})</code>"
                )
            else:
                self._send(f"❌ 백테스트를 끝내지 못했어요.\n{html.escape(error_msg)}")
        finally:
            self._backtest_lock.release()

    @staticmethod
    def _format_trade_timeline(result: BacktestResult, limit: int = 20) -> list[str]:
        events: list[tuple[str, int, str]] = []
        skipped_labels = {
            "SKIPPED_BY_CHASE_RULE": "추격상한초과",
            "STAGE_PRICE_RECOVERED": "가격회복미체결",
            "SLIPPAGE_EXCEEDS_PRICE_CEILING": "허용가초과",
            "ZERO_QUANTITY": "수량부족",
            "EXPOSURE_BLOCK": "한도초과",
        }
        for skipped in result.skipped_signals:
            reason = skipped_labels.get(str(skipped.get("reason")), "미체결")
            d = str(skipped["execution_date"]).replace("-", "")[2:]
            score_prefix = f"{skipped.get('score', 0)}점|" if "score" in skipped else ""
            details = f"[{score_prefix}{reason}]"
            events.append(
                (
                    str(skipped["execution_date"]),
                    1,
                    f"<code>⚪[{d}][매수미체결]{details}</code>",
                )
            )
        buy_counter: dict[str, int] = {}
        for trade in result.trades:
            purpose = str(trade["purpose"])
            cycle_id = str(trade.get("cycle_id", "default"))
            d = str(trade["date"]).replace("-", "")[2:]
            qty = trade["quantity"]
            price = Decimal(str(trade["price"]))
            price_str = _money(price)
            qty_str = f"{_quantity(qty)}주"

            if trade["side"] == "BUY":
                score_val = trade.get("score", 0)
                details = f"[{score_val}점|{qty_str}|{price_str}]"
                if purpose == "FIRST_ENTRY_CANDIDATE":
                    buy_counter[cycle_id] = 1
                    label = "1차매수"
                    icon = "🟢"
                elif purpose == "REBUY_CANDIDATE":
                    label = "재매수"
                    icon = "🟢"
                else:  # ADD_ENTRY_CANDIDATE
                    count = buy_counter.get(cycle_id, 1) + 1
                    buy_counter[cycle_id] = count
                    label = f"{count}차매수"
                    icon = {2: "🟡", 3: "🟠", 4: "🔴"}.get(count, "🔴")
            else:  # SELL
                details = f"[{qty_str}|{price_str}]"
                if purpose == "TP1":
                    label = "1차익절"
                    icon = "⚫"
                elif purpose == "TP2":
                    label = "2차완청"
                    icon = "⚫"
                else:
                    label = "매도"
                    icon = "⚫"
            events.append(
                (
                    str(trade["date"]),
                    1,
                    f"<code>{icon}[{d}][{label}]{details}</code>",
                )
            )
        events.sort(key=lambda item: (item[0], item[1]))
        omitted = max(0, len(events) - limit)
        selected = events[-limit:]
        lines = [event[2] for event in selected]
        if omitted:
            lines.insert(0, f"<code>…전체 {len(events)}건 중 최근 {limit}건 표시</code>")
        return lines

    def _format_backtest_results(self, results: dict[str, BacktestResult]) -> str:
        equity = pd.concat(
            [result.equity_curve.rename(symbol) for symbol, result in results.items()],
            axis=1,
            join="inner",
        ).sum(axis=1)
        initial = float(equity.iloc[0])
        final = float(equity.iloc[-1])
        elapsed_days = max(1, (equity.index[-1] - equity.index[0]).days)
        years = elapsed_days / 365.2425
        total_return = final / initial - 1
        cagr = (final / initial) ** (1 / years) - 1
        first_result = next(iter(results.values()))
        lines = [
            "☀️ <b>[JDSS 전략 백테스트 결과]</b>",
            "",
            f"<code>├ 시작일   : {first_result.start_date}</code>",
            f"<code>├ 종료일   : {first_result.end_date}</code>",
            f"<code>├ 초기자산 : {_money(initial):>12}</code>",
            f"<code>├ 최종자산 : {_money(final):>12}</code>",
            f"<code>├ 누적수익 : {total_return * 100:>+10.2f}%</code>",
            f"<code>├ 연평균   : {cagr * 100:>+10.2f}%</code>",
            f"<code>└ 최대낙폭 : {maximum_drawdown(equity) * 100:>10.2f}%</code>",
            "━━━━━━━━━━━━━━━━━━",
        ]
        for symbol, result in results.items():
            metrics = result.metrics
            lines.extend(
                [
                    f"✨ <b>[{symbol} 성과]</b>",
                    f"<code>├ 수익률 : {metrics['total_return_pct']:>+8.2f}%</code>",
                    f"<code>├ MDD    : {metrics['mdd_pct']:>8.2f}%</code>",
                    f"<code>├ 청산횟수 : {metrics['closed_cycles']:>8}회</code>",
                    f"<code>└ 승률    : {metrics['win_rate_pct']:>8.1f}%</code>",
                    "",
                ]
            )
            timeline = self._format_trade_timeline(result)
            lines.append(f"📜 <b>{symbol} 매매 내역</b>")
            if timeline:
                lines.extend(timeline)
            else:
                lines.append("조건에 도달한 매수 신호가 없었습니다.")
            lines.append("")
        lines.extend(
            [
                "━━━━━━━━━━━━━━━━━━",
                "💡 <i>종목당 $10,000 고정 한도 기준 (수수료 및 슬리피지 수치 반영)</i>",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _requested_symbol(text: str) -> str | None:
        parts = (text or "").split()
        return parts[1].upper() if len(parts) >= 2 else None

    def _scheduler_loop(self) -> None:
        while not self._stop.wait(self.config.scheduler.poll_interval_seconds):
            try:
                completed = self.market_clock.latest_completed_session(
                    delay_minutes=self.config.scheduler.signal_delay_minutes
                )
                last_analysis = self.repository.get_system_value("last_analysis_trade_date")
                if last_analysis != completed.isoformat():
                    results = self.analysis_service.analyze_all()
                    self.notify_new_signals(results)
                monitor_due = (
                    time.monotonic() - self._last_monitor
                    >= self.config.scheduler.order_monitor_interval_seconds
                )
                
                # 토스증권 08:50~09:00 KST 주간거래 전 주문 취소 및 리셋 시간대 예외 처리
                try:
                    import pytz
                    seoul_tz = pytz.timezone("Asia/Seoul")
                    now_kst = datetime.now(seoul_tz)
                    if now_kst.hour == 8 and 50 <= now_kst.minute <= 59:
                        monitor_due = False
                except ImportError:
                    pass

                if monitor_due:
                    for event in self.order_monitor.run_once():
                        self._send(f"ℹ️ {html.escape(event)}")
                    mismatches = self.reconciliation_service.run()
                    for symbol, issues in mismatches.items():
                        self._send(
                            f"🚨 <b>[{symbol} SAFE_MODE 경고]</b>\n"
                            + "\n".join(html.escape(issue) for issue in issues)
                        )
                    self._last_monitor = time.monotonic()
                self.repository.expire_stale_signals()
            except Exception as exc:
                LOGGER.exception("scheduler 실패")
                self.repository.log_event("WARNING", "SCHEDULER_ERROR", str(exc))

    def run(self) -> None:
        self.bot.set_my_commands(
            [
                telebot.types.BotCommand("dashboard", "☀️ 통합 대시보드"),
                telebot.types.BotCommand("account", "💰 토스 계좌 잔고"),
                telebot.types.BotCommand("status", "✨ 종목별 포지션 상세"),
                telebot.types.BotCommand("score", "🎯 JDSS 지표 분석"),
                telebot.types.BotCommand("signal", "🚨 활성 매수 신호"),
                telebot.types.BotCommand("backtest", "🌞 백테스트 실행"),
                telebot.types.BotCommand("guide", "📖 JDSS 용어 및 지표 설명서"),
                telebot.types.BotCommand("order", "🌟 미체결 주문 현황"),
                telebot.types.BotCommand("ping", "🏓 봇 상태 확인"),
                telebot.types.BotCommand("help", "🤖 메뉴 안내"),
            ]
        )
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        LOGGER.info("JDSS Telegram polling 시작")
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
