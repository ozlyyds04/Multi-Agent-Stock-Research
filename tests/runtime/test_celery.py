import asyncio

import pytest

from src.celery_app import celery_app, celery_enabled
from src.runtime import runner
from src.runtime.runner import get_runner
import src.tasks as tasks


def test_celery_app_has_queues_and_routes():
    names = [q.name for q in celery_app.conf.task_queues]
    assert "research" in names
    assert "research_high" in names
    assert "dead_letter" in names
    routes = celery_app.conf.task_routes
    assert routes["src.tasks.run_research"]["queue"] == "research"
    assert routes["src.tasks.resume_research"]["queue"] == "research_high"
    assert routes["src.tasks.dead_letter_run"]["queue"] == "dead_letter"


def test_tasks_registered():
    assert tasks.run_research.name == "src.tasks.run_research"
    assert tasks.resume_research.name == "src.tasks.resume_research"
    assert tasks.dead_letter_run.name == "src.tasks.dead_letter_run"


def test_celery_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv("CELERY_ENABLED", raising=False)
    assert celery_enabled() is False
    monkeypatch.setenv("CELERY_ENABLED", "true")
    assert celery_enabled() is True


class _FakeBus:
    def __init__(self):
        self.events = []

    def publish(self, run_id, event):
        self.events.append(event)


class _FakeApp:
    def stream(self, input_, config=None, stream_mode=None):
        yield {
            "supervisor": {
                "symbol": "AAPL",
                "days": 5,
                "outdir": "artifacts/AAPL",
                "report_path": "r.md",
                "plot_path": "c.png",
                "pdf_path": "r.pdf",
            }
        }


def test_runner_execute_success(monkeypatch, tmp_path):
    bus = _FakeBus()
    monkeypatch.setattr(runner, "get_event_bus", lambda: bus)
    monkeypatch.setattr(runner, "build_graph", lambda cfg: _FakeApp())
    monkeypatch.setattr(runner, "precheck_symbol", lambda s: (True, None, None))
    monkeypatch.setattr(runner, "_load_cfg", lambda: {})

    async def fake_update(run_id, **kw):
        return None

    monkeypatch.setattr(runner.db, "update_run", fake_update)

    status, retryable = get_runner().execute("rid1", "AAPL", 5, str(tmp_path))
    assert status == "success"
    assert retryable is False
    assert any(e.get("type") == "done" and e.get("status") == "success" for e in bus.events)


def test_runner_execute_failed_precheck(monkeypatch, tmp_path):
    bus = _FakeBus()
    monkeypatch.setattr(runner, "get_event_bus", lambda: bus)
    monkeypatch.setattr(runner, "build_graph", lambda cfg: _FakeApp())
    monkeypatch.setattr(
        runner, "precheck_symbol", lambda s: (False, "股票代码 BAD 无效或没有当前市场数据。", "请核实代码")
    )

    async def fake_update(run_id, **kw):
        return None

    monkeypatch.setattr(runner.db, "update_run", fake_update)

    status, retryable = get_runner().execute("rid2", "BAD", 5, str(tmp_path))
    assert status == "failed"
    assert retryable is False  # precheck 失败是确定性结果，不应触发重试
    assert any(e.get("type") == "done" and e.get("status") == "failed" for e in bus.events)


def test_runner_execute_invalid_symbol_rejected(monkeypatch, tmp_path):
    bus = _FakeBus()
    monkeypatch.setattr(runner, "get_event_bus", lambda: bus)
    monkeypatch.setattr(runner, "build_graph", lambda cfg: _FakeApp())
    monkeypatch.setattr(runner, "_load_cfg", lambda: {})

    async def fake_update(run_id, **kw):
        return None

    monkeypatch.setattr(runner.db, "update_run", fake_update)

    # 路径注入（../../evil）：worker 侧白名单必须拒绝，绝不写文件
    status, retryable = get_runner().execute("rid3", "../../evil", 5, str(tmp_path))
    assert status == "failed"
    assert retryable is False
    assert any(e.get("type") == "done" and e.get("status") == "failed" for e in bus.events)


def test_runner_transient_error_is_retryable(monkeypatch, tmp_path):
    bus = _FakeBus()
    monkeypatch.setattr(runner, "get_event_bus", lambda: bus)
    monkeypatch.setattr(runner, "precheck_symbol", lambda s: (True, None, None))
    monkeypatch.setattr(runner, "_load_cfg", lambda: {})

    class _BoomApp:
        def stream(self, input_, config=None, stream_mode=None):
            raise RuntimeError("HTTPSConnectionPool read timed out")

    monkeypatch.setattr(runner, "build_graph", lambda cfg: _BoomApp())

    async def fake_update(run_id, **kw):
        return None

    monkeypatch.setattr(runner.db, "update_run", fake_update)

    status, retryable = get_runner().execute("rid4", "AAPL", 5, str(tmp_path))
    assert status == "failed"
    assert retryable is True  # 网络超时类错误才值得重试


def test_dead_letter_marks_run_failed(monkeypatch):
    async def fake_get(run_id):
        return {"run_id": run_id, "error": "boom"}

    async def fake_update(run_id, **kw):
        joined = kw
        assert joined.get("phase") == "dead_letter"
        assert joined.get("status") == "failed"
        return None

    monkeypatch.setattr(tasks.db, "get_run", fake_get)
    monkeypatch.setattr(tasks.db, "update_run", fake_update)
    assert tasks.dead_letter_run("rid9") == {"run_id": "rid9"}
