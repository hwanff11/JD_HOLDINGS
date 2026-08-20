from __future__ import annotations

from pathlib import Path

import pytest

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


@pytest.mark.parametrize("value", ["-100123", "0"])
def test_runtime_settings_rejects_group_or_invalid_telegram_id(monkeypatch, value):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", value)

    with pytest.raises(ValueError, match="개인 대화의 양수 관리자 사용자 ID"):
        load_runtime_settings()
