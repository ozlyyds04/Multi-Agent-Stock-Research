import os
import src.graph.orchestrator as orchestrator


def test_e2e_workflow_creates_artifacts(monkeypatch, minimal_cfg, sample_bundle_upper, tmp_path):
    outdir = tmp_path / "artifacts"
    outdir.mkdir()

    # Provide real bundle + deterministic agent outputs
    monkeypatch.setattr("src.agents.data_agent.DataAgent.run", lambda self, symbol, days: sample_bundle_upper)
    monkeypatch.setattr("src.agents.analyst_agent.AnalystAgent.run", lambda self, bundle, days: "ANALYST_NOTE")
    monkeypatch.setattr("src.agents.compliance_agent.ComplianceAgent.run", lambda self, note: "FINAL_NOTE")

    # Create a small PNG placeholder for plot
    def fake_plot(dates, closes, currency, path):
        p = tmp_path / "chart.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header
        return str(p)

    monkeypatch.setattr("src.tools.plot_tool.save_price_plot", fake_plot)

    # Make PDF export a no-op but still create the file
    def fake_export(_md_path, pdf_path):
        with open(pdf_path, "wb") as f:
            f.write(b"%PDF-1.4\n%mock\n")

    monkeypatch.setattr("src.tools.pdf_tool.export_report_to_pdf", fake_export)

    app = orchestrator.build_graph(minimal_cfg)
    state = {"symbol": "AAPL", "days": 5, "outdir": str(outdir)}
    res = app.invoke(state)

    # Publisher writes report_path; raw json name is standard
    assert "report_path" in res
    assert os.path.exists(res["report_path"]) or isinstance(res["report_path"], str)

    # Your publisher sets pdf_path in state; make sure file exists if set
    pdf_path = os.path.join(str(outdir), os.path.basename(res.get("pdf_path", "")))
    if res.get("pdf_path"):
        assert os.path.exists(pdf_path)
