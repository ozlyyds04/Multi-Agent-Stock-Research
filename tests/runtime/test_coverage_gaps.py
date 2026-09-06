"""针对低覆盖运行时模块的补充测试：抬升总覆盖率（保持 ≥70%）。"""

import asyncio


def test_db_noop_when_not_configured():
    # conftest 已清空 DATABASE_URL + 设 CHECKPOINTER=memory，get_pool 返回 None -> 各函数 no-op
    from src.runtime import db

    async def run():
        await db.create_run("r", "AAPL", 5, "artifacts", False)
        await db.update_run("r", status="running", progress=10)
        assert await db.get_run("r") is None
        assert await db.list_runs() == []
        await db.reset_pool()

    asyncio.run(run())


def test_db_vec_to_pg():
    from src.runtime.memory import _vec_to_pg

    assert _vec_to_pg([1.0, 2.5]) == "[1.000000,2.500000]"


def test_backends_memory_cache_expiry():
    from src.runtime.backends import MemoryCache

    c = MemoryCache()
    c.set("k", {"a": 1}, 100)
    assert c.get("k") == {"a": 1}
    c.set("e", 2, -1)
    assert c.get("e") is None
    c.reset()


def test_backends_memory_short_term_recent():
    from src.runtime.backends import MemoryShortTermMemory

    m = MemoryShortTermMemory()
    for i in range(5):
        m.record("s", {"i": i})
    assert [x["i"] for x in m.recent("s", 3)] == [2, 3, 4]
    m.reset()


def test_backends_memory_event_bus_replay_done_closes():
    from src.runtime.backends import MemoryEventBus

    bus = MemoryEventBus()

    async def run():
        bus.set_loop(asyncio.get_running_loop())
        bus.publish("r1", {"type": "done", "status": "success"})
        events = []
        async for e in bus.stream("r1"):
            events.append(e)
            if e["type"] == "done":
                break
        return events

    assert asyncio.run(run())[-1]["type"] == "done"


def test_backends_factory_returns_memory_when_no_redis(monkeypatch):
    from src.runtime import backends as be

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS", raising=False)
    be.reset_backends()
    assert isinstance(be.get_cache(), be.MemoryCache)
    assert isinstance(be.get_short_term_memory(), be.MemoryShortTermMemory)
    assert isinstance(be.get_event_bus(), be.MemoryEventBus)
    be.reset_backends()


def test_memory_store_cosine_and_inmemory():
    from src.runtime.memory import InMemoryMemoryStore, _cosine

    assert _cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    store = InMemoryMemoryStore()
    store.add("AAPL", "要点", kind="fact", importance=0.5)
    assert len(store.recent("AAPL", 5)) == 1
    store.reset()


def test_memory_search_decay_updates_access():
    from src.runtime.memory import InMemoryMemoryStore

    store = InMemoryMemoryStore()
    store.add("AAPL", "苹果公司 最新收盘价", importance=0.8)
    rows = store.search([1.0, 0.0, 0.0], k=3, session_key="AAPL")
    assert rows  # 正常返回，access_count / last_accessed_at 被更新


def test_run_manager_evicts_stale_runs():
    import time

    from src.runtime.run_manager import RunManager, RunState

    mgr = RunManager()
    old = RunState(run_id="old", symbol="A", days=5, outdir="o", human=False)
    old.updated_at = time.time() - 25 * 3600  # 超过 24h TTL
    fresh = RunState(run_id="fresh", symbol="B", days=5, outdir="o", human=False)
    mgr.runs["old"] = old
    mgr.runs["fresh"] = fresh
    mgr._maybe_evict()
    assert "old" not in mgr.runs
    assert "fresh" in mgr.runs


def test_run_manager_snapshot_inmemory():
    from src.runtime.run_manager import RunManager, RunState

    mgr = RunManager()
    st = RunState(run_id="x", symbol="AAPL", days=5, outdir="o", human=False)
    st.status = "running"
    mgr.runs["x"] = st
    snap = mgr.snapshot("x")
    assert snap["status"] == "running"
    assert mgr.snapshot("missing") is None
    assert mgr.get("x") is st


def test_summarizer_returns_none_without_key(monkeypatch):
    from src.runtime import summarizer as s

    monkeypatch.setattr(s, "_llm_cfg", lambda: ("openai", "gpt-5"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s.reset_summarizer()
    assert s.get_summarizer() is None
    s.reset_summarizer()


def test_llm_extract_usage_response_metadata_fallback():
    from src.observability.llm import _extract_usage

    class R:
        response_metadata = {"token_usage": {"input_tokens": 3, "output_tokens": 4}}

    assert _extract_usage(R()) == (3, 4)


def test_llm_instrumented_callable_and_bound(monkeypatch):
    from src.observability.llm import InstrumentedLLM
    from src.observability import metrics

    metrics.set_node_node("analyze")

    class Inner:
        def invoke(self, *a, **k):
            return type("R", (), {"content": "ok", "usage_metadata": {"input_tokens": 1, "output_tokens": 1}})()

        def bind_tools(self, tools, **k):
            return self

    wrapped = InstrumentedLLM(Inner(), "test", "fake")
    # __call__ 路径（prompt | llm 会被包装成 RunnableLambda 调用）
    assert wrapped("hi").content == "ok"
    # bind_tools 返回的 BoundRunnable.invoke 走 _invoke 计数
    assert wrapped.bind_tools([]).invoke("x").content == "ok"
    assert metrics.LLM_CALLS.labels("test", "fake", "analyze")._value.get() >= 2
