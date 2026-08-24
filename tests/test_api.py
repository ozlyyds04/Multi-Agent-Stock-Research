from fastapi.testclient import TestClient
from src.api import app

client = TestClient(app)


def test_health_check():
    r = client.get("/health")
    assert r.status_code == 200
    assert "service" in r.json()


def _fake_pipeline_result():
    return {
        "report": "artifacts/AAPL/report.md",
        "plot": "artifacts/AAPL/chart.png",
        "raw": "artifacts/AAPL/raw.json",
        "pdf": "artifacts/AAPL/report.pdf",
    }


def test_analyze_endpoint(monkeypatch, tmp_path):
    # 契约测试：预检与流水线均离线 mock，不依赖外部行情/新闻配额
    monkeypatch.setattr(
        "src.api.fetch_quote",
        lambda symbol: {"ok": True, "valid": True, "symbol": symbol, "price": "200.0"},
    )
    monkeypatch.setattr(
        "src.api.run_pipeline",
        lambda symbol, days, outdir, human=False: _fake_pipeline_result(),
    )

    payload = {"symbol": "AAPL", "days": 5, "outdir": tmp_path.as_posix()}
    r = client.post("/analyze", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert "report_path" in data
    assert "raw_data" in data


def test_analyze_endpoint_normalizes_hk_symbol(monkeypatch, tmp_path):
    seen = {}

    def fake_quote(symbol):
        seen["quote"] = symbol
        return {"ok": True, "valid": True, "symbol": symbol, "price": "440.0"}

    def fake_run(symbol, days, outdir, human=False):
        seen["run"] = symbol
        return _fake_pipeline_result()

    monkeypatch.setattr("src.api.fetch_quote", fake_quote)
    monkeypatch.setattr("src.api.run_pipeline", fake_run)

    r = client.post("/analyze", json={"symbol": "0700.HK", "days": 5, "outdir": tmp_path.as_posix()})
    assert r.status_code == 200
    assert seen["quote"] == "00700"
    assert seen["run"] == "00700"


def test_analyze_rejects_invalid_symbol():
    r = client.post("/analyze", json={"symbol": "!!!", "days": 5})
    assert r.status_code == 400


def test_analyze_rejects_days_out_of_range():
    r = client.post("/analyze", json={"symbol": "AAPL", "days": 30})
    assert r.status_code == 400


def test_analyze_precheck_error_returns_400(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.api.fetch_quote",
        lambda symbol: {"ok": False, "rate_limited": False, "message": "上游行情接口错误"},
    )
    r = client.post("/analyze", json={"symbol": "AAPL", "days": 5, "outdir": tmp_path.as_posix()})
    assert r.status_code == 400


def test_analyze_precheck_exception_returns_500(monkeypatch, tmp_path):
    def boom(_symbol):
        raise RuntimeError("预检异常")

    monkeypatch.setattr("src.api.fetch_quote", boom)
    r = client.post("/analyze", json={"symbol": "AAPL", "days": 5, "outdir": tmp_path.as_posix()})
    assert r.status_code == 500


def test_analyze_pipeline_error_returns_400(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.api.fetch_quote",
        lambda symbol: {"ok": True, "valid": True, "symbol": symbol, "price": "1.0"},
    )
    monkeypatch.setattr(
        "src.api.run_pipeline",
        lambda *a, **k: {"status": "error", "symbol": "AAPL", "reason": "严格模式中止", "suggested_action": "x"},
    )
    r = client.post("/analyze", json={"symbol": "AAPL", "days": 5, "outdir": tmp_path.as_posix()})
    assert r.status_code == 400


def test_approve_endpoint_success(monkeypatch):
    monkeypatch.setattr(
        "src.api.resume_pipeline",
        lambda run_id, feedback: {
            "status": "success",
            "run_id": run_id,
            "symbol": "00700",
            "report": "a.md",
            "plot": "b.png",
            "raw": "c.json",
            "pdf": "d.pdf",
        },
    )
    r = client.post("/analyze/approve", json={"run_id": "abc123", "action": "approve"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert "report" in data


def test_approve_endpoint_invalid_action():
    r = client.post("/analyze/approve", json={"run_id": "abc123", "action": "bogus"})
    assert r.status_code == 400
