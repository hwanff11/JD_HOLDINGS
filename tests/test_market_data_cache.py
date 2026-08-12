from __future__ import annotations

from jd_holdings.infrastructure import market_data


def test_yfinance_internal_cache_uses_jdss_cache_dir(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        market_data.yf,
        "set_tz_cache_location",
        lambda value: calls.append(value),
    )

    data_source = market_data.YFinanceDataSource(tmp_path / "cache")

    expected = tmp_path / "cache" / "yfinance"
    assert data_source.cache_dir == tmp_path / "cache"
    assert expected.is_dir()
    assert calls == [str(expected)]


def test_yfinance_internal_cache_is_not_reconfigured_without_cache_dir(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        market_data.yf,
        "set_tz_cache_location",
        lambda value: calls.append(value),
    )

    data_source = market_data.YFinanceDataSource()

    assert data_source.cache_dir is None
    assert calls == []
