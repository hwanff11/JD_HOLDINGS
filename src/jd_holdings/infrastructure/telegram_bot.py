# Telegram HTML templates are intentionally kept as complete message lines.
# ruff: noqa: E501

from __future__ import annotations

import html
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
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
        elif requested in enabled_symbols:
            selected = (requested,)
        else:
            raise BacktestCommandError("지원 종목은 ALL, " + ", ".join(enabled_symbols) + "입니다.")
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


def _quantity(value: object) -> str:
    return f"{Decimal(str(value)):,.2f}".rstrip("0").rstrip(".")


def _profit_loss(value: object) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    amount = value.get("amountAfterCost", value.get("amount"))
    rate = value.get("rateAfterCost", value.get("rate"))
    if amount is None or rate is None:
        return None
    return _money(amount), f"{Decimal(str(rate)):+.2f}%"


def _regime_label(value: str) -> str:
    return {
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
        "EMPTY": "💤 관망 중",
        "WATCH": "👀 신호 감시 중",
        "CYCLING": "🔄 분할매수 진행 중",
        "HOLDING": "📦 보유 중",
        "SAFE_MODE": "🚨 안전 점검 필요",
    }.get(value, value.replace("_", " "))


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
        markup.add(
            *[
                InlineKeyboardButton(f"📊 {symbol} 상세", callback_data=f"detail|{symbol}")
                for symbol in self.config.enabled_symbols
            ]
        )
        markup.add(InlineKeyboardButton("🏦 실제 계좌", callback_data="account|view"))
        return markup

    @staticmethod
    def _format_score_message(result: AnalysisResult) -> str:
        snapshot = result.snapshot
        score = result.score
        return (
            f"📊 <b>{result.symbol} 상세 지표</b>\n\n"
            f"🎯 JDSS: <b>{score.total}점</b> · {_grade_label(score.grade.value)}\n"
            f"🌎 시장: {_regime_label(score.regime.value)}\n"
            f"🧭 판단: <b>{_action_label(result.decision.action.value)}</b>\n\n"
            f"CCI(5/10)  {snapshot.cci5:.2f} / {snapshot.cci10:.2f}\n"
            f"RSI(5/14)  {snapshot.rsi5:.2f} / {snapshot.rsi14:.2f}\n"
            f"ATR  {snapshot.atr_pct * 100:.2f}%\n"
            f"거래량  {snapshot.volume_ratio:.2f}배\n\n"
            "💡 지표는 참고용이며, 주문은 별도 승인 후에만 진행됩니다."
        )

    def _send_account(self) -> None:
        if self.account_client is None:
            self._send("🔐 토스 계좌 연결 정보를 확인해 주세요.")
            return
        try:
            buying_power = self.account_client.get_buying_power()
            holdings = [item for item in self.account_client.get_holdings() if _is_us_holding(item)]
            lines = [
                "🏦 <b>JH홀딩스 미국주식 계좌</b>",
                "",
                f"💵 주문가능금액  <b>{_money(buying_power)}</b>",
                f"📦 보유종목  <b>{len(holdings)}개</b>",
            ]
            if holdings:
                lines.append("\n━━━━━━━━━━")
            for item in holdings:
                symbol = str(item.get("symbol") or item.get("stockCode") or "-").upper()
                name = html.escape(str(item.get("name") or ""))
                quantity = item.get("quantity") or item.get("holdingQuantity") or "0"
                average = item.get("averagePrice") or item.get("averagePurchasePrice")
                current = item.get("currentPrice") or item.get("lastPrice")
                detail = f"🔹 <b>{html.escape(symbol)}</b>  {_quantity(quantity)}주"
                if name:
                    detail += f" · {name}"
                if average is not None:
                    detail += f"\n   평단 {_money(average)}"
                if current is not None:
                    detail += f" │ 현재가 {_money(current)}"
                profit = _profit_loss(item.get("profitLoss"))
                if profit:
                    detail += f"\n   평가손익 {profit[0]} ({profit[1]})"
                lines.append(detail)
            if not holdings:
                lines.append("현재 보유 중인 미국주식 종목이 없습니다.")

            lines.extend(
                [
                    "━━━━━━━━━━━━━━━━━━",
                    "💡 <i>안전을 위해 조회 전용 모드로 작동하고 있습니다.</i>",
                ]
            )
            self._send("\n".join(lines))
        except Exception as exc:
            LOGGER.exception("계좌 조회 실패")
            self._send(f"❌ 토스 계좌 조회 중 오류 발생:\n<code>{html.escape(str(exc))}</code>")

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
                "🧪 모의투자 (dry_run)"
                if self.settings.trading_mode == "dry_run"
                else "🔥 실거래 (live)"
            )
            self._send(
                "🤖 <b>[JH홀딩스 JDSS 봇 상태 점검]</b>\n\n"
                f"• <b>프로그램 버전</b>: v{__version__}\n"
                f"• <b>운영 모드</b>: {mode_label}\n"
                f"• <b>실주문 잠금</b>: {lock_text}\n\n"
                "✅ <i>JDSS 매매 시스템이 정상 가동 중입니다.</i>"
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
                    "🤖 <b>[JH홀딩스 통합 대시보드]</b>",
                    "",
                ]
                if results:
                    lines.append(
                        f"🌐 <b>시장 국면</b>: {_regime_label(results[0].score.regime.value)}"
                    )
                    lines.append(f"📅 <b>분석 기준일</b>: {results[0].trade_date.isoformat()}")
                lines.append(f"⚙️ <b>운영 모드</b>: {mode_label}")
                lines.append("━━━━━━━━━━━━━━━━━━")

                for result in results:
                    position = self.repository.get_position(result.symbol)
                    lines.extend(
                        [
                            f"🔹 <b>{result.symbol}</b>",
                            f"• <b>상태</b>: {_state_label(position.state.value)}",
                            f"• <b>JDSS 점수</b>: <b>{result.score.total}점</b> ({_grade_label(result.score.grade.value)})",
                            f"• <b>보유 잔고</b>: {_quantity(position.quantity)}주 │ 평단 {_money(position.average_price)}",
                            f"• <b>누적 매수금</b>: {_money(position.staged_entry_capital)} (한도 {_money(position.cycle_exposure_cap)})",
                            f"• <b>전략 판단</b>: <b>{_action_label(result.decision.action.value)}</b>",
                            "",
                        ]
                    )
                lines.extend(
                    [
                        "━━━━━━━━━━━━━━━━━━",
                        "💡 <i>/status [종목] 명령어로 세부 포지션을 조회하실 수 있습니다.</i>",
                    ]
                )
                markup = InlineKeyboardMarkup()
                markup.add(
                    InlineKeyboardButton(
                        "🏦 실제 토스 계좌 조회",
                        callback_data="account|view",
                    )
                )
                self._send("\n".join(lines), markup=markup)
                self.notify_new_signals(results)
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
                    s = result.snapshot
                    score_result = result.score
                    text = (
                        f"🎯 <b>[{result.symbol} JDSS 지표 분석]</b>\n\n"
                        f"📊 <b>총점</b>: <b>{score_result.total} / 100점</b> ({_grade_label(score_result.grade.value)})\n"
                        f"🌐 <b>시장 국면</b>: {_regime_label(score_result.regime.value)} ({score_result.regime_score}/25)\n\n"
                        f"<code>├ 과매도 점수 : {score_result.oversold_score:2d} / 40</code>\n"
                        f"<code>├ 반등 점수   : {score_result.reversal_score:2d} / 20</code>\n"
                        f"<code>├ 거래량 점수 : {score_result.volume_score:2d} / 10</code>\n"
                        f"<code>└ ATR 변동성  : {score_result.atr_score:2d} /  5</code>\n\n"
                        f"📈 <b>보조 지표 현황</b>\n"
                        f"<code>├ CCI (5/10)  : {s.cci5:6.1f} / {s.cci10:6.1f}</code>\n"
                        f"<code>├ RSI (5/14)  : {s.rsi5:6.1f} / {s.rsi14:6.1f}</code>\n"
                        f"<code>├ ATR 비율    : {s.atr_pct * 100:6.2f}%</code>\n"
                        f"<code>├ 거래량 비율 : {s.volume_ratio:6.2f}배</code>\n"
                        f"<code>└ 종가 위치   : {s.close_position:6.2f}</code>\n\n"
                        f"💡 <b>최종 전략 판단</b>: <b>{_action_label(result.decision.action.value)}</b>"
                    )
                    self._send(text)
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
                position = self.repository.get_position(symbol)
                plan = self.repository.active_tp_plan(symbol)
                lines = [
                    f"📦 <b>[{symbol} 상세 포지션 현황]</b>",
                    "",
                    f"• <b>상태</b>: {_state_label(position.state.value)}",
                    f"• <b>보유 수량</b>: <b>{_quantity(position.quantity)}주</b>",
                    f"• <b>평균 단가</b>: <b>{_money(position.average_price)}</b> (원가 {_money(position.current_cost_basis)})",
                    f"• <b>누적 매수금</b>: {_money(position.staged_entry_capital)} (한도 {_money(position.cycle_exposure_cap)})",
                    f"• <b>1차 진입가</b>: {_money(position.anchor_price)} (추가 진입 {position.rebuy_count}회)",
                ]
                if plan:
                    lines.extend(
                        [
                            "",
                            "🎯 <b>목표가 (Take-Profit)</b>",
                            f"<code>├ 1차 익절 (TP1) : {_money(Decimal(plan['tp1_price']))} × {plan['tp1_target_qty']}주</code>",
                            f"<code>└ 2차 익절 (TP2) : {_money(Decimal(plan['tp2_price']))} × {plan['tp2_target_qty']}주</code>",
                        ]
                    )
                self._send("\n".join(lines))

        @bot.message_handler(commands=["order", "o"])
        def orders(message):
            if not self._authorized_message(message):
                return
            values = self.repository.open_orders()
            if not values:
                self._send("📋 <b>현재 대기 중인 미체결 주문이 없습니다.</b>")
                return
            lines = ["📋 <b>[JDSS 미체결 주문 목록]</b>", ""]
            for item in values:
                price_str = _money(item["price"]) if item["price"] else "시장가"
                lines.append(
                    f"• <b>{item['symbol']}</b> {item['side']} {item['purpose']}\n"
                    f"  └ {_quantity(item['qty'])}주 @ {price_str} ({item['status']})"
                )
            self._send("\n".join(lines))

        @bot.message_handler(commands=["errors", "err"])
        def errors(message):
            if not self._authorized_message(message):
                return
            events = self.repository.recent_events(10)
            if not events:
                self._send("🧾 <b>최근 기록된 이벤트가 없습니다.</b>")
                return
            lines = ["🧾 <b>[최근 JDSS 시스템 이벤트]</b>", ""]
            for event in events:
                lines.append(
                    f"<code>[{event['severity']}] {event['created_at'][11:19]}</code>\n"
                    f"<b>{html.escape(event['event_type'])}</b>: {html.escape(event['message'])}\n"
                )
            self._send("\n".join(lines))

        @bot.message_handler(commands=["backtest", "bt"])
        def backtest(message):
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
                f"🧪 <b>[{symbols_text} 백테스트 시뮬레이션 가동]</b>\n"
                f"🗓️ <b>기간</b>: {request.start} ~ {request.end}\n\n"
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
                "🤖 <b>[JH홀딩스 JDSS 매매 봇 도움말]</b>\n\n"
                "📊 <b>/dashboard</b> (<code>/d</code>) — 통합 대시보드\n"
                "🎯 <b>/score</b> (<code>/sc</code>) [종목] — JDSS 세부 지표 분석\n"
                "🚨 <b>/signal</b> (<code>/sg</code>) — 활성 매수 신호 조회\n"
                "📦 <b>/status</b> (<code>/st</code>) [종목] — 상세 포지션 현황\n"
                "🏦 <b>/account</b> (<code>/acct</code>) — 토스증권 실제 계좌 잔고\n"
                "🧪 <b>/backtest</b> — SOXL 최근 300거래일\n"
                "   <code>/bt TQQQ 100</code> — 종목·거래일 지정\n"
                "📋 <b>/order</b> (<code>/o</code>) — 미체결 주문 현황\n"
                "🧾 <b>/errors</b> (<code>/err</code>) — 최근 시스템 로그\n"
                "🏓 <b>/ping</b> (<code>/p</code>) — 봇 상태 및 시스템 확인\n\n"
                "💡 <i>모든 매수는 2단계 안전 승인을 거쳐 실행됩니다.</i>"
            )

        @bot.callback_query_handler(func=lambda call: call.data == "account|view")
        def account_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            self._send_account()
            bot.answer_callback_query(call.id, "토스 계좌 잔고를 불러왔습니다.")

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
                    f"🛒 <b>[{quote.symbol} 최종 매수 승인 확인]</b>\n\n"
                    f"• <b>실시간 현재가</b>: {_money(quote.current_price)}\n"
                    f"• <b>최종 지정가</b>: <b>{_money(quote.limit_price)}</b> (상한 {_money(quote.execution_ceiling)})\n"
                    f"• <b>주문 수량</b>: <b>{_quantity(quote.quantity)}주</b>\n"
                    f"• <b>예상 투자금</b>: <b>{_money(quote.planned_budget)}</b> (수수료 약 {_money(quote.estimated_fee)})\n\n"
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
                    f"🛒 <b>[{mode_text} 전송 완료!]</b> 🚀\n\n"
                    f"• <b>주문번호</b>: <code>{html.escape(receipt.broker_order_id)}</code>\n"
                    f"• <b>처리 상태</b>: {html.escape(receipt.status)}\n"
                    f"• <b>체결 수량</b>: {_quantity(receipt.filled_quantity)}주 / {_quantity(receipt.quantity)}주"
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
            f"🚨 <b>{signal['symbol']} 매수 신호가 왔어요!</b>\n\n"
            f"🎯 JDSS  <b>{signal['score']}점</b> · {_grade_label(signal['grade'])}\n"
            f"🌎 시장  {_regime_label(signal['regime'])}\n"
            f"💵 예정 투자금  <b>{_money(signal['planned_budget'])}</b>\n"
            f"📊 기준가  {_money(signal['signal_close'])} "
            f"(상한 {_money(signal['max_chase_price'])})\n\n"
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
            engine = BacktestEngine(self.config)
            results = {}
            for symbol in request.symbols:
                target = self.data_source.daily(symbol, warmup_start, end)
                results[symbol] = engine.run(symbol, target, spy, qqq, start=start, end=end)
            self._send_long(self._format_backtest_results(results))
        except Exception as exc:
            LOGGER.exception("Telegram 백테스트 실패")
            self._send(f"❌ 백테스트를 끝내지 못했어요.\n{html.escape(str(exc))}")
        finally:
            self._backtest_lock.release()

    @staticmethod
    def _format_trade_timeline(result: BacktestResult, limit: int = 15) -> list[str]:
        events: list[tuple[str, int, str]] = []
        action_labels = {
            "FIRST_ENTRY_CANDIDATE": "1차 매수 신호",
            "ADD_ENTRY_CANDIDATE": "추가 매수 신호",
            "REBUY_CANDIDATE": "재매수 신호",
        }
        buy_labels = {
            "FIRST_ENTRY_CANDIDATE": "1차 매수",
            "ADD_ENTRY_CANDIDATE": "추가 매수",
            "REBUY_CANDIDATE": "재매수",
        }
        for signal in result.signals:
            action = action_labels.get(str(signal["action"]), "매수 신호")
            events.append(
                (
                    str(signal["trade_date"]),
                    0,
                    f"📣 {signal['trade_date']}  {action} "
                    f"· {signal['score']}점 · {_money(signal['signal_close'])}",
                )
            )
        skipped_labels = {
            "SKIPPED_BY_CHASE_RULE": "추격매수 상한 초과",
            "STAGE_PRICE_RECOVERED": "추가매수 가격 회복",
            "SLIPPAGE_EXCEEDS_PRICE_CEILING": "허용 매수가 초과",
            "ZERO_QUANTITY": "매수 가능 수량 부족",
            "EXPOSURE_BLOCK": "투자 한도 초과",
        }
        for skipped in result.skipped_signals:
            reason = skipped_labels.get(str(skipped.get("reason")), "매수 조건 미충족")
            events.append(
                (
                    str(skipped["execution_date"]),
                    1,
                    f"⚪ {skipped['execution_date']}  매수 미체결 · {reason}",
                )
            )
        for trade in result.trades:
            purpose = str(trade["purpose"])
            if trade["side"] == "BUY":
                label = buy_labels.get(purpose, "매수")
                icon = "🟢"
            else:
                label = {"TP1": "1차 매도", "TP2": "2차 매도"}.get(purpose, "매도")
                icon = "🟠" if purpose == "TP1" else "🔵"
            events.append(
                (
                    str(trade["date"]),
                    1,
                    f"{icon} {trade['date']}  {label} · "
                    f"{_quantity(trade['quantity'])}주 @ {_money(trade['price'])}",
                )
            )
        events.sort(key=lambda item: (item[0], item[1]))
        omitted = max(0, len(events) - limit)
        selected = events[-limit:]
        lines = [event[2] for event in selected]
        if omitted:
            lines.insert(0, f"… 전체 {len(events)}건 중 최근 {limit}건만 표시합니다.")
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
            "🧪 <b>[JDSS 전략 백테스트 결과]</b>",
            "",
            f"🗓️ <b>대상 기간</b>: {first_result.start_date} ~ {first_result.end_date} ({years:.1f}년)",
            f"💵 <b>초기 자산</b>: <b>{_money(initial)}</b>",
            f"📈 <b>최종 자산</b>: <b>{_money(final)}</b>",
            f"💰 <b>누적 수익률</b>: <code>{total_return * 100:+.2f}%</code>",
            f"📊 <b>연평균 (CAGR)</b>: <code>{cagr * 100:+.2f}%</code>",
            f"📉 <b>최대 낙폭 (MDD)</b>: <code>{maximum_drawdown(equity) * 100:.2f}%</code>",
            "━━━━━━━━━━━━━━━━━━",
        ]
        for symbol, result in results.items():
            metrics = result.metrics
            lines.extend(
                [
                    f"🔹 <b>{symbol} 성과 요약</b>",
                    f"• 수익률: <code>{metrics['total_return_pct']:+.2f}%</code> │ MDD: <code>{metrics['mdd_pct']:.2f}%</code>",
                    f"• 청산 거래: {metrics['closed_cycles']}회 │ 승률: <b>{metrics['win_rate_pct']:.1f}%</b>",
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
                telebot.types.BotCommand("dashboard", "📊 통합 대시보드"),
                telebot.types.BotCommand("score", "🎯 JDSS 지표 분석"),
                telebot.types.BotCommand("signal", "🚨 활성 매수 신호"),
                telebot.types.BotCommand("status", "📦 종목별 포지션 상세"),
                telebot.types.BotCommand("account", "🏦 토스 계좌 잔고"),
                telebot.types.BotCommand("backtest", "🧪 백테스트 실행"),
                telebot.types.BotCommand("order", "📋 미체결 주문 현황"),
                telebot.types.BotCommand("ping", "🏓 봇 상태 확인"),
                telebot.types.BotCommand("help", "🤖 메뉴 안내"),
            ]
        )
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        LOGGER.info("JDSS Telegram polling 시작")
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
