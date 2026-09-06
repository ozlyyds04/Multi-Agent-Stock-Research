"""
Worker 侧的研究执行器：由 Celery 任务调用。
与 API 侧的 RunManager 相互独立（RunManager 处理 HTTP/SSE/内存状态），
Celery worker 通过它执行图、把节点事件发到事件总线（Redis，跨进程可见）、更新 asyncpg 状态。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from src.graph.orchestrator import build_graph, _load_cfg
from src.graph.precheck import precheck_symbol
from src.utils.log_context import set_log_context
from src.utils.logger import get_logger
from src.runtime import db
from src.runtime.backends import get_event_bus
from src.runtime.graph_stream import stream_graph
from src.observability import metrics

logger = get_logger(__name__)

# symbol 白名单：worker 的入参来自 Celery 消息体（broker 可能无认证），必须再校验一次
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")


def _is_transient_error(msg: str) -> bool:
    low = (msg or "").lower()
    return any(
        k in low
        for k in ("timeout", "timed out", "connection", "temporarily", "reset by peer", "502", "503", "504")
    )


class ResearchRunner:
    def __init__(self):
        self._bus = get_event_bus()

    def _emit(self, run_id: str, event: Dict[str, Any]) -> None:
        try:
            self._bus.publish(run_id, event)
        except Exception as e:
            logger.warning("事件发布失败：%s", e)

    def _status(self, run_id: str, **fields: Any) -> None:
        try:
            db.run_sync(db.update_run(run_id, **fields))
        except Exception as e:
            logger.warning("runs 状态更新失败：%s", e)

    def _get_status(self, run_id: str) -> Optional[str]:
        try:
            row = db.run_sync(db.get_run(run_id))
            return (row or {}).get("status")
        except Exception as e:
            logger.warning("runs 状态读取失败：%s", e)
            return None

    def _precheck(self, symbol: str) -> tuple:
        return precheck_symbol(symbol)

    def _validate_paths(self, run_id: str, symbol: str, outdir: str) -> Optional[str]:
        """入参来自 Celery 消息体，必须在执行前做路径白名单校验。"""
        if not _SYMBOL_RE.match(symbol or ""):
            reason = f"非法的股票代码：{symbol!r}"
            logger.error("run %s %s", run_id, reason)
            return reason
        return None

    def execute(self, run_id: str, symbol: str, days: int, outdir: str, human: bool = False) -> tuple:
        """返回 (status, retryable)。retryable=True 表示基础设施类错误（网络/超时），值得重试。"""
        set_log_context(run_id=run_id, symbol=symbol)
        invalid = self._validate_paths(run_id, symbol, outdir)
        if invalid:
            self._fail(run_id, invalid)
            return "failed", False

        self._status(run_id, status="running", phase="precheck")
        self._emit(run_id, {"type": "status", "status": "running", "phase": "precheck"})

        try:
            ok, error, suggested = self._precheck(symbol)
        except Exception as e:
            ok, error, suggested = False, f"预检请求失败：{e}", None
        if not ok:
            self._fail(run_id, error, suggested)
            return "failed", _is_transient_error(error or "")

        cfg = _load_cfg()
        app = build_graph(cfg)
        outdir = os.path.join(outdir, symbol)
        initial = {
            "symbol": symbol,
            "days": days,
            "outdir": outdir,
            "run_id": run_id,
            "require_approval": human,
        }
        config = {"configurable": {"thread_id": run_id}}
        try:
            error_holder: Dict[str, str] = {}
            status = self._stream(run_id, app, initial, config, error_holder)
            retryable = status == "failed" and _is_transient_error(error_holder.get("error", ""))
            return status, retryable
        except Exception as e:
            logger.exception("run %s 执行异常", run_id)
            return self._fail(run_id, f"流水线执行失败：{e}"), _is_transient_error(str(e))

    def resume(self, run_id: str, feedback: Any) -> tuple:
        from langgraph.types import Command

        set_log_context(run_id=run_id)
        # worker 侧二次校验：API 侧的原子抢占之后，这里只接受仍处于待审批/恢复中的任务，
        # 防止重复投递的 resume 任务对同一 thread_id 双重恢复
        current = self._get_status(run_id)
        if current not in ("awaiting_approval", "resuming", "running"):
            logger.warning(
                "run %s 不在可恢复状态（status=%s），忽略重复的恢复请求。", run_id, current
            )
            return "skipped", False

        self._status(run_id, status="running", phase="resume")
        self._emit(run_id, {"type": "status", "status": "running", "phase": "resume"})
        cfg = _load_cfg()
        app = build_graph(cfg)
        config = {"configurable": {"thread_id": run_id}}
        try:
            error_holder: Dict[str, str] = {}
            status = self._stream(run_id, app, Command(resume=feedback), config, error_holder)
            retryable = status == "failed" and _is_transient_error(error_holder.get("error", ""))
            return status, retryable
        except Exception as e:
            logger.exception("run %s 审批恢复异常", run_id)
            return self._fail(run_id, f"审批恢复失败：{e}"), _is_transient_error(str(e))

    def _stream(self, run_id: str, app: Any, input_: Any, config: Dict[str, Any], error_holder: Dict[str, str]) -> str:
        status = stream_graph(
            app,
            input_,
            config,
            run_id,
            emit=lambda rid, ev: self._emit(rid, ev),
            set_status=lambda rid, **kw: self._status(rid, **kw),
            error_holder=error_holder,
        )
        if status == "success":
            metrics.inc_run_status("success")
        elif status == "failed":
            metrics.inc_run_status("failed")
        return status

    def _fail(self, run_id: str, reason: Optional[str], suggested_action: Optional[str] = None) -> str:
        self._status(
            run_id,
            status="failed",
            phase="error",
            progress=100,
            error=reason,
            suggested_action=suggested_action,
            result=None,
        )
        self._emit(run_id, {"type": "done", "status": "failed", "error": reason, "suggested_action": suggested_action})
        metrics.inc_run_status("failed")
        return "failed"


def get_runner() -> ResearchRunner:
    return ResearchRunner()
