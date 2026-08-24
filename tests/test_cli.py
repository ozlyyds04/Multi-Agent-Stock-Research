import builtins
import types
import pytest

import src.cli as cli


def test_cli_success(monkeypatch, capsys):
    # Arrange: fake run_pipeline success
    def fake_run_pipeline(symbol, days, outdir, human=False):
        return {"report": "artifacts/MSFT/report.md", "plot": "artifacts/MSFT/chart.png"}

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

    # Fake argv
    monkeypatch.setattr(
        "sys.argv",
        ["prog", "--symbol", "MSFT", "--days", "5", "--outdir", "artifacts", "--human", "false"],
    )

    # Act
    cli.main()

    # Assert: outputs printed
    out = capsys.readouterr().out
    assert "== 输出 ==" in out
    assert "report: artifacts/MSFT/report.md" in out


def test_cli_error_path(monkeypatch, capsys):
    # Arrange: fake run_pipeline error payload
    def fake_run_pipeline(symbol, days, outdir, human=False):
        return {"status": "error", "reason": "严格模式中止", "suggested_action": "禁用 strict_mode"}

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

    monkeypatch.setattr(
        "sys.argv",
        ["prog", "--symbol", "META", "--days", "5", "--outdir", "artifacts", "--human", "false"],
    )

    # Act
    cli.main()

    # Assert
    out = capsys.readouterr().out
    assert "[错误] 严格模式中止" in out
    assert "建议：禁用 strict_mode" in out
