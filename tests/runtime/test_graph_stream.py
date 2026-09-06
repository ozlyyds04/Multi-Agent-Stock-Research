from src.runtime.graph_stream import stream_graph


class _App:
    def __init__(self, frames):
        self.frames = frames

    def stream(self, input_, config=None, stream_mode=None):
        yield from self.frames


class _Interrupt:
    def __init__(self, value):
        self.value = value


def _success_frame():
    return {
        "supervisor": {
            "symbol": "AAPL",
            "days": 5,
            "outdir": "artifacts/AAPL",
            "report_path": "r.md",
            "plot_path": "c.png",
            "pdf_path": "r.pdf",
            "memory_read": [],
            "memory_written": 1,
            "memory_recalled": 0,
            "memory_compacted": 0,
        }
    }


def test_stream_graph_success():
    events, statuses = [], []

    def emit(rid, ev):
        events.append(ev)

    def status(rid, **kw):
        statuses.append(kw)

    st = stream_graph(_App([_success_frame()]), {}, {"configurable": {"thread_id": "x"}}, "x", emit, status)
    assert st == "success"
    assert any(e["type"] == "done" and e["status"] == "success" for e in events)
    assert any(k.get("status") == "success" and k.get("result", {}).get("report") == "r.md" for k in statuses)


def test_stream_graph_awaiting():
    events = []

    def emit(rid, ev):
        events.append(ev)

    def status(rid, **kw):
        pass

    st = stream_graph(
        _App([{"__interrupt__": (_Interrupt({"draft": "DRAFT"}),)}]),
        {},
        {"configurable": {"thread_id": "y"}},
        "y",
        emit,
        status,
    )
    assert st == "awaiting_approval"
    assert any(e["type"] == "awaiting_approval" and e["draft"] == "DRAFT" for e in events)


def test_stream_graph_failed_when_no_report():
    def emit(rid, ev):
        pass

    def status(rid, **kw):
        pass

    st = stream_graph(_App([]), {}, {"configurable": {"thread_id": "z"}}, "z", emit, status)
    assert st == "failed"


def test_precheck_symbol_ok(monkeypatch):
    from src.graph.precheck import precheck_symbol
    import src.graph.precheck as pc

    monkeypatch.setattr(pc, "fetch_quote", lambda s: {"ok": True, "valid": True})
    assert precheck_symbol("AAPL") == (True, None, None)


def test_precheck_symbol_invalid(monkeypatch):
    from src.graph import precheck as pc

    monkeypatch.setattr(pc, "fetch_quote", lambda s: {"ok": True, "valid": False})
    ok, err, _sug = pc.precheck_symbol("BAD")
    assert ok is False
    assert "无效" in err
