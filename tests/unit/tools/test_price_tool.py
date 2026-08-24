import json
from datetime import datetime, timezone

from src.tools import price_tool
from src.tools.price_tool import fetch_price_history


def _av_daily_response(days=3):
    series = {}
    for i in range(days):
        date = f"2026-08-{14 - i:02d}"
        series[date] = {
            "1. open": f"{100 + i}.0",
            "2. high": f"{102 + i}.0",
            "3. low": f"{99 + i}.0",
            "4. close": f"{101 + i}.0",
            "5. volume": str(1000000 + i),
        }
    return {"Time Series (Daily)": series}


def test_price_tool_returns_dict(monkeypatch, tmp_path):
    monkeypatch.setattr(price_tool, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "src.tools.alpha_vantage_tool._request",
        lambda *a, **k: _av_daily_response(),
    )
    out = fetch_price_history("AAPL", 5)
    assert isinstance(out, dict)
    assert out["data"]
    # 统一为时间升序：最旧在前、最新在后
    assert out["data"][0]["Date"] == "2026-08-12"
    assert out["data"][-1]["Date"] == "2026-08-14"
    assert out["data"][-1]["Close"] == 101.0


def test_price_history_uses_cache_without_network(monkeypatch, tmp_path):
    monkeypatch.setattr(price_tool, "_CACHE_DIR", str(tmp_path))
    payload = {
        "symbol": "AAPL",
        "days": 5,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "data": [{"Date": "2026-08-10", "Open": 100.0, "Close": 101.0}],
        "meta": {"days": 5},
    }
    (tmp_path / "AAPL_5d.json").write_text(json.dumps(payload), encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise AssertionError("有效缓存存在时不应调用 Alpha Vantage")

    monkeypatch.setattr("src.tools.alpha_vantage_tool._request", boom)

    out = fetch_price_history("AAPL", 5)
    assert out["data"] == payload["data"]
    assert out["meta"].get("cached") is True


def test_price_history_retries_transient_error_then_succeeds(monkeypatch, tmp_path):
    monkeypatch.setattr(price_tool, "_CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def flaky(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"Error Message": "connection reset by peer"}
        return _av_daily_response()

    monkeypatch.setattr("src.tools.alpha_vantage_tool._request", flaky)

    out = fetch_price_history("AAPL", 5)
    assert calls["n"] == 2  # 1 次瞬时失败 + 1 次成功
    assert len(out["data"]) == 3


def test_price_history_rate_limit_returns_error_without_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(price_tool, "_CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def limited(*_args, **_kwargs):
        calls["n"] += 1
        return {"Note": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."}

    monkeypatch.setattr("src.tools.alpha_vantage_tool._request", limited)

    out = fetch_price_history("AAPL", 5)
    assert calls["n"] == 1  # 每日配额限流不重试
    assert out["data"] == []
    assert out["__error__"]


def test_price_history_hk_uses_akshare(monkeypatch, tmp_path):
    monkeypatch.setattr(price_tool, "_CACHE_DIR", str(tmp_path))

    def fake_hk_daily(symbol, days=30):
        assert symbol == "00700"
        return {
            "ok": True,
            "data": [
                {"Date": "2026-08-14", "Open": 436.0, "High": 445.0, "Low": 435.0, "Close": 440.0, "Volume": 2000}
            ],
        }

    monkeypatch.setattr(price_tool, "fetch_hk_daily", fake_hk_daily)
    monkeypatch.setattr(
        "src.tools.alpha_vantage_tool._request",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("港股不应调用 Alpha Vantage")),
    )

    out = fetch_price_history("00700", 5)
    assert out["data"][0]["Close"] == 440.0
    assert out["meta"].get("cached") is False


def test_price_history_hk_invalid_code_no_retry(monkeypatch, tmp_path):
    monkeypatch.setattr(price_tool, "_CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def fake_hk_daily(symbol, days=30):
        calls["n"] += 1
        return {"ok": False, "rate_limited": False, "retryable": False, "message": "无效代码"}

    monkeypatch.setattr(price_tool, "fetch_hk_daily", fake_hk_daily)

    out = fetch_price_history("00000", 5)
    assert calls["n"] == 1
    assert out["data"] == []
    assert out["__error__"]


def test_price_history_reorders_cache_to_ascending(monkeypatch, tmp_path):
    monkeypatch.setattr(price_tool, "_CACHE_DIR", str(tmp_path))
    payload = {
        "symbol": "SKHY",
        "days": 10,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "data": [
            {"Date": "2026-08-17", "Open": 172.6, "Close": 171.38},
            {"Date": "2026-08-04", "Open": 150.18, "Close": 154.38},
        ],
        "meta": {"days": 10},
    }
    (tmp_path / "SKHY_10d.json").write_text(json.dumps(payload), encoding="utf-8")

    out = fetch_price_history("SKHY", 10)
    assert out["data"][0]["Date"] == "2026-08-04"
    assert out["data"][-1]["Date"] == "2026-08-17"
