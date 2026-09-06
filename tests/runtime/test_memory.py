import math

from src.runtime.embedder import HashEmbedder, get_embedder, reset_embedder
from src.runtime.memory import (
    InMemoryMemoryStore,
    get_memory_store,
    reset_memory_store,
)


def test_hash_embedder_normalized_and_deterministic():
    e = HashEmbedder(dim=64)
    v = e.embed_one("apple 苹果 最新收盘价")
    assert len(v) == 64
    assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-6
    assert v == e.embed_one("apple 苹果 最新收盘价")


def test_get_embedder_defaults_to_hash_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reset_embedder()
    assert isinstance(get_embedder(), HashEmbedder)
    reset_embedder()


def test_inmemory_memory_add_and_recent():
    store = InMemoryMemoryStore()
    store.add("AAPL", "最新收盘价 250", kind="fact", importance=0.6)
    store.add("AAPL", "市盈率 22", kind="fact", importance=0.5)
    rows = store.recent("AAPL", 5)
    assert len(rows) == 2
    assert rows[0]["session_key"] == "AAPL"


def test_inmemory_memory_search_ranks_similar_first():
    store = InMemoryMemoryStore()
    store.add("AAPL", "苹果公司 最新收盘价 250 美元", kind="fact", importance=0.6)
    store.add("AAPL", "特斯拉公司 财报 净利润 高", kind="fact", importance=0.6)
    emb = get_embedder().embed_one("苹果公司 最新收盘价")
    rows = store.search(emb, k=2, session_key="AAPL")
    assert "苹果公司" in rows[0]["content"]
    assert rows[0]["score"] >= rows[1]["score"]


def test_inmemory_memory_decay_affects_score():
    store = InMemoryMemoryStore()
    store.add("AAPL", "苹果公司 最新收盘价 250 美元", kind="fact", importance=0.6)
    emb = get_embedder().embed_one("苹果公司 最新收盘价")
    rows = store.search(emb, k=1, session_key="AAPL")
    assert rows[0]["score"] > 0


def test_memory_store_defaults_to_inmemory(monkeypatch):
    monkeypatch.delenv("MEMORY_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_memory_store()
    assert isinstance(get_memory_store(), InMemoryMemoryStore)
    reset_memory_store()


def test_memory_compact_prunes_beyond_max_entries():
    store = InMemoryMemoryStore()
    for i in range(5):
        store.add("AAPL", f"要点 {i}", kind="fact", importance=0.9)
    result = store.compact("AAPL", max_entries=3, summarizer=None)
    assert result["mode"] == "prune"
    assert result["compacted"] == 2
    assert result["kept"] == 3
    assert len(store.recent("AAPL", 10)) == 3


def test_memory_compacts_to_single_summary_when_summarizer_available():
    store = InMemoryMemoryStore()
    for i in range(5):
        store.add("AAPL", f"历史要点 {i}", kind="fact", importance=0.5)
    result = store.compact("AAPL", max_entries=3, summarizer=lambda text: "压缩后的综合要点")
    assert result["mode"] == "summarize"
    assert result["compacted"] == 2
    recent = store.recent("AAPL", 10)
    assert any(r["kind"] == "compressed" and r["content"] == "压缩后的综合要点" for r in recent)
    # 原有 5 条 + 1 条压缩摘要
    assert len(recent) <= 4


def test_memory_compact_noop_within_limit():
    store = InMemoryMemoryStore()
    store.add("AAPL", "a", kind="fact")
    store.add("AAPL", "b", kind="fact")
    result = store.compact("AAPL", max_entries=50, summarizer=lambda t: "x")
    assert result["mode"] == "none"
    assert result["compacted"] == 0


def _mock_graph(monkeypatch, sample_bundle_upper):
    import src.graph.orchestrator as orchestrator

    monkeypatch.setattr(
        orchestrator, "fetch_quote", lambda s: {"ok": True, "valid": True, "symbol": s, "price": "1.0"}
    )
    monkeypatch.setattr(
        "src.agents.data_agent.DataAgent.run", lambda self, symbol, days: sample_bundle_upper
    )
    monkeypatch.setattr(
        "src.agents.analyst_agent.AnalystAgent.run",
        lambda self, bundle, days, feedback="": "分析师备注",
    )
    monkeypatch.setattr(
        "src.agents.compliance_agent.ComplianceAgent.run", lambda self, note: "最终备注"
    )
    monkeypatch.setattr(
        "src.agents.supervisor_agent.SupervisorAgent.run",
        lambda self, symbol, data_summary, final_note: "主管摘要",
    )
    monkeypatch.setattr("src.tools.storage_tool.save_json", lambda *a, **k: None)
    monkeypatch.setattr("src.tools.storage_tool.save_markdown", lambda *a, **k: None)


def test_graph_writes_long_term_memory(monkeypatch, minimal_cfg, sample_bundle_upper, tmp_path):
    from src.graph.orchestrator import build_graph

    _mock_graph(monkeypatch, sample_bundle_upper)
    reset_memory_store()

    app = build_graph(minimal_cfg)
    state = {"symbol": "AAPL", "days": 5, "outdir": str(tmp_path), "run_id": "mem-test"}
    out = app.invoke(state, config={"configurable": {"thread_id": "mem-test"}})

    assert out.get("memory_recalled") == 0  # 首次运行无召回
    assert out.get("memory_written") == 1  # 写入 1 条
    store = get_memory_store()
    recent = store.recent("AAPL", 3)
    assert recent and "最新收盘价" in recent[0]["content"]
    reset_memory_store()
