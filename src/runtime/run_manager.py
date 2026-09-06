from __future__ import annotations

import asyncio
import concurrent.futures as cf
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.graph.orchestrator import build_graph, _load_cfg
from src.graph.precheck import precheck_symbol
from src.utils.log_context import set_log_context
from src.utils.logger import get_logger
from src.runtime import db
from src.runtime.backends import get_event_bus
from src.runtime.graph_stream import stream_graph
from src.observability import metrics
from src.celery_app import celery_enabled

logger = get_logger(__name__)


_TERMINAL_STATUSES = {"success", "failed"}
_RUN_TTL_SEC = 24 * 3600
_RUN_MAX = 500
# 进程内执行的全局兜底超时：settings.yaml 的 timeout_sec 只有 90-170s，
# 这里取更宽裕的值，仅用于拦截彻底挂死的任务
_GLOBAL_TIMEOUT_SEC = 15 * 60


@dataclass
class RunState:
    run_id: str
    symbol: str
    days: int
    outdir: str
    human: bool
    status: str = "pending"
    phase: str = ""
    progress: int = 0
    draft: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    suggested_action: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class RunManager:
    """
    异步运行时代理：
      - submit(): 创建 run 记录（asyncpg）+ 生成 run_id + 后台线程执行；
      - 后台线程用 app.stream(stream_mode="updates") 产出节点事件，桥接到 asyncio 队列；
      - status 用 LangGraph checkpoint 持久化（跨进程可恢复），runs 注册表用 asyncpg；
      - /stream 订阅者通过 asyncio.Queue 实时接收事件，并先回放已产生的事件。
    未配置 DATABASE_URL 时 runs 注册表走纯内存（本地/测试友好）。
    """

    def __init__(self, max_workers: int = 4):
        self.runs: Dict[str, RunState] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._executor = cf.ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()
        self._event_bus = get_event_bus()

    # ---- 对外接口（异步） ----
    async def submit(self, symbol: str, days: int, outdir: str, human: bool = False) -> str:
        self._loop = asyncio.get_running_loop()
        self._event_bus.set_loop(self._loop)
        run_id = str(uuid.uuid4())
        state = RunState(run_id=run_id, symbol=symbol, days=days, outdir=outdir, human=human)
        with self._lock:
            self.runs[run_id] = state
        self._maybe_evict()
        metrics.ACTIVE_RUNS.inc()
        try:
            await db.create_run(run_id, symbol, days, outdir, human)
        except Exception as e:
            logger.warning("asyncpg create_run 失败（忽略，走内存）：%s", e)
        try:
            if celery_enabled():
                from src.tasks import run_research as run_task

                _fail_fast = []
                if not (os.getenv("REDIS_URL") or "").strip():
                    # broker 回退到 127.0.0.1 默认值，但事件总线要求显式 REDIS_URL：
                    # 缺失时 worker 能跑，API 进程的 SSE 却收不到事件、查询不到状态
                    _fail_fast.append("REDIS_URL 未配置（SSE 事件将无法跨进程传递）")
                if not db.configured():
                    _fail_fast.append("DATABASE_URL 未配置（跨进程状态不可查询）")
                if _fail_fast:
                    logger.error(
                        "CELERY_ENABLED=true 但前置条件缺失，回退进程内执行：%s", "；".join(_fail_fast)
                    )
                    self._executor.submit(self._execute, run_id)
                else:
                    run_task.delay(run_id, symbol, days, outdir, human)
            else:
                self._executor.submit(self._execute, run_id)
        except Exception as e:
            # 派发失败（如 broker 不可达）：回滚状态，否则 run 永远停在 pending
            logger.exception("run %s 派发失败", run_id)
            metrics.ACTIVE_RUNS.dec()
            with self._lock:
                state.status = "failed"
                state.error = f"任务派发失败：{e}"
                state.updated_at = time.time()
            self._emit(run_id, {"type": "done", "status": "failed", "error": state.error})
        return run_id

    def _maybe_evict(self) -> None:
        """限制进程内 runs 大小：过期的以及超出上限的旧记录会被清理，避免长期运行内存泄漏。"""
        now = time.time()
        cutoff = now - _RUN_TTL_SEC
        with self._lock:
            for rid, s in list(self.runs.items()):
                if s.updated_at < cutoff and s.status not in ("running", "awaiting_approval", "resuming"):
                    # 活跃状态（含待审批，其 updated_at 停在审批时刻）不参与 TTL 逐出，
                    # 否则审批恢复时状态更新会被静默丢弃
                    self.runs.pop(rid, None)
            if len(self.runs) > _RUN_MAX:
                for rid, s in sorted(self.runs.items(), key=lambda kv: kv[1].updated_at):
                    if len(self.runs) <= _RUN_MAX:
                        break
                    if s.status in ("running", "awaiting_approval", "resuming"):
                        continue  # 进行中/待审批的任务不清理
                    self.runs.pop(rid, None)

    def get(self, run_id: str) -> Optional[RunState]:
        return self.runs.get(run_id)

    def snapshot(self, run_id: str) -> Optional[Dict[str, Any]]:
        state = self.runs.get(run_id)
        if not state:
            return None
        return self._snapshot_state(state)

    async def list_runs(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        """列出最近的任务（有 asyncpg 则读库，否则读进程内 runs）。"""
        if db.configured():
            try:
                return await db.list_runs(limit, offset)
            except Exception as e:
                logger.warning("asyncpg list_runs 失败，回退内存：%s", e)
        with self._lock:
            items = sorted(self.runs.values(), key=lambda s: s.created_at, reverse=True)[offset : offset + limit]
            return [self._snapshot_state(s) for s in items]

    @staticmethod
    def _snapshot_state(state: RunState) -> Dict[str, Any]:
        return {
            "run_id": state.run_id,
            "symbol": state.symbol,
            "days": state.days,
            "human": state.human,
            "status": state.status,
            "phase": state.phase,
            "progress": state.progress,
            "draft": state.draft,
            "result": state.result,
            "error": state.error,
            "suggested_action": state.suggested_action,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
        }

    async def resume(self, run_id: str, feedback: Any) -> bool:
        # Celery 模式下任务由 worker 执行，权威状态在 asyncpg；未配置库时看进程内状态。
        # 两侧都必须做原子抢占（check-then-act 会让并发审批请求双重恢复同一图）。
        state = self.runs.get(run_id)
        if db.configured():
            try:
                claimed = await db.claim_resume(run_id)
            except Exception as e:
                logger.warning("run %s 抢占恢复失败，回退内存判断：%s", run_id, e)
                claimed = None
            if claimed is False:
                # db 行存在但不在待审批状态：拒绝（即便内存态过期显示 awaiting）
                try:
                    row_status = (await db.get_run(run_id) or {}).get("status")
                except Exception:
                    row_status = None
                logger.warning("run %s 不在等待审批状态（db status=%s），拒绝恢复。", run_id, row_status)
                return False
            if claimed is None and state is not None:
                # db 不可用或行不存在：回退内存抢占
                with self._lock:
                    if state.status != "awaiting_approval":
                        return False
                    state.status = "resuming"
        else:
            if not state or state.status != "awaiting_approval":
                return False
            with self._lock:
                if state.status != "awaiting_approval":
                    return False
                state.status = "resuming"
        try:
            if celery_enabled():
                from src.tasks import resume_research as resume_task

                resume_task.delay(run_id, feedback)
            else:
                self._executor.submit(self._resume, run_id, feedback)
        except Exception as e:
            logger.exception("run %s 恢复派发失败", run_id)
            # 抢占成功但派发失败：回滚为 awaiting_approval，允许重新审批
            if state:
                with self._lock:
                    state.status = "awaiting_approval"
                    state.updated_at = time.time()
            self._emit(run_id, {"type": "status", "status": "awaiting_approval", "phase": "approval"})
            return False
        return True

    def stream(self, run_id: str):
        """暴露事件流（回放已产生的事件 + 实时事件，直到 done），供 SSE 消费。"""
        return self._event_bus.stream(run_id)

    # ---- 后台执行 ----
    def _watchdog_start(self, run_id: str) -> threading.Timer:
        """
        全局看门狗：图节点挂死（如 LLM/网络卡住）时线程无法被强杀，
        但至少要把 run 标记为 failed 并发出终态事件，避免 UI 永远等待、
        ACTIVE_RUNS 永不回落。挂起的 executor 线程会随任务结束自行释放。
        """
        timeout = _GLOBAL_TIMEOUT_SEC

        def _fire():
            state = self.runs.get(run_id)
            if state and state.status in ("running", "pending", "resuming"):
                logger.error("[run:%s] 执行超过 %d 秒，看门狗强制标记失败。", run_id, timeout)
                self._fail(run_id, f"执行超过 {timeout} 秒未完成，已被强制终止。", "请减少天数或稍后重试。")

        t = threading.Timer(timeout, _fire)
        t.daemon = True
        t.start()
        return t

    def _execute(self, run_id: str) -> None:
        state = self.runs.get(run_id)
        if not state:
            return
        set_log_context(run_id=run_id, symbol=state.symbol)
        logger.info("[run:%s] 开始异步执行：symbol=%s", run_id, state.symbol)
        self._set_status(run_id, status="running", phase="precheck")
        self._emit(run_id, {"type": "status", "status": "running", "phase": "precheck"})

        watchdog = self._watchdog_start(run_id)
        try:
            ok, error, suggested = self._precheck(state.symbol)
            if not ok:
                self._fail(run_id, error, suggested)
                return

            cfg = _load_cfg()
            app = build_graph(cfg)
            outdir = os.path.join(state.outdir, state.symbol)
            initial: Dict[str, Any] = {
                "symbol": state.symbol,
                "days": state.days,
                "outdir": outdir,
                "run_id": run_id,
                "require_approval": state.human,
            }
            config = {"configurable": {"thread_id": run_id}}
            self._stream_run(run_id, app, initial, config)
        except Exception as e:
            logger.exception("[run:%s] 异步执行异常", run_id)
            self._fail(run_id, f"流水线执行失败：{e}")
        finally:
            watchdog.cancel()

    def _resume(self, run_id: str, feedback: Any) -> None:
        state = self.runs.get(run_id)
        if not state:
            return
        set_log_context(run_id=run_id, symbol=state.symbol)
        logger.info("[run:%s] 从审批边恢复：%s", run_id, feedback)
        self._set_status(run_id, status="running", phase="resume")
        self._emit(run_id, {"type": "status", "status": "running", "phase": "resume"})

        cfg = _load_cfg()
        app = build_graph(cfg)
        config = {"configurable": {"thread_id": run_id}}
        from langgraph.types import Command

        watchdog = self._watchdog_start(run_id)
        try:
            self._stream_run(run_id, app, Command(resume=feedback), config)
        except Exception as e:
            logger.exception("[run:%s] 审批恢复异常", run_id)
            self._fail(run_id, f"审批恢复失败：{e}")
        finally:
            watchdog.cancel()

    def _stream_run(
        self,
        run_id: str,
        app: Any,
        input_: Any,
        config: Dict[str, Any],
    ) -> None:
        state = self.runs.get(run_id)
        if not state:
            return
        status = stream_graph(
            app,
            input_,
            config,
            run_id,
            emit=lambda rid, ev: self._emit(rid, ev),
            set_status=lambda rid, **kw: self._set_status(rid, **kw),
        )
        if status == "success":
            metrics.inc_run_status("success")
            metrics.ACTIVE_RUNS.dec()
        elif status == "awaiting_approval":
            metrics.inc_run_status("awaiting_approval")
        elif status == "failed":
            metrics.inc_run_status("failed")
            metrics.ACTIVE_RUNS.dec()

    # ---- 预检 ----
    def _precheck(self, symbol: str) -> Tuple[bool, Optional[str], Optional[str]]:
        return precheck_symbol(symbol)

    # ---- 内部状态/事件 ----
    def _set_status(self, run_id: str, **fields: Any) -> None:
        state = self.runs.get(run_id)
        if not state:
            # 状态被逐出后仍收到更新（理论上不应发生）：至少留痕，便于排障
            logger.warning("[run:%s] 状态更新被丢弃：进程内 run 记录不存在。", run_id)
            return
        with self._lock:
            for key, value in fields.items():
                setattr(state, key, value)
            state.updated_at = time.time()
        self._persist(run_id)

    def _fail(self, run_id: str, reason: Optional[str], suggested_action: Optional[str] = None) -> None:
        self._set_status(
            run_id,
            status="failed",
            phase="error",
            progress=100,
            error=reason,
            suggested_action=suggested_action,
            result=None,
        )
        self._emit(
            run_id,
            {"type": "done", "status": "failed", "error": reason, "suggested_action": suggested_action},
        )
        metrics.inc_run_status("failed")
        metrics.ACTIVE_RUNS.dec()

    def _emit(self, run_id: str, event: Dict[str, Any]) -> None:
        state = self.runs.get(run_id)
        if not state:
            return
        with self._lock:
            state.updated_at = time.time()
        try:
            self._event_bus.publish(run_id, event)
        except Exception as e:
            logger.warning("[run:%s] 事件发布失败（忽略）：%s", run_id, e)

    def _persist(self, run_id: str) -> None:
        if not self._loop:
            return
        if not db.configured():
            return
        state = self.runs.get(run_id)
        if not state:
            return
        coro = db.update_run(
            run_id,
            status=state.status,
            phase=state.phase,
            progress=state.progress,
            draft=state.draft,
            result=state.result,
            error=state.error,
            suggested_action=state.suggested_action,
        )
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception as e:
            coro.close()
            logger.warning("[run:%s] asyncpg 状态写入失败（忽略）：%s", run_id, e)


RUN_MANAGER = RunManager()
