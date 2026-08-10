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
from zoneinfo import ZoneInfo

import pandas as pd
import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from jd_holdings import __version__
from jd_holdings.application.analysis_service import AnalysisResult, AnalysisService
from jd_holdings.application.database import SQLiteRepository
from jd_holdings.application.idle_cash_manager import IdleCashManager, IdleCashReleasePending
from jd_holdings.application.order_monitor import OrderMonitor
from jd_holdings.application.reconciliation import ReconciliationService
from jd_holdings.application.trading_service import QuoteChangedError, TradingService
from jd_holdings.backtest.engine import BacktestResult
from jd_holdings.backtest.performance import maximum_drawdown
from jd_holdings.backtest.strategy_engine import StrategyBacktestEngine
from jd_holdings.config import StrategyConfig
from jd_holdings.infrastructure.market_clock import MarketClock
from jd_holdings.infrastructure.market_data import YFinanceDataSource
from jd_holdings.infrastructure.toss_client import TossClient
from jd_holdings.settings import RuntimeSettings

LOGGER = logging.getLogger(__name__)
IDLE_CASH_COMMANDS = ("sgov",)
SEOUL_TZ = ZoneInfo("Asia/Seoul")


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


def parse_history_request(
    text: str,
    enabled_symbols: tuple[str, ...],
    default_days: int = 7,
) -> tuple[tuple[str, ...], int]:
    """Parse /history [SYMBOL] [DAYS], defaulting to all configured symbols."""
    parts = (text or "").split()[1:]
    if len(parts) > 2:
        raise ValueError("형식: /history [종목] [거래일수]")
    symbols = enabled_symbols
    if parts and not parts[0].isdigit():
        symbol = parts.pop(0).upper()
        if symbol not in enabled_symbols:
            raise ValueError(
                f"지원하지 않는 종목입니다. 사용 가능 종목: {', '.join(enabled_symbols)}"
            )
        symbols = (symbol,)
    if parts:
        days = int(parts[0])
    else:
        days = default_days
    if not 1 <= days <= 90:
        raise ValueError("조회 기간은 1~90 거래일 사이로 입력해 주세요.")
    return symbols, days


def _is_toss_order_maintenance_window(now_kst: datetime) -> bool:
    return now_kst.hour == 8 and 50 <= now_kst.minute <= 59


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


def _format_idle_cash_event(event: str, trading_mode: str) -> str:
    escaped = html.escape(event)
    if trading_mode == "dry_run":
        label = "모의체결" if "FILLED" in event or "체결 반영" in event else "모의처리"
        return f"🧪 {label} — {escaped}"
    return f"💵 {escaped}"


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
        "S": "S등급·최우수",
        "A": "A등급·우수",
        "B": "B등급·관심",
        "WATCH": "관찰",
        "NO_TRADE": "매수 보류",
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
        "FIRST_ENTRY_CANDIDATE": "1차 매수 검토 가능",
        "ADD_ENTRY_CANDIDATE": "추가매수 검토 가능",
        "REBUY_CANDIDATE": "재매수 검토 가능",
        "NO_ACTION": "현재 매수 조건 미충족",
    }.get(value, value.replace("_", " "))


def _cci_label(value: float) -> str:
    if value <= -200:
        return "극심한 과매도"
    if value <= -150:
        return "강한 과매도"
    if value <= -100:
        return "과매도"
    if value >= 100:
        return "과열"
    return "중립"


def _rsi_label(value: float) -> str:
    if value <= 20:
        return "극심한 과매도"
    if value <= 30:
        return "과매도"
    if value <= 40:
        return "약세"
    if value >= 70:
        return "과매수"
    if value >= 60:
        return "강세"
    return "중립"


def _atr_label(value: float) -> str:
    if value < 0.01:
        return "변동성 낮음"
    if value < 0.02:
        return "변동성 보통 이하"
    if value <= 0.10:
        return "전략 적정 변동성"
    return "변동성 매우 큼"


def _volume_label(value: float) -> str:
    if value < 1.0:
        return "평균 이하"
    if value < 1.2:
        return "평균 수준"
    if value < 1.5:
        return "거래 증가"
    if value < 2.0:
        return "거래 활발"
    return "거래량 급증"


def _close_position_label(value: float) -> str:
    if value < 0.3:
        return "당일 저가권 마감"
    if value < 0.5:
        return "당일 하단 마감"
    if value < 0.7:
        return "당일 상단 마감"
    return "당일 고가권 마감"


def _ema_label(snapshot) -> str:
    if snapshot.close > snapshot.ema20 > snapshot.ema60:
        return "중장기 상승 추세"
    if snapshot.close > snapshot.ema5:
        return "단기 반등 확인"
    return "단기 반등 미확인"


def _bollinger_label(snapshot) -> str:
    if snapshot.close <= snapshot.bb_lower * 0.98:
        return "하단 2% 이상 이탈 (강한 과매도)"
    if snapshot.close <= snapshot.bb_lower:
        return "하단 접촉·이탈 (과매도)"
    if snapshot.close <= snapshot.bb_lower * 1.02:
        return "하단 근처"
    return "하단 위"


def _condition_mark(condition: bool) -> str:
    return "✅ 충족" if condition else "⬜ 미충족"


def _guide_cards() -> tuple[str, ...]:
    """Return the user-visible guide derived from the JDSS 2.2 contract."""
    card1 = (
        "📖 <b>[JDSS 2.2 지표 및 점수 체계 가이드]</b>\n\n"
        "🎯 <b>1. JDSS 종합 점수 체계 (100점 만점)</b>\n"
        "💡 <i>(단일 지표가 아닌 아래 모든 지표들의 총 합산 점수입니다.)</i>\n"
        "• <b>55점 이상</b> : 모든 매수 단계의 최소 점수\n"
        "• <b>반등 5점 이상</b> : 추락 중 진입을 막는 필수 조건\n"
        "• <b>82점 이상</b> : A등급 구간\n"
        "• <b>90점 이상</b> : S등급 구간\n\n"
        "🟢 <b>2. 시장 국면 지표 (25점 만점)</b>\n"
        "• <b>20일 / 60일 이동평균선(EMA)</b> 교차 상태 분석\n"
        "• <b>🟢 강세장 (25점)</b> : 20일 EMA &gt; 60일 EMA\n"
        "• <b>🟡 중립장 (15점)</b> : 중립 추세\n"
        "• <b>🔴 약세장 (0점)</b> : 모든 신규·추가매수 차단\n\n"
        "📉 <b>3. 과매도·반등 지표</b>\n"
        "• <b>CCI (5 / 10), RSI (5 / 14), 볼린저 밴드</b>로 과매도 측정\n"
        "• 양봉, 전일 종가, EMA5, 캔들 종가 위치로 반등 점수 산출"
    )
    card2 = (
        "🛒 <b>[JDSS 2.2 분할매수 및 익절 메커니즘]</b>\n\n"
        "🛒 <b>4단계 분할 매수</b>\n"
        "• <b>1차 (40%)</b> : JDSS 55점·반등 5점 이상 🟢\n"
        "• <b>2차 (30%)</b> : 최초 체결가 대비 <b>-2%</b> 🟡\n"
        "• <b>3차 (20%)</b> : 최초 체결가 대비 <b>-5%</b> 🟠\n"
        "• <b>4차 (10%)</b> : 최초 체결가 대비 <b>-7%</b> 🔴\n"
        "• 모든 단계는 RED 국면 차단 및 사용자 2단계 승인 필수\n\n"
        "💰 <b>목표 익절과 잔여청산</b>\n"
        "• <b>TP1</b> : 평단 대비 <b>+4%</b>, 약 50% 매도\n"
        "• <b>TP2</b> : 평단 대비 <b>+6%</b>, 잔량 매도\n"
        "• TP1 완전체결 후 20개 완결 거래일 동안 TP2 미체결 시 "
        "평단 <b>+2%</b> 잔여청산으로 전환\n"
        "• 자동손절·재매수 없음\n\n"
        "💡 <i>/score [종목] 명령어로 최신 지표를 확인할 수 있습니다.</i>"
    )
    card3 = (
        "🔎 <b>[보조지표를 쉽게 읽는 법]</b>\n\n"
        "• <b>CCI 5 / 10</b> : 단기 과매도 강도입니다. "
        "<code>-100</code> 이하 과매도, <code>-150</code> 이하 강한 과매도, "
        "<code>-200</code> 이하는 극심한 과매도로 봅니다.\n"
        "• <b>RSI 5 / 14</b> : 0~100 사이의 매수·매도 압력입니다. "
        "<code>30</code> 이하는 과매도, <code>20</code> 이하는 극심한 과매도입니다.\n"
        "• <b>EMA 5 / 20 / 60</b> : 단기·중기·장기 추세선입니다. "
        "종가가 EMA5 위로 올라오면 단기 반등 확인에 유리합니다.\n"
        "• <b>볼린저 하단</b> : 종가가 하단에 닿거나 밑으로 내려가면 "
        "가격이 평소 범위보다 눌린 과매도 구간으로 봅니다.\n"
        "• <b>거래량 비율</b> : 최근 평균 대비 오늘 거래량입니다. "
        "<code>1.0배</code>는 평균, <code>1.5배</code>는 활발, <code>2.0배</code> 이상은 급증입니다.\n"
        "• <b>ATR 비율</b> : 하루 가격 변동 폭입니다. JDSS 2.2 전략은 "
        "<code>2~10%</code>를 적정 구간으로 평가하고, 10% 초과는 위험을 낮춰 평가합니다.\n"
        "• <b>종가 위치</b> : 당일 저가=0, 고가=1입니다. "
        "<code>0.50</code> 이상이면 장 후반 반등 조건 하나를 충족합니다.\n\n"
        "⚠️ <i>보조지표 하나만으로 매수하지 않습니다. 총점 55점, 반등 5점, "
        "시장 국면과 2단계 승인을 함께 확인합니다.</i>"
    )
    card4 = (
        "💵 <b>[SGOV 유휴자금 운용]</b>\n\n"
        "• TQQQ·SOXL에 아직 투입되지 않은 JDSS 배정금은 <b>SGOV</b>에 예치합니다.\n"
        "• 계좌에는 최소 <code>$250</code> 현금 버퍼를 남기고, <code>$100</code> 이상일 때 조정합니다.\n"
        "• 전략 매수 전 필요한 금액만큼 SGOV를 먼저 매도하고, "
        "매도 체결과 달러 매수가능금액을 확인한 뒤 본 주문을 허용합니다.\n"
        "• SGOV 현금화가 미체결·거절되거나 원장 수량이 맞지 않으면 전략 매수를 차단합니다.\n"
        "• 기존 개인 SGOV는 JDSS 관리분으로 자동 편입하지 않습니다.\n\n"
        "💡 <i>/sgov 명령어로 JDSS 관리 SGOV 수량과 현금화 상태를 확인할 수 있습니다.</i>"
    )
    return card1, card2, card3, card4


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
        idle_cash_manager: IdleCashManager | None = None,
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
        self.idle_cash_manager = idle_cash_manager
        self.allowed_chat_id = settings.allowed_chat_ids[0]
        self.bot = telebot.TeleBot(settings.telegram_bot_token, threaded=True)
        self._stop = threading.Event()
        self._backtest_lock = threading.Lock()
        self._last_monitor = 0.0
        self._last_idle_cash_sweep = 0.0
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
    def _format_score_history(
        symbol: str,
        history: list[dict[str, object]],
        requested_days: int,
    ) -> str:
        lines = [
            f"📈 <b>{symbol} 최근 {requested_days}거래일 JDSS 점수</b>",
            "<i>완결 일봉 기준 재계산 결과</i>",
            "",
        ]
        if not history:
            return "\n".join(lines + ["조회 가능한 점수 이력이 없습니다."])
        for item in history:
            trade_date = item["trade_date"]
            lines.append(
                f"• <code>{trade_date}</code>  "
                f"<b>{item['score']}점</b> · {item['grade']} · {item['regime']}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_score_message(result: AnalysisResult) -> str:
        snapshot = result.snapshot
        score = result.score
        regime_value = score.regime.value
        return (
            f"🎯 <b>[{result.symbol} 지표 상세 분석]</b>\n\n"
            f"📊 <b>총점</b> : <code>{score.total}점</code> / 100점 ({_grade_label(score.grade.value)})\n"
            f"🌐 <b>시장 국면</b> : {_regime_label(regime_value)}\n\n"
            "✨ <b>점수 구성</b>\n"
            f"• 시장 국면 : <code>{score.regime_score:2d} / 25점</code>\n"
            f"• 과매도 점수 : <code>{score.oversold_score:2d} / 40점</code>\n"
            f"• 반등 점수 : <code>{score.reversal_score:2d} / 20점</code>\n"
            f"• 거래량 점수 : <code>{score.volume_score:2d} / 10점</code>\n"
            f"• ATR 변동성 : <code>{score.atr_score:2d} / 5점</code>\n\n"
            "🔎 <b>보조 지표 해석</b>\n"
            f"• CCI 5 / 10 : <code>{snapshot.cci5:.1f} / {snapshot.cci10:.1f}</code> "
            f"→ {_cci_label(snapshot.cci5)} / {_cci_label(snapshot.cci10)}\n"
            f"• RSI 5 / 14 : <code>{snapshot.rsi5:.1f} / {snapshot.rsi14:.1f}</code> "
            f"→ {_rsi_label(snapshot.rsi5)} / {_rsi_label(snapshot.rsi14)}\n"
            f"• EMA 추세 : <b>{_ema_label(snapshot)}</b> "
            f"(종가 <code>{snapshot.close:.2f}</code> / EMA5 <code>{snapshot.ema5:.2f}</code>)\n"
            f"• 볼린저 하단 : <b>{_bollinger_label(snapshot)}</b> "
            f"(하단 <code>{snapshot.bb_lower:.2f}</code>)\n"
            f"• 거래량 : <code>{snapshot.volume_ratio:.2f}배</code> → {_volume_label(snapshot.volume_ratio)}\n"
            f"• ATR : <code>{snapshot.atr_pct * 100:.2f}%</code> → {_atr_label(snapshot.atr_pct)}\n"
            f"• 종가 위치 : <code>{snapshot.close_position:.2f}</code> → {_close_position_label(snapshot.close_position)}\n\n"
            "🚦 <b>JDSS 2.2 핵심 조건</b>\n"
            f"• 총점 55점 이상 : {_condition_mark(score.total >= 55)}\n"
            f"• 반등 5점 이상 : {_condition_mark(score.reversal_score >= 5)}\n"
            f"• RED 국면 아님 : {_condition_mark(regime_value not in {'RED', 'BEARISH'})}\n\n"
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

    def _format_idle_cash_message(self) -> str:
        if self.idle_cash_manager is None or not self.config.idle_cash.enabled:
            return "💵 <b>[유휴자금 운용]</b>\n\nSGOV 운용이 비활성화되어 있습니다."
        snapshot = self.idle_cash_manager.snapshot()
        state_label = "🚨 SAFE_MODE" if snapshot.safe_mode else "✅ 정상"
        personal_qty = max(0, snapshot.broker_quantity - snapshot.state.managed_quantity)
        return (
            "💵 <b>[JDSS SGOV 유휴자금]</b>\n\n"
            f"• <b>운용 상태</b> : {state_label}\n"
            f"• <b>JDSS 관리 수량</b> : <code>{snapshot.state.managed_quantity}주</code>\n"
            f"• <b>관리분 평가액</b> : <code>{_money(snapshot.market_value)}</code>\n"
            f"• <b>SGOV 현재가</b> : <code>{_money(snapshot.price)}</code>\n"
            f"• <b>목표 유휴자금</b> : <code>{_money(snapshot.target_value)}</code>\n"
            f"• <b>달러 주문가능</b> : <code>{_money(snapshot.buying_power)}</code>\n"
            f"• <b>비관리 SGOV</b> : <code>{personal_qty}주</code>\n\n"
            "💡 <i>비관리 수량은 기존 개인 보유분으로 간주하며 자동 매도하지 않습니다.</i>"
        )

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
        except Exception as exc:
            LOGGER.warning("대시보드 계좌 요약 조회 실패: %s", type(exc).__name__)
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
                if self.idle_cash_manager is not None and self.config.idle_cash.enabled:
                    cash = self.idle_cash_manager.snapshot()
                    lines.append(
                        f"💵 <b>SGOV 관리분</b> : <code>{cash.state.managed_quantity}주</code> "
                        f"(<code>{_money(cash.market_value)}</code>)"
                    )
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

        @bot.message_handler(commands=list(IDLE_CASH_COMMANDS))
        def idle_cash(message):
            if not self._authorized_message(message):
                return
            try:
                self._send(self._format_idle_cash_message())
            except Exception as exc:
                LOGGER.exception("SGOV 유휴자금 조회 실패")
                self._send(
                    f"❌ SGOV 유휴자금 조회 중 오류 발생:\n<code>{html.escape(str(exc))}</code>"
                )

        @bot.message_handler(commands=["history", "h"])
        def history(message):
            if not self._authorized_message(message):
                return
            try:
                symbols, days = parse_history_request(
                    message.text, self.config.enabled_symbols
                )
                self._send(f"⏳ 최근 {days}거래일 점수를 계산 중입니다...")
                for symbol in symbols:
                    rows = self.analysis_service.score_history(symbol, days)
                    self._send(self._format_score_history(symbol, rows, days))
            except Exception as exc:
                LOGGER.exception("history 실패")
                self._send(
                    f"❌ 점수 이력 조회 중 오류 발생:\n<code>{html.escape(str(exc))}</code>"
                )

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
            signals = self.trading_service.active_signals()
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
                for card in _guide_cards():
                    self._send(card, chat_id=message.chat.id)
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
                f"• <b>종료일</b> : <code>{request.end}</code>"
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

        @bot.message_handler(commands=["help", "menu", "start"])
        def help_handler(message):
            if not self._authorized_message(message):
                return
            self._send(
                "☀️ <b>[JH홀딩스 JDSS 메뉴]</b>\n\n"
                "• <code>/dashboard</code> : 통합 대시보드\n"
                "• <code>/account</code> : 💰 토스 계좌 잔고\n"
                "• <code>/sgov</code> : 💵 JDSS SGOV 유휴자금\n"
                "• <code>/status</code> : 종목별 상세 포지션\n"
                "• <code>/score</code> : JDSS 세부 지표 분석\n"
                "• <code>/history</code> 또는 <code>/h</code> : 최근 점수 이력\n"
                "• <code>/signal</code> : 활성 매수 신호\n"
                "• <code>/backtest</code> : 자유 종목 백테스트\n"
                "• <code>/guide</code> : 📖 JDSS 용어 및 지표 가이드\n"
                "• <code>/order</code> : 미체결 주문 현황\n"
                "• <code>/errors</code> : 최근 시스템 기록\n"
                "• <code>/ping</code> : 봇 상태 확인\n\n"
                "✨ <b>사용 예시</b> : <code>/score TQQQ</code> · <code>/h TQQQ 7</code> · <code>/bt NVDA 100</code>\n\n"
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
                self._send_final_quote(quote)
                bot.answer_callback_query(call.id, "최종 주문조건을 계산했습니다.")
            except IdleCashReleasePending as exc:
                markup = InlineKeyboardMarkup()
                if exc.signal_id is not None:
                    markup.add(
                        InlineKeyboardButton(
                            "❌ 현금화 후 매수 취소",
                            callback_data=f"cancel|cash|{exc.signal_id}",
                        )
                    )
                self._send(
                    "💵 <b>SGOV 현금화를 진행하고 있습니다.</b>\n\n"
                    "체결과 달러 매수가능금액이 확인되면 최종 매수 승인 버튼을 자동으로 보내드립니다.",
                    markup=markup,
                )
                bot.answer_callback_query(call.id, str(exc), show_alert=True)
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
            if not self._authorized_callback(call):
                bot.answer_callback_query(call.id, "권한이 없습니다.", show_alert=True)
                return
            try:
                _, kind, raw_id = call.data.split("|", 2)
                if kind == "approval":
                    self.trading_service.cancel_approval(int(raw_id))
                elif kind == "cash":
                    self.trading_service.cancel_cash_release(int(raw_id))
                else:
                    raise ValueError("지원하지 않는 취소 유형입니다.")
            except Exception as exc:
                bot.answer_callback_query(call.id, str(exc), show_alert=True)
                return
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
        markup.add(
            InlineKeyboardButton(
                "❌ 무시 / 취소", callback_data=f"cancel|approval|{approval_id}"
            )
        )
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

    def _send_final_quote(self, quote) -> None:
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton(
                "✅ 최종 매수 실행",
                callback_data=f"ex|{quote.execution_approval_id}|{quote.execution_token}",
            )
        )
        markup.add(
            InlineKeyboardButton(
                "❌ 취소",
                callback_data=f"cancel|approval|{quote.execution_approval_id}",
            )
        )
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

    def _run_backtest_and_send(self, request: TelegramBacktestRequest) -> None:
        try:
            start = request.start.isoformat()
            end = request.end.isoformat()
            warmup_start = (request.start - timedelta(days=400)).isoformat()
            spy = self.data_source.daily("SPY", warmup_start, end)
            qqq = self.data_source.daily("QQQ", warmup_start, end)
            idle_cash_data = (
                self.data_source.daily(self.config.idle_cash.symbol, warmup_start, end)
                if self.config.idle_cash.enabled
                else None
            )
            
            # 🚀 섹터 가드용 데이터 (실거래와 동일하게 SOXX, SMH 등 벤치마크 데이터를 백테스트 엔진에 전달)
            sector_data = {}
            guard_config = self.config.market_regime.get("soxl_sector_guard", {})
            if guard_config.get("enabled", False):
                benchmarks = guard_config.get("benchmark_candidates", ["SOXX", "SMH"])
                for bench in benchmarks:
                    try:
                        sector_data[bench] = self.data_source.daily(bench, warmup_start, end)
                    except Exception as exc:
                        LOGGER.warning("%s 섹터 데이터 없이 백테스트 계속: %s", bench, exc)
            
            engine = StrategyBacktestEngine(self.config)
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
                    sector_data=sector_data if sector_data else None,
                    idle_cash_data=idle_cash_data,
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
    def _format_trade_timeline(result: BacktestResult, limit: int = 15) -> list[str]:
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
                suffix = ""
            else:  # SELL
                details = f"[{qty_str}|{price_str}]"
                if purpose == "TP1":
                    label = "1차익절"
                    icon = "⚫"
                    suffix = "🎉"
                elif purpose == "TP2":
                    label = "2차완청"
                    icon = "⚫"
                    suffix = "🎉"
                elif purpose == "REMAINDER_EXIT":
                    label = "잔여청산"
                    icon = "⚫"
                    suffix = "🎉"
                else:
                    label = "매도"
                    icon = "⚫"
                    suffix = ""
            events.append(
                (
                    str(trade["date"]),
                    1,
                    f"<code>{icon}[{d}][{label}]{details}</code>{suffix}",
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
        total_return = final / initial - 1
        first_result = next(iter(results.values()))
        lines = [
            "☀️ <b>[JDSS 전략 백테스트 결과]</b>",
            "",
            f"<code>├ 시작일   : {first_result.start_date}</code>",
            f"<code>├ 종료일   : {first_result.end_date}</code>",
            f"<code>├ 초기자산 : {_money(initial):>12}</code>",
            f"<code>├ 최종자산 : {_money(final):>12}</code>",
            f"<code>├ 누적수익 : {total_return * 100:>+10.2f}%</code>",
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
                    f"<code>├ SGOV수익 : {_money(metrics.get('idle_cash_income', 0)):>10}</code>",
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

                # 토스증권 주간거래 전 주문 취소·리셋 시간에는 주문 상태 갱신을 건너뜁니다.
                if _is_toss_order_maintenance_window(datetime.now(SEOUL_TZ)):
                    monitor_due = False

                if monitor_due:
                    for event in self.order_monitor.run_once():
                        self._send(f"ℹ️ {html.escape(event)}")
                    if self.idle_cash_manager is not None:
                        for event in self.idle_cash_manager.refresh_orders():
                            self._send(_format_idle_cash_event(event, self.settings.trading_mode))
                        for quote in self.trading_service.resume_cash_releases():
                            self._send(
                                f"💵 <b>[{quote.symbol} SGOV 현금화 완료]</b>\n"
                                "최신 주문조건을 다시 확인했습니다."
                            )
                            self._send_final_quote(quote)
                    mismatches = self.reconciliation_service.run()
                    for symbol, issues in mismatches.items():
                        self._send(
                            f"🚨 <b>[{symbol} SAFE_MODE 경고]</b>\n"
                            + "\n".join(html.escape(issue) for issue in issues)
                        )
                    self._last_monitor = time.monotonic()
                cash_due = (
                    self.idle_cash_manager is not None
                    and time.monotonic() - self._last_idle_cash_sweep
                    >= self.config.idle_cash.sweep_interval_seconds
                )
                if cash_due:
                    for event in self.idle_cash_manager.run_once():
                        self._send(_format_idle_cash_event(event, self.settings.trading_mode))
                    self._last_idle_cash_sweep = time.monotonic()
                self.repository.expire_stale_signals()
            except Exception as exc:
                LOGGER.exception("scheduler 실패")
                self.repository.log_event("WARNING", "SCHEDULER_ERROR", str(exc))

    def run(self) -> None:
        self.bot.set_my_commands(
            [
                telebot.types.BotCommand("dashboard", "☀️ 통합 대시보드"),
                telebot.types.BotCommand("account", "💰 토스 계좌 잔고"),
                telebot.types.BotCommand(IDLE_CASH_COMMANDS[0], "💵 SGOV 유휴자금"),
                telebot.types.BotCommand("status", "✨ 종목별 포지션 상세"),
                telebot.types.BotCommand("score", "🎯 JDSS 지표 분석"),
                telebot.types.BotCommand("history", "📈 최근 점수 이력"),
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
