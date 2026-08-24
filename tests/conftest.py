import os
from dataclasses import dataclass
import pytest


# Hermetic test env: ChatDeepSeek/ChatOpenAI require a key at construction,
# but agent runs are mocked, so a placeholder key is enough for graph builds.
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")


@dataclass
class DummyMsg:
    content: str


class DummyChain:
    """Mimics a LangChain runnable chain with .invoke() returning an object with .content."""
    def __init__(self, content: str):
        self.content = content
        self.last_input = None

    def invoke(self, payload):
        self.last_input = payload
        return DummyMsg(self.content)


class DummyPrompt:
    """Mimics ChatPromptTemplate where (prompt | llm) returns a runnable chain."""
    def __init__(self, chain: DummyChain):
        self._chain = chain

    def __or__(self, _llm):
        return self._chain


@pytest.fixture
def sample_symbol():
    return "AAPL"


@pytest.fixture
def sample_days():
    return 10


@pytest.fixture
def sample_bundle_upper():
    """
    IMPORTANT: matches your publisher expectations: Date / Close keys.
    """
    return {
        "symbol": "AAPL",
        "prices": {
            "data": [
                {"Date": "2026-01-01", "Close": 150.0},
                {"Date": "2026-01-02", "Close": 152.0},
                {"Date": "2026-01-03", "Close": 151.0},
                {"Date": "2026-01-04", "Close": 155.0},
                {"Date": "2026-01-05", "Close": 157.0},
            ]
        },
        "fundamentals": {
            "income_statement": [
                {"fiscalYear": 2025, "revenue": 1000, "netIncome": 200, "epsDiluted": 5, "reportedCurrency": "USD"}
            ],
            "key_metrics_ttm": [
                {"returnOnEquityTTM": 0.31, "freeCashFlowYieldTTM": 0.02}
            ]
        },
        "news": [
            {"title": "Headline 1", "link": "https://example.com/1", "published": "2025-01-01"},
            {"title": "Headline 2", "link": "https://example.com/2", "published": "2025-01-02"},
        ]
    }


@pytest.fixture
def minimal_cfg(tmp_path):
    """
    Minimal config dict used by build_graph tests.
    Keep strict_mode false to avoid aborting on missing third-party data.
    """
    return {
        "llm": {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 1, "max_tokens": 4500},
        "news": {"sources": ["https://example.com/rss?s={{symbol}}"]},
        "orchestration": {"max_news": 5, "timeout_sec": 90, "price_days_default": 30},
        "report": {"filename_template": "{{symbol}}_{{date}}_report.md", "outdir": str(tmp_path)},
        "compliance": {
            "forbidden_phrases": ["guaranteed returns", "inside information"],
            "disclosure": ["This is not investment advice."]
        },
        "strict_mode": False,
    }

import os
import subprocess
import pytest


def pytest_configure(config):
    # Ensure these are set before any imports during tests
    os.environ.setdefault("PYTHONBREAKPOINT", "0")
    os.environ.setdefault("MPLBACKEND", "Agg")
    # 测试始终使用内存 checkpointer，避免依赖 PostgreSQL 实例
    os.environ["CHECKPOINTER"] = "memory"


@pytest.fixture(autouse=True)
def _block_external_processes(monkeypatch, tmp_path):
    """
    Prevent any external binary invocation (wkhtmltopdf/pandoc/etc.)
    and make PDF/chart creation deterministic.
    """

    # --- Block subprocess calls globally ---
    class _DummyPopen:
        def __init__(self, *a, **k):
            self.returncode = 0
        def communicate(self, *a, **k):
            return (b"", b"")
        def wait(self, *a, **k):
            return 0

    monkeypatch.setattr(subprocess, "Popen", _DummyPopen, raising=True)

    def _dummy_run(*a, **k):
        class R:
            returncode = 0
            stdout = b""
            stderr = b""
        return R()

    monkeypatch.setattr(subprocess, "run", _dummy_run, raising=True)

    # --- Fake fpdf2 PDF export if it is imported ---
    try:
        from src.tools import pdf_tool

        def _fake_export(_md_path, pdf_path):
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4\n%mock\n")
            return pdf_path

        monkeypatch.setattr(pdf_tool, "export_report_to_pdf", _fake_export, raising=True)
    except Exception:
        pass

    # --- Fake plot saving (avoid matplotlib runtime issues) ---
    try:
        from src.tools import plot_tool  # noqa
        fake_png = tmp_path / "chart.png"
        fake_png.write_bytes(b"\x89PNG\r\n\x1a\n")
        monkeypatch.setattr(plot_tool, "save_price_plot", lambda *a, **k: str(fake_png), raising=True)
    except Exception:
        pass

    yield

