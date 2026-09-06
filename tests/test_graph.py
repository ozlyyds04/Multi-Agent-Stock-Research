import os
from src.graph.orchestrator import run_pipeline, _normalize_supervisor_markdown, _daily_market_metrics


def test_normalize_supervisor_markdown():
    md = (
        "# NVIDIA（NASDAQ: NVDA）研究报告\n\n"
        "# 概览\n概览内容\n\n"
        "# 估值与技术面\n# 估值\n估值内容\n# 技术面\n技术面内容\n\n"
        "# 新闻头条及解读\n# Temasek投资相关消息\n消息内容\n\n"
        "#\n"
        "##\n"
        "普通段落\n\n\n\n"
        "结尾\n"
    )
    title, body = _normalize_supervisor_markdown(md)
    assert title == "NVIDIA（NASDAQ: NVDA）研究报告"
    assert "### 概览" in body
    assert "### 估值与技术面" in body
    assert "#### 估值" in body
    assert "#### 技术面" in body
    assert "### 新闻头条及解读" in body
    assert "#### Temasek投资相关消息" in body
    assert "#\n" not in body
    assert "##\n" not in body
    assert "\n\n\n" not in body
    assert "普通段落" in body
    assert not body.startswith("# ")


def test_normalize_supervisor_markdown_preserves_relative_levels():
    md = "# 标题\n\n## 基本面\n内容\n\n### 盈利能力\n盈利内容\n\n### 毛利率与资本回报\n毛利率内容\n"
    title, body = _normalize_supervisor_markdown(md)
    assert title == "标题"
    assert "### 基本面" in body
    assert "#### 盈利能力" in body
    assert "#### 毛利率与资本回报" in body


def test_daily_market_metrics():
    rows = [
        {"Date": "2026-08-10", "Close": 100.0},
        {"Date": "2026-08-11", "Close": 105.0},
        {"Date": "2026-08-14", "Close": 110.0},
    ]
    m = _daily_market_metrics(rows, {"epsDiluted": 5.0, "reportedCurrency": "USD"})
    assert m["latest_close"] == 110.0
    assert round(m["period_return"], 4) == 0.1
    assert round(m["pe"], 2) == 22.0
    assert m["latest_date"] == "2026-08-14"
    assert m["start_date"] == "2026-08-10"

    empty = _daily_market_metrics([], {})
    assert empty["latest_close"] is None
    assert empty["pe"] is None

    no_eps = _daily_market_metrics(rows, {})
    assert no_eps["pe"] is None

    # 币种错配导致的异常市盈率（如 ADR 美元价格 ÷ 外币 EPS）不展示
    adr = _daily_market_metrics(rows, {"epsDiluted": 60378.0})
    assert adr["pe"] is None


def test_pipeline_smoke(monkeypatch, tmp_path, sample_bundle_upper):
    # 离线冒烟：mock 预检、各智能体和存储，不依赖外部行情/新闻配额
    monkeypatch.setattr(
        "src.graph.orchestrator.fetch_quote",
        lambda symbol: {"ok": True, "valid": True, "symbol": symbol, "price": "1.0"},
    )
    monkeypatch.setattr(
        "src.agents.data_agent.DataAgent.run",
        lambda self, symbol, days: sample_bundle_upper,
    )
    monkeypatch.setattr(
        "src.agents.analyst_agent.AnalystAgent.run",
        lambda self, bundle, days, feedback="": "分析师备注",
    )
    monkeypatch.setattr(
        "src.agents.compliance_agent.ComplianceAgent.run",
        lambda self, note: "最终备注",
    )
    monkeypatch.setattr(
        "src.agents.supervisor_agent.SupervisorAgent.run",
        lambda self, symbol, data_summary, final_note: "主管摘要",
    )
    monkeypatch.setattr("src.tools.storage_tool.save_json", lambda *a, **k: None)
    monkeypatch.setattr("src.tools.storage_tool.save_markdown", lambda *a, **k: None)

    outdir = tmp_path.as_posix()
    res = run_pipeline("AAPL", 5, outdir, human=False)
    assert "report" in res
