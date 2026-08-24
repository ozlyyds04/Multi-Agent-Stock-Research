from unittest.mock import MagicMock
from src.agents.data_agent import DataAgent


def test_data_agent_run_collects_expected_keys(monkeypatch):
    # Patch tool calls used inside DataAgent.run
    monkeypatch.setattr("src.agents.data_agent.fetch_price_history", lambda s, d: {"data": [{"Date": "x", "Close": 1.0}]})
    monkeypatch.setattr("src.agents.data_agent.fetch_income_statement", lambda s, limit: {"income_statement": [{"revenue": 1, "reportedCurrency": "USD"}]})
    monkeypatch.setattr("src.agents.data_agent.fetch_key_metrics", lambda s: {"key_metrics_ttm": [{"returnOnEquityTTM": 0.1}]})
    monkeypatch.setattr("src.agents.data_agent.fetch_news_feeds", lambda s, rss, max_items: [{"title": "t"}])

    agent = DataAgent(llm=MagicMock(), rss_templates=["https://x"], max_news=3)
    out = agent.run("AAPL", days=5)

    assert out["symbol"] == "AAPL"
    assert "prices" in out and "data" in out["prices"]
    assert "fundamentals" in out
    assert "income_statement" in out["fundamentals"]
    assert "key_metrics_ttm" in out["fundamentals"]
    assert "news" in out
    assert isinstance(out["prices"]["data"], list)
