"""覆盖 checkpointer / tasks（Celery 包装）/ precheck 的低覆盖分支。"""

import pytest

# ---------------- checkpointer ----------------


def test_checkpointer_postgres_fails_back_to_memory(monkeypatch):
    import psycopg

    from src.utils import checkpointer

    # 直接重置单例
    checkpointer.reset_checkpointer()
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/db")
    monkeypatch.setenv("CHECKPOINTER", "")

    def _raise(*a, **k):
        raise ConnectionError("no db reachable")

    monkeypatch.setattr(psycopg.Connection, "connect", staticmethod(_raise))

    saver = checkpointer.get_checkpointer()
    from langgraph.checkpoint.memory import MemorySaver

    assert isinstance(saver, MemorySaver)
    # 第二次调用命中 _SAVER 缓存
    assert checkpointer.get_checkpointer() is saver
    checkpointer.reset_checkpointer()


def test_checkpointer_memory_when_no_dsn(monkeypatch):
    from src.utils import checkpointer

    checkpointer.reset_checkpointer()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CHECKPOINTER", "memory")
    from langgraph.checkpoint.memory import MemorySaver

    assert isinstance(checkpointer.get_checkpointer(), MemorySaver)
    checkpointer.reset_checkpointer()


# ---------------- tasks (Celery) ----------------


class _FakeTaskSelf:
    max_retries = 2

    def __init__(self, retries=0):
        self.request = type("_Req", (), {"retries": retries})()

    def retry(self, **kw):
        raise RuntimeError("celery retry requested")


def test_tasks_run_research_success(monkeypatch):
    from src import tasks

    class Runner:
        def execute(self, *a, **k):
            return ("completed", False)

        def resume(self, *a, **k):
            return ("completed", False)

    monkeypatch.setattr(tasks, "get_runner", lambda: Runner())
    assert tasks.run_research.run.__func__(_FakeTaskSelf(), "r1", "AAPL", 5, "out") == "completed"


def test_tasks_run_research_retries_before_max(monkeypatch):
    from src import tasks

    dead = []

    class Runner:
        def execute(self, *a, **k):
            return ("failed", True)

        def resume(self, *a, **k):
            return ("failed", False)

    monkeypatch.setattr(tasks, "get_runner", lambda: Runner())
    monkeypatch.setattr(tasks.dead_letter_run, "delay", lambda rid: dead.append(rid))

    # 未到 max_retries -> 触发 Celery retry
    with pytest.raises(RuntimeError, match="retry requested"):
        tasks.run_research.run.__func__(_FakeTaskSelf(retries=0), "r1", "AAPL", 5, "out")
    assert dead == []


def test_tasks_run_research_retry_exhausted_dead_letter(monkeypatch):
    from src import tasks

    dead = []

    class Runner:
        def execute(self, *a, **k):
            return ("failed", True)

    monkeypatch.setattr(tasks, "get_runner", lambda: Runner())
    monkeypatch.setattr(tasks.dead_letter_run, "delay", lambda rid: dead.append(rid))

    # 已达 max_retries（2 == max_retries）-> 不再触发 retry，落死信
    assert tasks.run_research.run.__func__(_FakeTaskSelf(retries=2), "r1", "AAPL", 5, "out") == "failed"
    assert dead == ["r1"]


def test_tasks_run_research_dead_letter_direct(monkeypatch):
    from src import tasks

    class Runner:
        def execute(self, *a, **k):
            return ("failed", False)

        def resume(self, *a, **k):
            return ("failed", False)

    dead = []
    monkeypatch.setattr(tasks, "get_runner", lambda: Runner())
    monkeypatch.setattr(tasks.dead_letter_run, "delay", lambda rid: dead.append(rid))
    assert tasks.run_research.run.__func__(_FakeTaskSelf(), "r1", "AAPL", 5, "out") == "failed"
    assert dead == ["r1"]


def test_tasks_resume_research(monkeypatch):
    from src import tasks

    dead = []

    class Runner:
        def resume(self, *a, **k):
            return ("completed", False)

    monkeypatch.setattr(tasks, "get_runner", lambda: Runner())
    monkeypatch.setattr(tasks.dead_letter_run, "delay", lambda rid: dead.append(rid))
    assert tasks.resume_research.run.__func__(_FakeTaskSelf(), "r1", "feedback") == "completed"
    assert dead == []


def test_tasks_dead_letter_run_no_pool(monkeypatch):
    from src import tasks

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CHECKPOINTER", "memory")
    out = tasks.dead_letter_run("r1")
    assert out == {"run_id": "r1"}


# ---------------- precheck ----------------


def test_precheck_quote_exception(monkeypatch):
    from src.graph import precheck

    def _boom(symbol):
        raise ValueError("no key")

    monkeypatch.setattr(precheck, "fetch_quote", _boom)
    ok, err, action = precheck.precheck_symbol("NVDA")
    assert ok is False and "预检请求失败" in err and "ALPHA_VANTAGE_API_KEY" in action


def test_precheck_invalid_symbol(monkeypatch):
    from src.graph import precheck

    monkeypatch.setattr(precheck, "fetch_quote", lambda s: {"ok": True, "valid": False})
    ok, err, action = precheck.precheck_symbol("ZZZZ")
    assert ok is False and "无效或没有当前市场数据" in err and "核实" in action


def test_precheck_rate_limited_skips(monkeypatch):
    from src.graph import precheck

    monkeypatch.delenv("SKIP_PRECHECK_ON_RATELIMIT", raising=False)
    monkeypatch.setattr(
        precheck,
        "fetch_quote",
        lambda s: {"ok": False, "rate_limited": True, "message": "429"},
    )
    assert precheck.precheck_symbol("NVDA") == (True, None, None)


def test_precheck_rate_limited_without_skip(monkeypatch):
    from src.graph import precheck

    monkeypatch.setenv("SKIP_PRECHECK_ON_RATELIMIT", "false")
    monkeypatch.setattr(
        precheck,
        "fetch_quote",
        lambda s: {"ok": False, "rate_limited": True, "message": "429"},
    )
    ok, err, action = precheck.precheck_symbol("NVDA")
    assert ok is False and "无法验证" in err and "请检查" in action


def test_precheck_ok(monkeypatch):
    from src.graph import precheck

    monkeypatch.setattr(precheck, "fetch_quote", lambda s: {"ok": True, "valid": True})
    assert precheck.precheck_symbol("AAPL") == (True, None, None)
