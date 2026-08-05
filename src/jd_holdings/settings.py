from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

LIVE_CONFIRMATION_PHRASE = "ENABLE_JDSS_LIVE_ORDERS"


@dataclass(frozen=True)
class RuntimeSettings:
    trading_mode: str
    live_confirmation: str
    telegram_bot_token: str | None
    allowed_chat_ids: tuple[int, ...]
    database_path: Path
    log_path: Path
    cache_path: Path = Path("data/cache")
    config_path: Path = Path("strategy.yaml")

    @property
    def live_trading_enabled(self) -> bool:
        return self.trading_mode == "live" and self.live_confirmation == LIVE_CONFIRMATION_PHRASE

    def require_live_trading(self) -> None:
        if not self.live_trading_enabled:
            raise PermissionError(
                "실주문 잠금 상태입니다. trading_mode과 이중 확인 문구를 모두 설정해야 합니다."
            )


def load_runtime_settings(env_path: str | Path | None = None) -> RuntimeSettings:
    if env_path:
        load_dotenv(Path(env_path), override=False)
    else:
        load_dotenv(override=False)
    mode = os.getenv("JDSS_TRADING_MODE", "dry_run").strip().lower()
    if mode not in {"dry_run", "live"}:
        raise ValueError("JDSS_TRADING_MODE은 dry_run 또는 live여야 합니다")
    raw_chat_ids = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
    try:
        chat_ids = tuple(int(value.strip()) for value in raw_chat_ids.split(",") if value.strip())
    except ValueError as exc:
        raise ValueError("TELEGRAM_ALLOWED_CHAT_IDS는 정수여야 합니다") from exc
    if len(set(chat_ids)) != len(chat_ids):
        raise ValueError("Telegram Chat ID가 중복되었습니다")
    return RuntimeSettings(
        trading_mode=mode,
        live_confirmation=os.getenv("JDSS_LIVE_CONFIRMATION", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        allowed_chat_ids=chat_ids,
        database_path=Path(os.getenv("JDSS_DB_PATH", "data/jdss.db")),
        log_path=Path(os.getenv("JDSS_LOG_PATH", "logs/jdss.log")),
        cache_path=Path(os.getenv("JDSS_CACHE_PATH", "data/cache")),
        config_path=Path(os.getenv("JDSS_CONFIG_PATH", "strategy.yaml")),
    )
