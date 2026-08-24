from unittest.mock import MagicMock
from src.agents.analyst_agent import AnalystAgent
from tests.conftest import DummyChain, DummyPrompt


def test_analyst_agent_run_returns_string(sample_bundle_upper):
    agent = AnalystAgent(llm=MagicMock())

    chain = DummyChain("ANALYST_NOTE")
    agent.prompt = DummyPrompt(chain)  # key fix: avoid MagicMock with "|" operator

    out = agent.run(bundle=sample_bundle_upper, days=7)

    assert isinstance(out, str)
    assert out == "ANALYST_NOTE"
    assert chain.last_input["symbol"] == "AAPL"
    assert chain.last_input["days"] == 7


def _incomplete_bundle():
    return {
        "symbol": "AAPL",
        "prices": {"symbol": "AAPL", "data": []},
        "fundamentals": {
            "income_statement": [{"fiscalYear": 2025, "revenue": 1000, "netIncome": 200}],
            "key_metrics_ttm": [{"returnOnEquityTTM": 0.3}],
        },
        "news": [{"title": "t", "link": "l", "published": "2026-01-01"}],
    }


def test_analyst_agent_uses_tools_when_data_missing(monkeypatch):
    calls = []

    def fake_quote(symbol):
        calls.append(("quote", symbol))
        return {"ok": True, "valid": True, "symbol": symbol, "price": "150.0"}

    monkeypatch.setattr("src.tools.quote_tool.fetch_quote", fake_quote)

    class FakeResp:
        def __init__(self, content, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls or []

    class FakeToolLLM:
        def __init__(self, responses):
            self._responses = list(responses)
            self.bound = None

        def bind_tools(self, tools):
            self.bound = tools
            return self

        def invoke(self, messages):
            return self._responses.pop(0)

    llm = FakeToolLLM([
        FakeResp("", [{"name": "fetch_quote", "args": {"symbol": "AAPL"}, "id": "call_1"}]),
        FakeResp("ANALYST_NOTE", []),
    ])
    agent = AnalystAgent(llm=llm)

    out = agent.run(bundle=_incomplete_bundle(), days=7)

    assert out == "ANALYST_NOTE"
    assert calls == [("quote", "AAPL")]
    assert llm.bound is not None


def test_analyst_agent_skips_tools_when_data_complete(sample_bundle_upper):
    llm = MagicMock()
    agent = AnalystAgent(llm=llm)
    chain = DummyChain("ANALYST_NOTE")
    agent.prompt = DummyPrompt(chain)

    out = agent.run(bundle=sample_bundle_upper, days=7)

    assert out == "ANALYST_NOTE"
    llm.bind_tools.assert_not_called()
