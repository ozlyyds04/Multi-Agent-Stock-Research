from src.tools.quote_tool import fetch_quote


def test_quote_tool_dispatches_hk_to_akshare(monkeypatch):
    seen = {}

    def fake_hk(symbol):
        seen["symbol"] = symbol
        return {"ok": True, "valid": True, "symbol": symbol, "price": "440.0"}

    monkeypatch.setattr("src.tools.quote_tool.fetch_hk_quote", fake_hk)
    out = fetch_quote("00700")
    assert out["valid"] is True
    assert seen["symbol"] == "00700"


def test_quote_tool_dispatches_others_to_alpha_vantage(monkeypatch):
    seen = {}

    def fake_av(symbol, api_key=None):
        seen["symbol"] = symbol
        return {"ok": True, "valid": True, "symbol": symbol, "price": "200.0"}

    monkeypatch.setattr("src.tools.quote_tool.fetch_av_quote", fake_av)
    out = fetch_quote("AAPL")
    assert out["valid"] is True
    assert seen["symbol"] == "AAPL"
