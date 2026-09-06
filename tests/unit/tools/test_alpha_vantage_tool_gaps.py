"""覆盖 alpha_vantage_tool.py 的低覆盖分支（mock requests / 限流器）。"""

import pytest


def _patch_requests(monkeypatch, payload, raise_for_status=True, exc=None):
    import src.tools.alpha_vantage_tool as av

    class _Resp:
        def raise_for_status(self):
            if exc:
                raise exc
            if raise_for_status is False:
                raise RuntimeError("http error")

        def json(self):
            return payload

    monkeypatch.setattr(av.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(av, "check_rate_limit", lambda *a, **k: None)
    monkeypatch.setenv(av.API_KEY_ENV, "test-key")
    return av


def test_get_api_key_arg_and_env(monkeypatch):
    from src.tools import alpha_vantage_tool as av

    monkeypatch.setenv(av.API_KEY_ENV, "env-key")
    assert av.get_api_key("arg-key") == "arg-key"
    assert av.get_api_key() == "env-key"
    monkeypatch.delenv(av.API_KEY_ENV, raising=False)
    with pytest.raises(ValueError, match="缺少 Alpha Vantage API 密钥"):
        av.get_api_key()


def test_request_success_and_error(monkeypatch):
    from src.tools import alpha_vantage_tool as av

    _patch_requests(monkeypatch, {"ok": 1})
    assert av._request({"f": "x"}, "k") == {"ok": 1}

    class _Err:
        def raise_for_status(self):
            raise RuntimeError("boom")

        def json(self):
            raise AssertionError("should not call json")

    monkeypatch.setattr(av.requests, "get", lambda *a, **k: _Err())
    out = av._request({"f": "x"}, "k")
    assert out == {"Error Message": "boom"}


def test_request_local_rate_limited(monkeypatch):
    from src.tools import alpha_vantage_tool as av
    from src.runtime.ratelimit import RateLimitExceeded

    def _raise(*a, **k):
        raise RateLimitExceeded("too fast")

    monkeypatch.setattr(av, "check_rate_limit", _raise)
    assert av._request({"f": "x"}, "k") == {"Note": "local rate limit reached"}


def test_is_rate_limited_response():
    from src.tools import alpha_vantage_tool as av

    assert av.is_rate_limited_response({"Note": "x"}) is True
    assert av.is_rate_limited_response({"Information": "y"}) is True
    assert av.is_rate_limited_response({"05. price": "1429.43"}) is False


def test_fetch_quote_variants(monkeypatch):
    from src.tools import alpha_vantage_tool as av

    _patch_requests(monkeypatch, {"Note": "limit"})
    assert av.fetch_quote("AAPL")["rate_limited"] is True

    _patch_requests(monkeypatch, {"Error Message": "bad"})
    assert av.fetch_quote("AAPL") == {"ok": False, "rate_limited": False, "message": "bad"}

    _patch_requests(monkeypatch, {"Global Quote": {}})
    assert av.fetch_quote("AAPL") == {"ok": True, "valid": False, "message": "股票代码无效或没有当前市场数据"}

    _patch_requests(monkeypatch, {"Global Quote": {"01. symbol": "AAPL", "05. price": "150.5"}})
    assert av.fetch_quote("AAPL") == {"ok": True, "valid": True, "symbol": "AAPL", "price": "150.5"}


def test_fetch_daily_series_variants(monkeypatch):
    from src.tools import alpha_vantage_tool as av

    _patch_requests(monkeypatch, {"Information": "limit"})
    assert av.fetch_daily_series("AAPL")["rate_limited"] is True

    _patch_requests(monkeypatch, {})
    out = av.fetch_daily_series("AAPL")
    assert out == {"ok": False, "rate_limited": False, "message": "未获取到 AAPL 的日线数据"}

    series = {
        "2026-01-02": {"1. open": "2", "2. high": "3", "3. low": "1", "4. close": "2.5", "5. volume": "10"},
        "2026-01-01": {"1. open": "1", "2. high": "2", "3. low": "0.9", "4. close": "1.5", "5. volume": "5"},
    }
    _patch_requests(monkeypatch, {"Time Series (Daily)": series})
    recs = av.fetch_daily_series("AAPL")["data"]
    # 升序（最旧在前）
    assert recs[0]["Date"] == "2026-01-01" and recs[1]["Date"] == "2026-01-02"
    assert recs[0]["Close"] == 1.5
