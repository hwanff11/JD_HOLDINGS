from __future__ import annotations

from pathlib import Path

from jd_holdings.settings import load_runtime_settings


def test_runtime_settings_support_shared_cache_path(monkeypatch):
    monkeypatch.setenv("JDSS_TRADING_MODE", "dry_run")
    monkeypatch.setenv("JDSS_LIVE_CONFIRMATION", "")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("JDSS_DB_PATH", "/srv/jdss/data/jdss.db")
    monkeypatch.setenv("JDSS_LOG_PATH", "/srv/jdss/logs/jdss.log")
    monkeypatch.setenv("JDSS_CACHE_PATH", "/srv/jdss/data/cache")
    monkeypatch.setenv("JDSS_CONFIG_PATH", "/srv/jdss/current/strategy.yaml")

    settings = load_runtime_settings()

    assert settings.database_path == Path("/srv/jdss/data/jdss.db")
    assert settings.log_path == Path("/srv/jdss/logs/jdss.log")
    assert settings.cache_path == Path("/srv/jdss/data/cache")
    assert settings.config_path == Path("/srv/jdss/current/strategy.yaml")
    assert not settings.live_trading_enabled
