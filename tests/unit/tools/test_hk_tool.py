import pandas as pd

from src.tools.hk_tool import (
    fetch_hk_daily,
    fetch_hk_quote,
    is_hk_symbol,
    to_hk_bare,
)


def _hk_df():
    return pd.DataFrame({
        "date": ["2026-08-13", "2026-08-14"],
        "open": [450.0, 436.0],
        "high": [460.0, 445.0],
        "low": [448.0, 435.0],
        "close": [441.0, 440.0],
        "volume": [1000, 2000],
        "amount": [1e8, 2e8],
    })


def test_hk_symbol_recognition():
    assert is_hk_symbol("1810")
    assert is_hk_symbol("9988")
    assert is_hk_symbol("0700")
    assert is_hk_symbol("00700")
    assert is_hk_symbol("1810.HK")
    assert is_hk_symbol("0700.HK")
    assert is_hk_symbol("00700.HK")
    assert is_hk_symbol("9988.HK")
    assert not is_hk_symbol("AAPL")
    assert not is_hk_symbol("600519.SHH")
    assert not is_hk_symbol("002185")


def test_to_hk_bare_and_rss():
    assert to_hk_bare("1810") == "01810"
    assert to_hk_bare("1810.HK") == "01810"
    assert to_hk_bare("0700") == "00700"
    assert to_hk_bare("00700.HK") == "00700"
    assert to_hk_bare("9988.HK") == "09988"


def test_to_hk_bare_rejects_non_numeric():
    import pytest

    with pytest.raises(ValueError):
        to_hk_bare("AAPL")


def test_fetch_hk_daily_maps_columns(monkeypatch):
    monkeypatch.setattr("akshare.stock_hk_daily", lambda symbol: _hk_df())
    out = fetch_hk_daily("00700", days=2)
    assert out["ok"] is True
    assert len(out["data"]) == 2
    assert out["data"][-1]["Date"] == "2026-08-14"
    assert out["data"][-1]["Close"] == 440.0
    assert out["data"][-1]["Volume"] == 2000


def test_fetch_hk_daily_invalid_code(monkeypatch):
    def invalid(_symbol):
        raise KeyError("date")

    monkeypatch.setattr("akshare.stock_hk_daily", invalid)
    out = fetch_hk_daily("00000", days=5)
    assert out["ok"] is False
    assert out.get("retryable") is False


def test_fetch_hk_quote_valid(monkeypatch):
    monkeypatch.setattr("akshare.stock_hk_daily", lambda symbol: _hk_df())
    out = fetch_hk_quote("00700")
    assert out["ok"] is True
    assert out["valid"] is True
    assert out["symbol"] == "00700"
    assert out["price"] == 440.0


def test_fetch_hk_quote_invalid(monkeypatch):
    monkeypatch.setattr(
        "akshare.stock_hk_daily",
        lambda symbol: (_ for _ in ()).throw(KeyError("date")),
    )
    out = fetch_hk_quote("00000")
    assert out["ok"] is True
    assert out["valid"] is False
