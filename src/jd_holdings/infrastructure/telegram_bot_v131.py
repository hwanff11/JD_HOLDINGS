"""Compatibility import for deployments that referenced the former v1.3.1 module."""

from .telegram_bot_final import FinalTelegramBotApp

ValidatedTelegramBotApp = FinalTelegramBotApp

__all__ = ["ValidatedTelegramBotApp"]
