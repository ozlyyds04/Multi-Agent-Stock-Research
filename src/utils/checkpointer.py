from __future__ import annotations

import os
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

_SAVER: Any = None
_CONN: Any = None


def reset_checkpointer() -> None:
    """测试用：重置进程内单例。"""
    global _SAVER, _CONN
    if _CONN is not None:
        try:
            _CONN.close()
        except Exception:
            pass
    _SAVER = None
    _CONN = None


def get_checkpointer() -> Any:
    """
    返回 LangGraph checkpointer：
      - 配置了 DATABASE_URL 且 CHECKPOINTER != memory 时，使用 PostgreSQL（状态可跨进程恢复）；
      - 否则回退到内存 MemorySaver。
    """
    global _SAVER, _CONN
    if _SAVER is not None:
        return _SAVER

    dsn = (os.getenv("DATABASE_URL") or "").strip()
    force_memory = os.getenv("CHECKPOINTER", "").lower() in ("memory", "mem")

    if dsn and not force_memory:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg import Connection

            conn = Connection.connect(dsn, autocommit=True, connect_timeout=5)
            saver = PostgresSaver(conn)
            saver.setup()  # 幂等建表
            _CONN = conn
            _SAVER = saver
            logger.info("使用 PostgreSQL checkpointer（审批状态可跨进程恢复）。")
            return _SAVER
        except Exception as e:
            logger.warning("PostgreSQL checkpointer 初始化失败，回退到内存 MemorySaver：%s", e)
            if _CONN is not None:
                try:
                    _CONN.close()
                except Exception:
                    pass
            _CONN = None

    from langgraph.checkpoint.memory import MemorySaver

    _SAVER = MemorySaver()
    logger.info("使用内存 checkpointer（进程重启后状态不保留）。")
    return _SAVER
