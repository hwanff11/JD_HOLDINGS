from __future__ import annotations

import html
import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import date
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
from jd_holdings.backtest.performance import maximum_drawdown, risk_adjusted_metrics
from jd_holdings.config import StrategyConfig
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
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


def _format_number(value: float | int, digits: int = 2) -> str:
    if isinstance(value, float) and math.isinf(value):
        return "∞"
    return f"{float(value):.{digits}f}"


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

    def _register_handlers(self) -> None:
        bot = self.bot

        @bot.message_handler(commands=["ping", "p"])
        def ping(message):
            if not self._authorized_message(message):
                return
            lock = "해제" if self.settings.live_trading_enabled else "잠금"
            self._send(
                f"🏓 <b>JDSS 정상</b>\n버전: {__version__}\n"
                f"모드: {html.escape(self.settings.trading_mode)}\n실주문: {lock}"
            )

        @bot.message_handler(commands=["dashboard", "d"])
        def dashboard(message):
            if not self._authorized_message(message):
                return
            try:
                results = self.analysis_service.analyze_all()
                lines = ["📊 <b>JDSS 대시보드</b>"]
                if results:
                    lines.append(f"시장국면: <b>{results[0].score.regime.value}</b>")
                    lines.append(f"분석일: {results[0].trade_date.isoformat()}")
                lines.append(f"운영모드: {html.escape(self.settings.trading_mode)}")
                for result in results:
                    position = self.repository.get_position(result.symbol)
                    lines.extend(
                        [
                            "",
                            f"<b>{result.symbol}</b>",
                            f"상태: {position.state.value}",
                            f"JDSS: {result.score.total}점 / {result.score.grade.value}",
                            f"보유: {position.quantity}주 / 평단 ${position.average_price:.2f}",
                            f"사이클 허용액: ${position.cycle_exposure_cap:.2f}",
                            f"단계매수 누적액: ${position.staged_entry_capital:.2f}",
                            f"다음 판단: {result.decision.action.value}",
                        ]
                    )
                self._send("\n".join(lines))
                self.notify_new_signals(results)
            except Exception as exc:
                LOGGER.exception("dashboard 실패")
                self._send(f"❌ 대시보드 생성 실패: {html.escape(str(exc))}")

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
                        f"🎯 <b>{result.symbol} JDSS 점수</b>\n\n"
                        f"총점: <b>{score_result.total} / 100</b> ({score_result.grade.value})\n"
                        f"시장국면: {score_result.regime.value} ({score_result.regime_score}/25)\n"
                        f"과매도: {score_result.oversold_score}/40\n"
                        f"반등: {score_result.reversal_score}/20\n"
                        f"거래량: {score_result.volume_score}/10\n"
                        f"ATR: {score_result.atr_score}/5\n\n"
                        f"CCI5/10: {s.cci5:.2f} / {s.cci10:.2f}\n"
                        f"RSI5/14: {s.rsi5:.2f} / {s.rsi14:.2f}\n"
                        f"ATR%: {s.atr_pct * 100:.2f}%\n"
                        f"거래량 비율: {s.volume_ratio:.2f}배\n"
                        f"종가 위치: {s.close_position:.2f}\n\n"
                        f"결론: <b>{result.decision.action.value}</b>"
                    )
                    self._send(text)
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
                self._send("현재 실행 가능한 JDSS 매수신호가 없습니다.")
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
                    f"📦 <b>{symbol} 포지션</b>",
                    f"상태: {position.state.value}",
                    f"사이클: {html.escape(position.cycle_id or '-')}",
                    f"수량: {position.quantity}주",
                    f"평단: ${position.average_price:.4f}",
                    f"현재 원가: ${position.current_cost_basis:.2f}",
                    f"사이클 허용액: ${position.cycle_exposure_cap:.2f}",
                    f"단계매수액: ${position.staged_entry_capital:.2f}",
                    f"1차 기준가격: ${position.anchor_price:.4f}",
                    f"재매수 횟수: {position.rebuy_count}",
                ]
                if plan:
                    lines.extend(
                        [
                            f"TP1: ${Decimal(plan['tp1_price']):.2f} × {plan['tp1_target_qty']}주",
                            f"TP2: ${Decimal(plan['tp2_price']):.2f} × {plan['tp2_target_qty']}주",
                        ]
                    )
                self._send("\n".join(lines))

        @bot.message_handler(commands=["order", "o"])
        def orders(message):
            if not self._authorized_message(message):
                return
            values = self.repository.open_orders()
            if not values:
                self._send("현재 JDSS 미체결 주문이 없습니다.")
                return
            lines = ["📋 <b>JDSS 미체결 주문</b>"]
            for item in values:
                lines.append(
                    f"{item['symbol']} {item['purpose']} {item['side']} "
                    f"{item['qty']}주 @ ${html.escape(str(item['price'] or '시장가'))} "
                    f"({item['status']})"
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
                    "/bt ALL 2025-01-01\n"
                    "/bt TQQQ 2021-01-01 2024-12-31"
                )
                return
            if not self._backtest_lock.acquire(blocking=False):
                self._send("⏳ 다른 백테스트가 실행 중입니다. 완료 알림 후 다시 요청해 주세요.")
                return
            symbols_text = "+".join(request.symbols)
            self._send(
                f"🧪 <b>{symbols_text} 백테스트 시작</b>\n"
                f"기간: {request.start} ~ {request.end}\n"
                "조회 전용이며 실제 주문은 실행하지 않습니다."
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
                "<b>JDSS 명령어</b>\n"
                "/dashboard /d — 통합 대시보드\n"
                "/score /sc [종목] — JDSS 점수\n"
                "/signal /sg — 활성 매매신호\n"
                "/status /st [종목] — 포지션\n"
                "/indicator /i [종목] — 지표\n"
                "/backtest /bt [ALL|종목] [시작일] [종료일] — 백테스트\n"
                "/order /o — JDSS 주문\n"
                "/errors /err — 최근 이벤트\n"
                "/ping /p — 상태 확인\n\n"
                "모든 매수는 검토와 최종 실행의 2단계 승인이 필요합니다."
            )

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
                    f"📋 <b>{quote.symbol} 최종 주문 확인</b>\n\n"
                    f"주문 세션: {quote.session}\n"
                    f"실시간 현재가: ${quote.current_price:.4f}\n"
                    f"전략상 상한: ${quote.execution_ceiling:.4f}\n"
                    f"최종 지정가: <b>${quote.limit_price:.4f}</b>\n"
                    f"수량: <b>{quote.quantity}주</b>\n"
                    f"예상 수수료: ${quote.estimated_fee:.2f}\n"
                    f"계획예산: ${quote.planned_budget:.2f}\n\n"
                    "실행 승인은 "
                    f"{self.config.global_.execution_token_ttl_seconds}초 동안 유효합니다.",
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
                    f"✅ <b>{mode_text} 접수</b>\n"
                    f"주문번호: {html.escape(receipt.broker_order_id)}\n"
                    f"상태: {html.escape(receipt.status)}\n"
                    f"수량: {receipt.quantity}주 / 체결: {receipt.filled_quantity}주"
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
                bot.answer_callback_query(call.id, "취소했습니다.")

    def notify_new_signals(self, results: list[AnalysisResult]) -> None:
        for result in results:
            if result.signal_created and result.signal_id is not None:
                self._send_signal(self.repository.get_signal(result.signal_id))

    def _send_signal(self, signal: dict) -> None:
        approval_id, token = self.trading_service.create_review_approval(signal["signal_id"])
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ 매수 검토", callback_data=f"rv|{approval_id}|{token}"))
        markup.add(InlineKeyboardButton("❌ 무시", callback_data="cancel|signal"))
        detail = signal.get("score_detail")
        if detail is None and signal.get("score_detail_json"):
            import json

            detail = json.loads(signal["score_detail_json"])
        detail = detail or {}
        self._send(
            f"🎯 <b>JDSS 매수 후보 — {signal['symbol']}</b>\n\n"
            f"행동: {signal['action']}\n"
            f"JDSS: <b>{signal['score']}점 / {signal['grade']}</b>\n"
            f"시장국면: {signal['regime']}\n"
            f"반등점수: {detail.get('reversal_score', '-')} / 20\n"
            f"신호 종가: ${Decimal(signal['signal_close']):.4f}\n"
            f"추격매수 상한: ${Decimal(signal['max_chase_price']):.4f}\n"
            f"예정 투자금: ${Decimal(signal['planned_budget']):.2f}\n"
            f"유효시간: {signal['valid_until'][:19]} UTC",
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
            self._send(f"❌ 백테스트 실패: {html.escape(str(exc))}")
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
        sharpe, sortino = risk_adjusted_metrics(
            equity, self.config.backtest.annualization_days
        )
        first_result = next(iter(results.values()))
        lines = [
            "🧪 <b>JDSS v1.3 백테스트 완료</b>",
            f"기간: {first_result.start_date} ~ {first_result.end_date}",
            f"대상: {' + '.join(results)}",
            "모드: 조회 전용 / 실제 주문 없음",
            "",
            "<b>합산 포트폴리오</b>",
            f"초기자금: ${initial:,.2f}",
            f"최종자산: ${final:,.2f}",
            f"총수익률: {total_return * 100:+.2f}%",
            f"연복리수익률(CAGR): {cagr * 100:+.2f}%",
            f"최대낙폭(MDD): {maximum_drawdown(equity) * 100:.2f}%",
            f"샤프 / 소르티노: {sharpe:.2f} / {sortino:.2f}",
            "",
            "<b>종목별 결과</b>",
        ]
        for symbol, result in results.items():
            metrics = result.metrics
            lines.extend(
                [
                    "",
                    f"<b>{symbol}</b>",
                    f"수익률 / CAGR: {metrics['total_return_pct']:+.2f}% / "
                    f"{metrics['cagr_pct']:+.2f}%",
                    f"MDD / 최악 MAE: {metrics['mdd_pct']:.2f}% / "
                    f"{metrics['worst_mae_pct']:.2f}%",
                    f"완료 사이클 / 승률: {metrics['closed_cycles']}회 / "
                    f"{metrics['win_rate_pct']:.2f}%",
                    "Profit Factor / 기대수익: "
                    f"{_format_number(metrics['profit_factor'], 3)} / "
                    f"${metrics['expectancy_usd']:,.2f}",
                    f"평균 / 최대 보유일: {metrics['average_holding_days']:.1f} / "
                    f"{metrics['maximum_holding_days']}일",
                    f"TP1 / TP2 도달률: {metrics['tp1_reach_rate_pct']:.1f}% / "
                    f"{metrics['tp2_reach_rate_pct']:.1f}%",
                    f"연평균 신호 / 자금활용률: {metrics['signals_per_year']:.1f}회 / "
                    f"{metrics['average_capital_utilization_pct']:.1f}%",
                ]
            )
        lines.extend(
            [
                "",
                f"비용: 매수·매도 수수료 각 {self.config.global_.buy_fee * 100}% / "
                f"{self.config.global_.sell_fee * 100}%, "
                f"슬리피지 {self.config.backtest.default_slippage * 100}%",
                "고정자금: 종목당 $10,000 / 수익 재투자 없음",
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
                telebot.types.BotCommand("dashboard", "JDSS 통합 대시보드"),
                telebot.types.BotCommand("score", "JDSS 점수"),
                telebot.types.BotCommand("signal", "활성 매매신호"),
                telebot.types.BotCommand("status", "포지션 상태"),
                telebot.types.BotCommand("indicator", "기술지표"),
                telebot.types.BotCommand("backtest", "ALL/종목 기간 백테스트"),
                telebot.types.BotCommand("order", "JDSS 주문"),
                telebot.types.BotCommand("errors", "최근 이벤트"),
                telebot.types.BotCommand("ping", "봇 상태"),
                telebot.types.BotCommand("help", "도움말"),
            ]
        )
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        LOGGER.info("JDSS Telegram polling 시작")
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
