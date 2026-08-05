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
    if parts and not _looks_like_iso_date(parts[0]):
        requested = parts.pop(0).upper()
        if requested == "ALL":
            selected = enabled_symbols
        elif requested in enabled_symbols:
            selected = (requested,)
        else:
            raise BacktestCommandError(
                "지원 종목은 ALL, " + ", ".join(enabled_symbols) + "입니다."
            )
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
        return TelegramBacktestRequest(
            symbols=selected, start=start, end=latest_completed
        )
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

    def _dashboard_markup(self) -> InlineKeyboardMarkup:
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            *[
                InlineKeyboardButton(
                    f"📊 {symbol} 상세", callback_data=f"detail|{symbol}"
                )
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
            holdings = [
                item for item in self.account_client.get_holdings() if _is_us_holding(item)
            ]
            lines = [
                "🏦 <b>JH홀딩스 미국주식 계좌</b>",
                "",
                f"💵 주문가능금액  <b>{_money(buying_power)}</b>",
                f"📦 보유종목  <b>{len(holdings)}개</b>",
            ]
            if holdings:
                lines.append("\n━━━━━━━━━━")
            for item in holdings:
                symbol = html.escape(
                    str(item.get("symbol") or item.get("stockCode") or "-").upper()
                )
                name = html.escape(str(item.get("name") or ""))
                quantity = item.get("quantity") or item.get("holdingQuantity") or "0"
                average = item.get("averagePrice") or item.get("averagePurchasePrice") or 0
                current = item.get("currentPrice") or item.get("lastPrice") or 0
                profit = item.get("profitLoss")
                lines.extend(
                    [
                        f"\n<b>{symbol}</b>{f' · {name}' if name else ''}",
                        f"{_quantity(quantity)}주  ·  평단 {_money(average)}  ·  "
                        f"현재 {_money(current)}",
                    ]
                )
                if profit is not None:
                    lines.append(f"평가손익  {_money(profit)}")
            if not holdings:
                lines.append("\n현재 보유 중인 미국주식이 없습니다.")
            lines.append("\n🔒 계좌 조회만 했어요. 실제 주문은 잠겨 있습니다.")
            self._send("\n".join(lines))
        except Exception as exc:
            LOGGER.exception("계좌 조회 실패")
            self._send(f"❌ 계좌를 불러오지 못했습니다.\n{html.escape(str(exc))}")

    def _register_handlers(self) -> None:
        bot = self.bot

        @bot.message_handler(commands=["ping", "p"])
        def ping(message):
            if not self._authorized_message(message):
                return
            lock = "🟢 사용 가능" if self.settings.live_trading_enabled else "🔒 잠금"
            mode = "실전 투자" if self.settings.live_trading_enabled else "모의 투자"
            self._send(
                f"🤖 <b>JH홀딩스 봇, 정상 작동 중입니다!</b>\n\n"
                f"⚙️ {mode}  ·  실주문 {lock}\n"
                f"버전 {__version__}"
            )

        @bot.message_handler(commands=["dashboard", "d"])
        def dashboard(message):
            if not self._authorized_message(message):
                return
            try:
                results = self.analysis_service.analyze_all()
                lines = ["🤖 <b>JH홀딩스 통합 대시보드</b>"]
                if results:
                    lines.append(f"\n🌎 시장  {_regime_label(results[0].score.regime.value)}")
                mode = "🟢 실전" if self.settings.live_trading_enabled else "🧪 모의투자"
                lines.append(f"⚙️ 운영  {mode}")
                lines.append("\n━━━━━━━━━━")
                for result in results:
                    position = self.repository.get_position(result.symbol)
                    lines.extend(
                        [
                            "",
                            f"🔹 <b>{result.symbol}</b>  ·  {result.score.total}점 "
                            f"({_grade_label(result.score.grade.value)})",
                            f"{_state_label(position.state.value)}  ·  "
                            f"{_action_label(result.decision.action.value)}",
                            f"보유 {_quantity(position.quantity)}주  ·  "
                            f"평단 {_money(position.average_price)}",
                        ]
                    )
                lines.append("\n💡 종목별 상세 지표는 아래 버튼에서 확인하세요.")
                self._send("\n".join(lines), markup=self._dashboard_markup())
                self.notify_new_signals(results)
            except Exception as exc:
                LOGGER.exception("dashboard 실패")
                self._send(f"❌ 대시보드 생성 실패: {html.escape(str(exc))}")

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
                self._send(f"❌ 점수 계산 실패: {html.escape(str(exc))}")

        @bot.message_handler(commands=["signal", "sg"])
        def signal(message):
            if not self._authorized_message(message):
                return
            signals = self.repository.active_signals()
            if not signals:
                self._send(
                    "💤 <b>지금은 매수 신호가 없어요.</b>\n"
                    "좋은 타점이 오면 바로 알려드릴게요! 😊"
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
                    f"📦 <b>{symbol} 투자 현황</b>",
                    f"\n{_state_label(position.state.value)}",
                    f"보유  <b>{_quantity(position.quantity)}주</b>",
                    f"평단  <b>{_money(position.average_price)}</b>",
                    f"투입금  {_money(position.current_cost_basis)}",
                ]
                if plan:
                    lines.extend(
                        [
                            "\n🎯 <b>익절 계획</b>",
                            f"1차  {_money(plan['tp1_price'])} · "
                            f"{_quantity(plan['tp1_target_qty'])}주",
                            f"2차  {_money(plan['tp2_price'])} · "
                            f"{_quantity(plan['tp2_target_qty'])}주",
                        ]
                    )
                self._send("\n".join(lines))

        @bot.message_handler(commands=["order", "o"])
        def orders(message):
            if not self._authorized_message(message):
                return
            values = self.repository.open_orders()
            if not values:
                self._send("✅ <b>현재 기다리는 주문이 없어요.</b>")
                return
            lines = ["📋 <b>미체결 주문</b>"]
            for item in values:
                lines.append(
                    f"{item['symbol']} · {'매수' if item['side'] == 'BUY' else '매도'} "
                    f"{_quantity(item['qty'])}주 · "
                    f"{_money(item['price']) if item['price'] else '시장가'}"
                )
            self._send("\n".join(lines))

        @bot.message_handler(commands=["errors", "err"])
        def errors(message):
            if not self._authorized_message(message):
                return
            events = self.repository.recent_events(10)
            if not events:
                self._send("기록된 JDSS 이벤트가 없습니다.")
                return
            lines = ["🧾 <b>최근 JDSS 이벤트</b>"]
            for event in events:
                lines.append(
                    f"{event['created_at'][:19]} [{event['severity']}] "
                    f"{html.escape(event['event_type'])}: {html.escape(event['message'])}"
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
                    f"⚠️ {html.escape(str(exc))}\n\n"
                    "예시\n"
                    "/bt\n"
                    "/bt TQQQ 100  (최근 100거래일)\n"
                    "/bt ALL 250\n"
                    "/bt TQQQ 2021-01-01 2024-12-31"
                )
                return
            if not self._backtest_lock.acquire(blocking=False):
                self._send("⏳ 다른 백테스트가 실행 중입니다. 완료 알림 후 다시 요청해 주세요.")
                return
            symbols_text = "+".join(request.symbols)
            self._send(
                f"🧪 <b>{symbols_text} 백테스트를 시작할게요!</b>\n"
                f"{request.start} ~ {request.end}\n\n"
                "계산이 끝나면 결과를 바로 알려드립니다. 😊"
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
                "🤖 <b>JH홀딩스 봇 메뉴</b>\n\n"
                "📊 /d  통합 대시보드\n"
                "🏦 /account  미국주식 계좌\n"
                "💡 /signal  현재 매수 신호\n"
                "📦 /st TQQQ  종목 투자 현황\n"
                "🧪 /bt TQQQ 100  최근 100거래일 백테스트\n"
                "🧪 /bt ALL 250  전체 종목 250거래일\n\n"
                "🔒 매수는 항상 2단계 승인 후에만 진행됩니다."
            )

        @bot.callback_query_handler(func=lambda call: call.data.startswith("detail|"))
        def detail_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            symbol = call.data.split("|", 1)[1]
            try:
                result = next(
                    item
                    for item in self.analysis_service.analyze_all()
                    if item.symbol == symbol
                )
                self._send(self._format_score_message(result))
                bot.answer_callback_query(call.id, f"{symbol} 상세 지표입니다.")
            except Exception as exc:
                LOGGER.exception("상세 지표 조회 실패")
                bot.answer_callback_query(call.id, str(exc), show_alert=True)

        @bot.callback_query_handler(func=lambda call: call.data == "account|view")
        def account_callback(call):
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            self._send_account()
            bot.answer_callback_query(call.id, "미국주식 계좌를 불러왔습니다.")

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
                        "✅ 이 조건으로 매수",
                        callback_data=(f"ex|{quote.execution_approval_id}|{quote.execution_token}"),
                    )
                )
                markup.add(InlineKeyboardButton("❌ 취소", callback_data="cancel|review"))
                self._send(
                    f"🛒 <b>{quote.symbol} 매수 최종 확인</b>\n\n"
                    f"현재가  {_money(quote.current_price)}\n"
                    f"매수가  <b>{_money(quote.limit_price)}</b>\n"
                    f"수량  <b>{_quantity(quote.quantity)}주</b>\n"
                    f"예상 투자금  {_money(quote.planned_budget)}\n\n"
                    f"💡 {self.config.global_.execution_token_ttl_seconds}초 안에 "
                    "최종 승인해 주세요.",
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
                    f"✅ <b>{mode_text} 접수 완료!</b>\n\n"
                    f"{_quantity(receipt.quantity)}주 중 "
                    f"{_quantity(receipt.filled_quantity)}주가 체결됐어요.\n"
                    f"주문번호  {html.escape(receipt.broker_order_id)}"
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
                self._send("❌ <b>매수 검토를 취소했어요.</b>\n다음 좋은 타점을 기다릴게요.")
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
                "👀 실시간 시세로 검토",
                callback_data=f"rv|{approval_id}|{token}",
            )
        )
        markup.add(InlineKeyboardButton("다음에 볼게요", callback_data="cancel|signal"))
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
            spy = self.data_source.daily("SPY", start, end)
            qqq = self.data_source.daily("QQQ", start, end)
            engine = BacktestEngine(self.config)
            results = {}
            for symbol in request.symbols:
                target = self.data_source.daily(symbol, start, end)
                results[symbol] = engine.run(
                    symbol, target, spy, qqq, start=start, end=end
                )
            self._send(self._format_backtest_results(results))
        except Exception as exc:
            LOGGER.exception("Telegram 백테스트 실패")
            self._send(f"❌ 백테스트를 끝내지 못했어요.\n{html.escape(str(exc))}")
        finally:
            self._backtest_lock.release()

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
            "🏁 <b>백테스트가 끝났어요!</b>",
            f"{' + '.join(results)}  ·  {first_result.start_date} ~ {first_result.end_date}",
            "",
            f"💰 총수익률  <b>{total_return * 100:+.2f}%</b>",
            f"📈 연평균  <b>{cagr * 100:+.2f}%</b>",
            f"📉 최대낙폭  <b>{maximum_drawdown(equity) * 100:.2f}%</b>",
            f"🏦 최종자산  <b>${final:,.2f}</b>",
            "\n━━━━━━━━━━",
        ]
        for symbol, result in results.items():
            metrics = result.metrics
            lines.extend(
                [
                    "",
                    f"🔹 <b>{symbol}</b>  수익 {metrics['total_return_pct']:+.2f}% "
                    f"· 낙폭 {metrics['mdd_pct']:.2f}%",
                    f"거래 {metrics['closed_cycles']}회  ·  승률 {metrics['win_rate_pct']:.2f}%",
                ]
            )
        lines.extend(
            [
                "",
                "💡 종목당 $10,000 고정 · 수익 재투자 없음",
                "수수료와 슬리피지를 모두 반영한 결과입니다.",
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
                            f"🚨 <b>{symbol} SAFE_MODE</b>\n"
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
                telebot.types.BotCommand("account", "🏦 미국주식 계좌"),
                telebot.types.BotCommand("signal", "💡 현재 매수 신호"),
                telebot.types.BotCommand("status", "📦 종목 투자 현황"),
                telebot.types.BotCommand("backtest", "🧪 최근 N거래일 백테스트"),
                telebot.types.BotCommand("help", "🤖 메뉴 안내"),
            ]
        )
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        LOGGER.info("JDSS Telegram polling 시작")
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
