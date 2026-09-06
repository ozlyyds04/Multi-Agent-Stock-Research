from fastapi.testclient import TestClient
import pytest

from src.api import app
from src.observability import metrics
from src.observability.llm import InstrumentedLLM
from src.observability.pricing import compute_cost


class FakeLLM:
    def invoke(self, *a, **k):
        return type("R", (), {
            "content": "ok",
            "usage_metadata": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        })()


def test_metrics_endpoint_exposes_families():
    client = TestClient(app)
    r = client.get("/metrics")
    assert r.status_code == 200
    text = r.text
    for family in (
        "research_runs_total",
        "research_llm_calls_total",
        "research_node_duration_seconds",
        "research_data_source_errors_total",
        "research_strict_aborts_total",
    ):
        assert family in text


def test_instrumented_llm_records_call_metrics():
    metrics.set_node_node("analyze")
    llm = InstrumentedLLM(FakeLLM(), "test", "fake")
    out = llm.invoke("hello")
    assert out.content == "ok"
    assert metrics.LLM_CALLS.labels("test", "fake", "analyze")._value.get() >= 1
    assert metrics.LLM_TOKENS.labels("test", "fake", "input", "analyze")._value.get() >= 10


def test_pricing_compute_cost_scales_with_tokens():
    # 1M tokens at $1/M input => $1.0
    assert compute_cost("fake", 1_000_000, 0) == pytest.approx(1.0)
    assert compute_cost("fake", 0, 1_000_000) == pytest.approx(1.0)
