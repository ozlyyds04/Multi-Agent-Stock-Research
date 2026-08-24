import base64
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

_APP_PATH = Path(__file__).resolve().parents[3] / "src" / "ui" / "streamlit_app.py"
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def artifact_dir(tmp_path):
    (tmp_path / "report.md").write_text("# 测试报告\n", encoding="utf-8")
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "raw.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    (tmp_path / "chart.png").write_bytes(_PNG_1PX)
    return tmp_path


def _payload(tmp_path, symbol="00700"):
    return {
        "status": "success",
        "symbol": symbol,
        "report": str(tmp_path / "report.md"),
        "pdf": str(tmp_path / "report.pdf"),
        "plot": str(tmp_path / "chart.png"),
        "raw": str(tmp_path / "raw.json"),
        "message": "ok",
    }


def test_overview_artifacts_rendered_as_light_badges(artifact_dir):
    payload = _payload(artifact_dir)
    at = AppTest.from_file(str(_APP_PATH), default_timeout=20)
    at.session_state["symbol"] = "00700"
    at.session_state["result"] = payload
    at.session_state["artifact_bytes"] = {
        "symbol": "00700",
        "_paths": {
            "md": payload["report"],
            "pdf": payload["pdf"],
            "chart": payload["plot"],
            "raw": payload["raw"],
        },
    }
    at.run()

    assert at.text_input[0].value == "00700"
    md_vals = [m.value for m in at.markdown]

    # 股票代码数字在白色卡片内（同一 markdown 块）
    assert any(
        'class="card"' in v and "股票代码" in v and "00700" in v for v in md_vals
    )

    # 产物徽章与智能体流程同款浅色样式，且在同一白色卡片内
    artifact_card = next(
        (v for v in md_vals if 'class="card"' in v and "产物" in v and "✓" in v), ""
    )
    assert artifact_card
    for name in ("Markdown", "PDF", "图表", "原始 JSON"):
        assert f"✓ {name}" in artifact_card, name


def test_submit_syncs_normalized_symbol_into_input(artifact_dir, monkeypatch):
    payload = _payload(artifact_dir)

    class FakeResp:
        status_code = 200

        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    monkeypatch.setattr("requests.post", lambda *a, **k: FakeResp(payload))

    at = AppTest.from_file(str(_APP_PATH), default_timeout=20)
    at.session_state["symbol"] = "0700.HK"
    at.run()
    at.text_input[0].set_value("0700.HK")
    at.button[0].click().run()

    assert at.session_state["symbol"] == "00700"
    assert at.text_input[0].value == "00700"


def test_report_renders_collapsible_sections(tmp_path):
    report_md = "# 测试报告\n\n## 1. 市场概览\n- 数据覆盖\n\n## 2. 基本面亮点\n- 财务\n"
    (tmp_path / "report.md").write_text(report_md, encoding="utf-8")
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "raw.json").write_text("{}", encoding="utf-8")
    (tmp_path / "chart.png").write_bytes(_PNG_1PX)

    payload = {
        "status": "success",
        "symbol": "AAPL",
        "report": str(tmp_path / "report.md"),
        "pdf": str(tmp_path / "report.pdf"),
        "plot": str(tmp_path / "chart.png"),
        "raw": str(tmp_path / "raw.json"),
        "message": "ok",
    }

    at = AppTest.from_file(str(_APP_PATH), default_timeout=20)
    at.session_state["symbol"] = "AAPL"
    at.session_state["result"] = payload
    at.session_state["artifact_bytes"] = {
        "symbol": "AAPL",
        "_paths": {
            "md": payload["report"],
            "pdf": payload["pdf"],
            "chart": payload["plot"],
            "raw": payload["raw"],
        },
    }
    at.run()

    labels = [e.label for e in at.expander]
    assert any("▸ 1. 市场概览" in l for l in labels)
    assert any("▸ 2. 基本面亮点" in l for l in labels)
