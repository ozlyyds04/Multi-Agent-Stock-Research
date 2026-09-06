"""LangGraph 流式执行器的共享实现：供进程内 RunManager 与 Celery ResearchRunner 复用。"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


_NODE_PROGRESS: Dict[str, int] = {
    "collect_data": 10,
    "repair_data": 25,
    "validate_data": 35,
    "analyze": 50,
    "compliance": 65,
    "approval": 80,
    "supervisor": 90,
}


def _make_result(snap: Dict[str, Any]) -> Dict[str, Any]:
    symbol = snap.get("symbol") or ""
    outdir = snap.get("outdir") or ""
    return {
        "report": snap.get("report_path"),
        "plot": snap.get("plot_path"),
        "pdf": snap.get("pdf_path"),
        "raw": os.path.join(outdir, f"{symbol}_raw.json") if outdir and symbol else None,
        "memory_read": [
            {"content": r.get("content"), "score": r.get("score")} for r in (snap.get("memory_read") or [])
        ],
        "memory_written": int(snap.get("memory_written") or 0),
        "memory_recalled": int(snap.get("memory_recalled") or 0),
        "memory_compacted": int(snap.get("memory_compacted") or 0),
    }


def stream_graph(
    app: Any,
    input_: Any,
    config: Dict[str, Any],
    run_id: str,
    emit: Callable[[str, Dict[str, Any]], None],
    set_status: Callable[..., None],
    error_holder: Optional[Dict[str, str]] = None,
) -> str:
    """执行图并发布节点事件、写状态，返回终态（success / awaiting_approval / failed）。

    error_holder（可选）：失败时把失败原因写入该 dict，供调用方判断是否为可重试的瞬时错误。
    """
    try:
        last_error = None
        for item in app.stream(input_, config=config, stream_mode="updates"):
            if not isinstance(item, dict):
                continue
            if "__interrupt__" in item:
                intr = item["__interrupt__"]
                payload = intr[0].value if intr and hasattr(intr[0], "value") else (intr[0] if intr else {})
                draft = payload.get("draft") if isinstance(payload, dict) else str(payload)
                set_status(run_id, status="awaiting_approval", phase="approval", progress=80, draft=draft)
                emit(
                    run_id,
                    {"type": "awaiting_approval", "status": "awaiting_approval", "phase": "approval", "draft": draft},
                )
                return "awaiting_approval"

            node = next(iter(item.keys()))
            snap = item[node] if isinstance(item[node], dict) else {}
            if snap.get("error"):
                last_error = str(snap["error"])
            prog = _NODE_PROGRESS.get(node, 50)
            set_status(run_id, status="running", phase=node, progress=prog)
            emit(run_id, {"type": "node", "node": node, "phase": node, "status": "running", "progress": prog})

            if snap.get("report_path"):
                result = _make_result(snap)
                set_status(run_id, status="success", phase="done", progress=100, result=result)
                emit(run_id, {"type": "done", "status": "success", "result": result})
                return "success"

        return _fail(set_status, emit, run_id, last_error or "流水线执行结束但未生成报告。", _err=error_holder)
    except Exception as e:
        logger.exception("run %s 节点流处理异常", run_id)
        return _fail(set_status, emit, run_id, f"流水线执行失败：{e}", _err=error_holder)


def _fail(
    set_status: Callable,
    emit: Callable,
    run_id: str,
    reason: str,
    suggested: str | None = None,
    _err: Optional[Dict[str, str]] = None,
) -> str:
    if _err is not None:
        _err["error"] = reason
    set_status(
        run_id, status="failed", phase="error", progress=100, error=reason, suggested_action=suggested, result=None
    )
    emit(run_id, {"type": "done", "status": "failed", "error": reason, "suggested_action": suggested})
    return "failed"
