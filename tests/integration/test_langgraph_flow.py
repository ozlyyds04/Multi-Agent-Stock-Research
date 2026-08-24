from unittest.mock import MagicMock

import pytest

import src.graph.orchestrator as orchestrator


def test_build_graph_executes_nodes_in_order(monkeypatch, minimal_cfg, sample_bundle_upper, tmp_path):
    calls = []

    # Stub agent run methods at class level so build_graph instances use them.
    monkeypatch.setattr("src.agents.data_agent.DataAgent.run", lambda self, symbol, days: (calls.append("data"), sample_bundle_upper)[1])
    monkeypatch.setattr("src.agents.analyst_agent.AnalystAgent.run", lambda self, bundle, days, feedback="": (calls.append("analyst"), "ANALYST_NOTE")[1])
    monkeypatch.setattr("src.agents.compliance_agent.ComplianceAgent.run", lambda self, note: (calls.append("compliance"), "FINAL_NOTE")[1])

    # Avoid heavy dependencies during test
    monkeypatch.setattr("src.tools.plot_tool.save_price_plot", lambda dates, closes, currency, path: (calls.append("plot"), str(tmp_path / "x.png"))[1])
    monkeypatch.setattr("src.tools.storage_tool.save_markdown", lambda md, outdir, filename: calls.append("save_md"))
    monkeypatch.setattr("src.tools.storage_tool.save_json", lambda bundle, outdir, filename: calls.append("save_json"))
    monkeypatch.setattr("src.tools.pdf_tool.export_report_to_pdf", lambda *a, **k: calls.append("pdf"))

    app = orchestrator.build_graph(minimal_cfg)
    state = {"symbol": "AAPL", "days": 5, "outdir": str(tmp_path)}
    app.invoke(state, config={"configurable": {"thread_id": "order-test"}})

    # Ensures agent-to-agent communication + publish happens
    assert calls[:3] == ["data", "analyst", "compliance"]
    assert "data" in calls
    assert "analyst" in calls


def test_build_graph_repairs_missing_data_before_analyze(monkeypatch, minimal_cfg, sample_bundle_upper, tmp_path):
    calls = []
    incomplete = dict(sample_bundle_upper)
    incomplete["prices"] = {"symbol": "AAPL", "data": []}

    monkeypatch.setattr(
        "src.agents.data_agent.DataAgent.run",
        lambda self, symbol, days: (calls.append("data"), incomplete)[1],
    )
    monkeypatch.setattr(
        "src.graph.orchestrator.fetch_price_history",
        lambda symbol, days: (calls.append("repair"), sample_bundle_upper["prices"])[1],
    )
    monkeypatch.setattr(
        "src.agents.analyst_agent.AnalystAgent.run",
        lambda self, bundle, days, feedback="": (calls.append("analyst"), "ANALYST_NOTE")[1],
    )
    monkeypatch.setattr(
        "src.agents.compliance_agent.ComplianceAgent.run",
        lambda self, note: (calls.append("compliance"), "FINAL_NOTE")[1],
    )
    monkeypatch.setattr(
        "src.agents.supervisor_agent.SupervisorAgent.run",
        lambda self, symbol, data_summary, final_note: (calls.append("supervisor"), "FINAL_NOTE")[1],
    )
    monkeypatch.setattr(
        "src.tools.plot_tool.save_price_plot",
        lambda dates, closes, currency, path: (calls.append("plot"), str(tmp_path / "x.png"))[1],
    )
    monkeypatch.setattr("src.tools.storage_tool.save_markdown", lambda *a, **k: calls.append("save_md"))
    monkeypatch.setattr("src.tools.storage_tool.save_json", lambda *a, **k: calls.append("save_json"))
    monkeypatch.setattr("src.tools.pdf_tool.export_report_to_pdf", lambda *a, **k: calls.append("pdf"))

    app = orchestrator.build_graph(minimal_cfg)
    state = {"symbol": "AAPL", "days": 5, "outdir": str(tmp_path)}
    app.invoke(state, config={"configurable": {"thread_id": "repair-test"}})

    # 数据缺失 → 修复节点 → 分析 → 合规 → 主管
    assert calls[:4] == ["data", "repair", "analyst", "compliance"]


def test_build_graph_aborts_when_repair_cannot_fix(monkeypatch, minimal_cfg, tmp_path):
    incomplete = {
        "symbol": "AAPL",
        "prices": {"symbol": "AAPL", "data": []},
        "fundamentals": {
            "income_statement": [{"fiscalYear": 2025, "revenue": 1000}],
            "key_metrics_ttm": [{"returnOnEquityTTM": 0.3}],
        },
        "news": [{"title": "t", "link": "l", "published": "2026-01-01"}],
    }

    monkeypatch.setattr("src.agents.data_agent.DataAgent.run", lambda self, symbol, days: incomplete)
    monkeypatch.setattr(
        "src.graph.orchestrator.fetch_price_history",
        lambda symbol, days: {"symbol": symbol, "data": [], "__error__": {"where": "prices", "message": "限流"}},
    )

    app = orchestrator.build_graph(minimal_cfg)
    state = {"symbol": "AAPL", "days": 5, "outdir": str(tmp_path)}
    with pytest.raises(ValueError, match="严格模式中止"):
        app.invoke(state, config={"configurable": {"thread_id": "abort-test"}})


def _mock_publish_dependencies(monkeypatch, calls, tmp_path):
    monkeypatch.setattr(
        "src.agents.supervisor_agent.SupervisorAgent.run",
        lambda self, symbol, data_summary, final_note: (calls.append("supervisor"), "FINAL_NOTE")[1],
    )
    monkeypatch.setattr(
        "src.tools.plot_tool.save_price_plot",
        lambda dates, closes, currency, path: (calls.append("plot"), str(tmp_path / "x.png"))[1],
    )
    monkeypatch.setattr("src.tools.storage_tool.save_markdown", lambda *a, **k: calls.append("save_md"))
    monkeypatch.setattr("src.tools.storage_tool.save_json", lambda *a, **k: calls.append("save_json"))
    monkeypatch.setattr("src.tools.pdf_tool.export_report_to_pdf", lambda *a, **k: calls.append("pdf"))


def test_build_graph_approval_pauses_and_resumes(monkeypatch, minimal_cfg, sample_bundle_upper, tmp_path):
    from langgraph.types import Command

    calls = []
    _mock_publish_dependencies(monkeypatch, calls, tmp_path)
    monkeypatch.setattr("src.agents.data_agent.DataAgent.run", lambda self, symbol, days: sample_bundle_upper)
    monkeypatch.setattr(
        "src.agents.analyst_agent.AnalystAgent.run",
        lambda self, bundle, days, feedback="": (calls.append("analyst"), "ANALYST_NOTE")[1],
    )
    monkeypatch.setattr(
        "src.agents.compliance_agent.ComplianceAgent.run",
        lambda self, note: (calls.append("compliance"), "FINAL_NOTE")[1],
    )

    app = orchestrator.build_graph(minimal_cfg)
    config = {"configurable": {"thread_id": "approval-test-1"}}
    state = {"symbol": "AAPL", "days": 5, "outdir": str(tmp_path), "require_approval": True}

    r1 = app.invoke(state, config=config)
    assert r1.get("__interrupt__")
    assert calls[:2] == ["analyst", "compliance"]

    r2 = app.invoke(Command(resume={"action": "approve"}), config=config)
    assert r2.get("report_path")
    assert "supervisor" in calls


def test_build_graph_approval_reject_loops_back(monkeypatch, minimal_cfg, sample_bundle_upper, tmp_path):
    from langgraph.types import Command

    calls = []
    _mock_publish_dependencies(monkeypatch, calls, tmp_path)
    monkeypatch.setattr("src.agents.data_agent.DataAgent.run", lambda self, symbol, days: sample_bundle_upper)
    monkeypatch.setattr(
        "src.agents.analyst_agent.AnalystAgent.run",
        lambda self, bundle, days, feedback="": (calls.append("analyst"), "ANALYST_NOTE")[1],
    )
    monkeypatch.setattr(
        "src.agents.compliance_agent.ComplianceAgent.run",
        lambda self, note: (calls.append("compliance"), "FINAL_NOTE")[1],
    )

    app = orchestrator.build_graph(minimal_cfg)
    config = {"configurable": {"thread_id": "approval-test-2"}}
    state = {"symbol": "AAPL", "days": 5, "outdir": str(tmp_path), "require_approval": True}

    app.invoke(state, config=config)
    r2 = app.invoke(Command(resume={"action": "reject", "comment": "请更保守"}), config=config)

    assert r2.get("__interrupt__")  # 驳回后回到分析智能体重写，再次暂停等待审批
    assert calls.count("analyst") == 2
    assert calls.count("compliance") == 2
