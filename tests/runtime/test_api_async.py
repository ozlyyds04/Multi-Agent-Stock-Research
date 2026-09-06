import time

from fastapi.testclient import TestClient

from src.api import app
from src.runtime import run_manager as rm


class _Interrupt:
    def __init__(self, value: dict):
        self.value = value


class _FakeApp:
    def __init__(self, execute_frames, resume_frames):
        self.execute_frames = execute_frames
        self.resume_frames = resume_frames

    def stream(self, input_, config=None, stream_mode=None):
        if isinstance(input_, dict):
            yield from self.execute_frames
        else:
            yield from self.resume_frames


def _success_frame(tmp_path):
    return {
        "supervisor": {
            "symbol": "AAPL",
            "days": 5,
            "outdir": str(tmp_path),
            "report_path": f"{tmp_path}/report.md",
            "plot_path": f"{tmp_path}/chart.png",
            "pdf_path": f"{tmp_path}/report.pdf",
        }
    }


def _mock_success(monkeypatch, tmp_path):
    monkeypatch.setattr(rm, "build_graph", lambda cfg: _FakeApp([_success_frame(tmp_path)], []))
    monkeypatch.setattr(rm, "precheck_symbol", lambda s: (True, None, None))


def _wait_status(client, run_id, statuses, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rr = client.get(f"/api/research/{run_id}")
        if rr.status_code == 200 and rr.json()["run"]["status"] in statuses:
            return rr.json()["run"]
        time.sleep(0.01)
    raise AssertionError(f"等待 {statuses} 超时")


def test_submit_returns_run_id_immediately(monkeypatch, tmp_path):
    _mock_success(monkeypatch, tmp_path)
    client = TestClient(app)
    r = client.post("/api/research", json={"symbol": "AAPL", "days": 5, "outdir": str(tmp_path)})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "accepted"
    assert body["run_id"]
    assert body["symbol"] == "AAPL"


def test_submit_rejects_invalid(monkeypatch, tmp_path):
    _mock_success(monkeypatch, tmp_path)
    client = TestClient(app)
    assert client.post("/api/research", json={"symbol": "!!!", "days": 5}).status_code == 400
    assert client.post("/api/research", json={"symbol": "AAPL", "days": 30}).status_code == 400


def test_status_reaches_success_and_stream_closes(monkeypatch, tmp_path):
    _mock_success(monkeypatch, tmp_path)
    client = TestClient(app)
    run_id = client.post("/api/research", json={"symbol": "AAPL", "days": 5, "outdir": str(tmp_path)}).json()["run_id"]
    snap = _wait_status(client, run_id, {"success", "failed"})
    assert snap["status"] == "success"
    assert "report" in snap["result"]

    stream = client.get(f"/api/research/{run_id}/stream")
    assert stream.status_code == 200
    assert '"type": "node"' in stream.text or '"type": "awaiting_approval"' in stream.text
    assert '"type": "done"' in stream.text
    assert '"status": "success"' in stream.text


def test_decision_requires_awaiting_approval(monkeypatch, tmp_path):
    analyze = {"analyze": {"symbol": "AAPL", "analyst_note": "NOTE"}}
    interrupt = {"__interrupt__": (_Interrupt({"symbol": "AAPL", "draft": "DRAFT", "analyst_note": "NOTE", "round": 1}),)}
    resume = _success_frame(tmp_path)
    monkeypatch.setattr(rm, "build_graph", lambda cfg: _FakeApp([analyze, interrupt], [resume]))
    monkeypatch.setattr(rm, "precheck_symbol", lambda s: (True, None, None))

    client = TestClient(app)
    run_id = client.post("/api/research", json={"symbol": "AAPL", "days": 5, "outdir": str(tmp_path)}).json()["run_id"]
    snap = _wait_status(client, run_id, {"awaiting_approval", "failed"})
    assert snap["status"] == "awaiting_approval"
    assert snap["draft"] == "DRAFT"

    # 非法动作
    r = client.post(f"/api/research/{run_id}/decision", json={"action": "bogus"})
    assert r.status_code == 400

    # 批准恢复
    r = client.post(f"/api/research/{run_id}/decision", json={"action": "approve"})
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"
    final = _wait_status(client, run_id, {"success", "failed"})
    assert final["status"] == "success"


def test_list_research_endpoint(monkeypatch, tmp_path):
    _mock_success(monkeypatch, tmp_path)
    client = TestClient(app)
    run_id = client.post("/api/research", json={"symbol": "AAPL", "days": 5, "outdir": str(tmp_path)}).json()["run_id"]
    _wait_status(client, run_id, {"success", "failed"})

    r = client.get("/api/research")
    assert r.status_code == 200
    ids = [x["run_id"] for x in r.json()["runs"]]
    assert run_id in ids
