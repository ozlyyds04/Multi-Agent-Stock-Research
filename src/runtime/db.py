from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any, Dict, List, Optional

import asyncpg

from src.utils.logger import get_logger

logger = get_logger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_runs (
  run_id           TEXT PRIMARY KEY,
  symbol           TEXT NOT NULL,
  days             INTEGER NOT NULL,
  outdir           TEXT NOT NULL,
  human            BOOLEAN NOT NULL DEFAULT FALSE,
  status           TEXT NOT NULL DEFAULT 'pending',
  phase            TEXT NOT NULL DEFAULT '',
  progress         INTEGER NOT NULL DEFAULT 0,
  draft            TEXT,
  result           JSONB,
  error            TEXT,
  suggested_action TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_research_runs_created_at
  ON research_runs(created_at DESC);
"""

_pool: Optional[asyncpg.Pool] = None
_dsn: Optional[str] = None
_pool_loop = None
_pool_lock: Optional[asyncio.Lock] = None
_schema_ready = False


def _memory_mode() -> bool:
    """与 checkpointer 一致：CHECKPOINTER=memory 时 runs 注册表也走纯内存，便于本地/测试。"""
    return os.getenv("CHECKPOINTER", "").lower() in ("memory", "mem")


def configured() -> bool:
    """是否配置了 DATABASE_URL（未配置时运行时走纯内存，便于测试/本地）。"""
    return bool((os.getenv("DATABASE_URL") or "").strip()) and not _memory_mode()


async def _get_pool_lock() -> asyncio.Lock:
    """按当前 loop 惰性创建锁，避免并发首调各自建池。"""
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


async def get_pool() -> Optional[asyncpg.Pool]:
    """
    返回 asyncpg 连接池（幂等）。未配置 DATABASE_URL 或连接失败时返回 None，表示仅内存存储。
    """
    global _pool, _dsn, _pool_loop, _schema_ready
    running_loop = None
    try:
        import asyncio

        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn or _memory_mode():
        return None
    # 跨 asyncio.run 边界时，池绑定在旧 loop 上，需要重建（Celery worker 场景）
    if _pool is None or _dsn != dsn or (running_loop is not None and _pool_loop is not running_loop):
        lock = await _get_pool_lock()
        async with lock:
            # 双重检查：等待锁期间别的协程可能已建好池
            if _pool is not None and _dsn == dsn and (
                running_loop is None or _pool_loop is running_loop
            ):
                return _pool
            if _pool is not None:
                try:
                    await _pool.close()
                except Exception:
                    pass
            try:

                async def _init_conn(conn):
                    # 让 asyncpg 自动把 Python dict/list 编成 JSON 存 jsonb，并在读取时解回 dict
                    await conn.set_type_codec(
                        "jsonb",
                        encoder=json.dumps,
                        decoder=json.loads,
                        schema="pg_catalog",
                    )

                _pool = await asyncpg.create_pool(
                    dsn,
                    min_size=1,
                    max_size=10,
                    timeout=4,
                    command_timeout=8,
                    init=_init_conn,
                )
                _dsn = dsn
                _pool_loop = running_loop
                if not _schema_ready:
                    async with _pool.acquire() as conn:
                        await conn.execute(_SCHEMA)
                    _schema_ready = True
                logger.info("已连接 asyncpg（runs 注册表）。")
            except Exception as e:
                logger.warning("asyncpg 连接失败，回退到内存 runs：%s", e)
                if _pool is not None:
                    try:
                        await _pool.close()
                    except Exception:
                        pass
                _pool = None
                _dsn = None
                _pool_loop = None
    return _pool


async def reset_pool() -> None:
    """测试用：关闭并重置连接池。"""
    global _pool, _dsn, _pool_loop, _schema_ready
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:
            pass
    _pool = None
    _dsn = None
    _pool_loop = None
    _schema_ready = False


async def create_run(run_id: str, symbol: str, days: int, outdir: str, human: bool) -> None:
    pool = await get_pool()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO research_runs(run_id, symbol, days, outdir, human, status) "
            "VALUES($1, $2, $3, $4, $5, 'pending')",
            run_id,
            symbol,
            days,
            outdir,
            human,
        )


_UNSET = object()  # 区分"未提供"（跳过）与"显式置 NULL"

# ------------------------------
# 常驻 loop：供无 asyncio 环境的调用方（Celery worker / 同步图节点）执行 DB 协程。
# 每次用 asyncio.run() 新建 loop 会让连接池跨 loop 重建（每次状态更新都新建
# TCP 连接并重跑 DDL），因此所有同步调用统一走这里。
# ------------------------------
_worker_loop: Optional[asyncio.AbstractEventLoop] = None
_worker_loop_lock = threading.Lock()


def _ensure_worker_loop() -> asyncio.AbstractEventLoop:
    global _worker_loop
    with _worker_loop_lock:
        if _worker_loop is None or _worker_loop.is_closed():
            _worker_loop = asyncio.new_event_loop()
            threading.Thread(target=_worker_loop.run_forever, daemon=True, name="db-worker-loop").start()
        return _worker_loop


def run_sync(coro, timeout: float = 15.0) -> Any:
    """在常驻 loop 上同步执行协程（供无事件循环的调用方使用）。"""
    loop = _ensure_worker_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)


async def update_run(
    run_id: str,
    *,
    status: Any = _UNSET,
    phase: Any = _UNSET,
    progress: Any = _UNSET,
    draft: Any = _UNSET,
    result: Any = _UNSET,
    error: Any = _UNSET,
    suggested_action: Any = _UNSET,
) -> None:
    pool = await get_pool()
    if not pool:
        return
    cols: List[str] = []
    vals: List[Any] = []

    def _add(col: str, value: Any) -> None:
        if value is _UNSET:
            return
        cols.append(f"{col}=${len(vals) + 1}")
        vals.append(value)

    _add("status", status)
    _add("phase", phase)
    _add("progress", progress)
    _add("draft", draft)
    _add("result", result)
    _add("error", error)
    _add("suggested_action", suggested_action)
    if not cols:
        return
    cols.append("updated_at=now()")
    vals.append(run_id)
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE research_runs SET {', '.join(cols)} WHERE run_id=${len(vals)}",
            *vals,
        )


async def claim_resume(run_id: str) -> Optional[bool]:
    """
    原子抢占待审批的 run（避免并发审批请求双重恢复同一图）：
    只有 status 仍为 awaiting_approval 时才置为 resuming。
    返回 True=抢占成功；False=run 存在但不在待审批状态；
    None=存储不可用（无法判定），调用方应回退内存判断。
    """
    pool = await get_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        status = await conn.execute(
            "UPDATE research_runs SET status='resuming', updated_at=now() "
            "WHERE run_id=$1 AND status='awaiting_approval'",
            run_id,
        )
        return status.endswith("1")


async def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    pool = await get_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM research_runs WHERE run_id=$1", run_id)
        return dict(row) if row else None


async def list_runs(limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    pool = await get_pool()
    if not pool:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM research_runs ORDER BY created_at DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
        )
        return [dict(r) for r in rows]
