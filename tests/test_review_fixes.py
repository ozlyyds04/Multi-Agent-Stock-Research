"""针对本次审查修复的回归测试。"""

from src.runtime.memory import InMemoryMemoryStore
from src.tools.alpha_vantage_tool import is_rate_limited_response
from src.tools.math_tool import basic_return_stats
from src.utils.helper import safe_num, is_rate_limit_error


# ---- #1 限流误判：正常行情数字不得被判为限流 ----
def test_rate_limit_response_not_triggered_by_numbers():
    fake_quote = {
        "Global Quote": {
            "01. symbol": "X429",
            "05. price": "1429.43",
            "06. volume": "10429300",
        }
    }
    assert is_rate_limited_response(fake_quote) is False


def test_rate_limit_response_triggered_by_note():
    assert is_rate_limited_response({"Note": "limit reached"}) is True
    assert is_rate_limited_response({"Information": "daily quota"}) is True


def test_is_rate_limit_error_still_works_on_messages():
    assert is_rate_limit_error(ValueError("HTTP 429 Too Many Requests")) is True
    assert is_rate_limit_error(ValueError("no data")) is False


# ---- #2/#4 math_tool：vol 键名 + 非正值过滤 ----
def test_basic_return_stats_returns_vol_and_handles_zero_prices():
    stats = basic_return_stats([10.0, 0.0, 11.0, 12.0])  # 0 会被过滤
    assert "vol" in stats
    assert stats["mean"] is not None
    assert stats["vol"] == stats["vol"]  # 不是 NaN


def test_basic_return_stats_insufficient_data():
    stats = basic_return_stats([10.0])
    assert stats["mean"] is None and stats["vol"] is None


# ---- #3 safe_num：小数精度 + bool/NaN ----
def test_safe_num_decimals_and_bool_nan():
    assert safe_num(6.28, "USD", decimals=2) == "6.28 USD"
    assert safe_num(1234567.0) == "1,234,567"
    assert safe_num(True) == "N/A"
    assert safe_num(float("nan")) == "N/A"
    assert safe_num(None) == "N/A"


# ---- #11 compact 不得误删其他 session 的记忆 ----
def test_memory_compact_prune_keeps_other_sessions():
    store = InMemoryMemoryStore()
    for i in range(60):
        store.add("AAPL", f"aapl-{i}", kind="fact", importance=0.1 + i / 1000)
    for i in range(5):
        store.add("MSFT", f"msft-{i}", kind="fact", importance=0.5)
    res = store.compact("AAPL", max_entries=10, summarizer=None)
    assert res["mode"] == "prune"
    remaining = [e for e in store._entries if e["session_key"] == "MSFT"]
    assert len(remaining) == 5  # 其他股票的记忆必须原样保留
    aapl = [e for e in store._entries if e["session_key"] == "AAPL"]
    assert len(aapl) == 10


# ---- runner：resume 状态复核（worker 侧防双重恢复） ----
def test_runner_resume_rejects_non_awaiting_status(monkeypatch):
    from src.runtime import runner as runner_mod

    bus = _FakeBusForRunner()
    monkeypatch.setattr(runner_mod, "get_event_bus", lambda: bus)

    class R(runner_mod.ResearchRunner):
        def __init__(self):
            super().__init__()
            self._bus = bus

    r = R()
    monkeypatch.setattr(r, "_get_status", lambda rid: "success")
    status, retryable = r.resume("ridx", {"action": "approve"})
    assert status == "skipped"
    assert not any(e.get("type") == "status" for e in bus.events)


def test_runner_resume_proceeds_on_awaiting(monkeypatch):
    from src.runtime import runner as runner_mod

    bus = _FakeBusForRunner()
    monkeypatch.setattr(runner_mod, "get_event_bus", lambda: bus)

    class R(runner_mod.ResearchRunner):
        def __init__(self):
            super().__init__()
            self._bus = bus

    r = R()
    monkeypatch.setattr(r, "_get_status", lambda rid: "awaiting_approval")

    class FakeApp:
        def stream(self, input_, config=None, stream_mode=None):
            yield {"supervisor": {"symbol": "AAPL", "outdir": "x", "report_path": "r.md"}}

    monkeypatch.setattr(runner_mod, "build_graph", lambda cfg: FakeApp())
    monkeypatch.setattr(runner_mod, "_load_cfg", lambda: {})

    async def fake_update(run_id, **kw):
        return None

    monkeypatch.setattr(runner_mod.db, "update_run", fake_update)

    status, retryable = r.resume("ridy", {"action": "approve"})
    assert status == "success"


class _FakeBusForRunner:
    def __init__(self):
        self.events = []

    def publish(self, run_id, event):
        self.events.append(event)


# ---- pdf_tool：图片路径白名单（#18 SSRF/外泄） ----
def test_pdf_image_src_whitelist(tmp_path):
    from src.tools.pdf_tool import _safe_image_src

    base = str(tmp_path)
    inside = tmp_path / "chart.png"
    inside.write_bytes(b"x")

    assert _safe_image_src(str(inside), base) is not None
    assert _safe_image_src("http://evil.com/x.png", base) is None
    assert _safe_image_src("javascript:alert(1)", base) is None
    assert _safe_image_src(str(tmp_path.parent / "outside.png"), base) is None
    assert _safe_image_src(str(tmp_path / "doc.pdf"), base) is None


# ---- plot_tool：失败返回 None，成功路径返回 outpath（#16 修复配套） ----
# conftest 的 autouse fixture 会 fake 掉 save_price_plot；
# 模块顶层（fixture 生效前）拿到原始实现，直接测真实逻辑
from src.tools.plot_tool import save_price_plot as _orig_save_price_plot


def test_plot_success_returns_path(tmp_path):
    out = str(tmp_path / "c.png")
    assert (
        _orig_save_price_plot(["2026-01-0%d" % i for i in range(1, 4)], [10.0, 11.0, 12.0], "USD", out) == out
    )


def test_plot_failure_returns_none(tmp_path, monkeypatch):
    import src.tools.plot_tool as pt

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(pt.plt, "figure", boom)
    assert _orig_save_price_plot(["d1", "d2"], [1.0, 2.0], "USD", str(tmp_path / "c.png")) is None


# ---- storage_tool：文件名路径分隔符拒绝 ----
def test_storage_rejects_path_in_filename(tmp_path):
    import pytest as _pytest
    from src.tools.storage_tool import save_json

    with _pytest.raises(ValueError):
        save_json({}, str(tmp_path), "../evil.json")
