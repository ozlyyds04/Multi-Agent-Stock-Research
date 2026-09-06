import asyncio

from src.runtime.run_manager import RunManager


class FakeInterrupt:
    def __init__(self, value: dict):
        self.value = value


class FakeApp:
    """模拟 LangGraph app：.stream 根据入参是 dict(执行) 还是 Command(恢复) 返回不同帧。"""

    def __init__(self, execute_frames, resume_frames):
        self.execute_frames = execute_frames
        self.resume_frames = resume_frames

    def stream(self, input_, config=None, stream_mode=None):
        if isinstance(input_, dict):
            yield from self.execute_frames
        else:
            yield from self.resume_frames


def _run_until(mgr, run_id, statuses, timeout=2.0):
    async def _wait():
        for _ in range(int(timeout * 100)):
            snap = mgr.snapshot(run_id)
            if snap and snap["status"] in statuses:
                return snap
            await asyncio.sleep(0.01)
        raise AssertionError(f"等待 {statuses} 超时")

    return asyncio.run(_wait())


def test_run_success(monkeypatch, tmp_path):
    frame = {
        "supervisor": {
            "symbol": "AAPL",
            "days": 5,
            "outdir": str(tmp_path),
            "report_path": "r.md",
            "plot_path": "c.png",
            "pdf_path": "r.pdf",
        }
    }
    monkeypatch.setattr("src.runtime.run_manager.build_graph", lambda cfg: FakeApp([frame], []))
    monkeypatch.setattr("src.runtime.run_manager.precheck_symbol", lambda s: (True, None, None))

    mgr = RunManager()
    run_id = asyncio.run(mgr.submit("AAPL", 5, str(tmp_path)))
    snap = _run_until(mgr, run_id, {"success", "failed"})

    assert snap["status"] == "success"
    assert snap["phase"] == "done"
    assert snap["progress"] == 100
    assert snap["result"]["report"] == "r.md"
    assert snap["result"]["plot"] == "c.png"


def test_run_awaiting_approval_then_resume(monkeypatch, tmp_path):
    analyze = {"analyze": {"symbol": "AAPL", "analyst_note": "NOTE"}}
    interrupt = {"__interrupt__": (FakeInterrupt({"symbol": "AAPL", "draft": "DRAFT", "analyst_note": "NOTE", "round": 1}),)}
    resume = {
        "supervisor": {
            "symbol": "AAPL",
            "days": 5,
            "outdir": str(tmp_path),
            "report_path": "r.md",
            "plot_path": "c.png",
            "pdf_path": "r.pdf",
        }
    }
    monkeypatch.setattr(
        "src.runtime.run_manager.build_graph",
        lambda cfg: FakeApp([analyze, interrupt], [resume]),
    )
    monkeypatch.setattr("src.runtime.run_manager.precheck_symbol", lambda s: (True, None, None))

    mgr = RunManager()
    run_id = asyncio.run(mgr.submit("AAPL", 5, str(tmp_path)))
    snap = _run_until(mgr, run_id, {"awaiting_approval", "failed"})

    assert snap["status"] == "awaiting_approval"
    assert snap["draft"] == "DRAFT"
    assert snap["phase"] == "approval"

    assert asyncio.run(mgr.resume(run_id, {"action": "approve"})) is True
    final = _run_until(mgr, run_id, {"success", "failed"})
    assert final["status"] == "success"
    assert final["result"]["report"] == "r.md"


def test_run_failed_when_precheck_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.runtime.run_manager.precheck_symbol",
        lambda s: (False, "股票代码 BAD 无效或没有当前市场数据。", "请核实代码"),
    )
    monkeypatch.setattr("src.runtime.run_manager.build_graph", lambda cfg: FakeApp([], []))

    mgr = RunManager()
    run_id = asyncio.run(mgr.submit("BAD", 5, str(tmp_path)))
    snap = _run_until(mgr, run_id, {"failed"})

    assert snap["status"] == "failed"
    assert "无效" in (snap["error"] or "")
    assert snap["progress"] == 100


def test_resume_checks_db_status_when_configured(monkeypatch):
    from unittest.mock import MagicMock

    from src.runtime import run_manager as rm

    monkeypatch.setattr(rm.db, "configured", lambda: True)

    async def fake_get_run(run_id):
        return {"run_id": run_id, "status": "awaiting_approval"}

    monkeypatch.setattr(rm.db, "get_run", fake_get_run)
    monkeypatch.setattr(rm, "celery_enabled", lambda: True)
    resume_task = MagicMock()
    monkeypatch.setattr("src.tasks.resume_research", resume_task)

    mgr = rm.RunManager()

    async def inner():
        return await mgr.resume("ridx", {"action": "approve"})

    assert asyncio.run(inner()) is True
    assert resume_task.delay.called
