"""
长期记忆存储：事实/偏好的向量化记忆，支持相似检索、重要性、时间衰减与读写记录。

- InMemoryMemoryStore：进程内实现（本地/测试，零依赖）。
- PostgresMemoryStore：Postgres + pgvector（生产），需设置 MEMORY_BACKEND=postgres 且 DATABASE_URL。

记忆条目字段：session_key / kind(fact|preference|note) / content / embedding /
importance / created_at / last_accessed_at / access_count / meta。
"""

from __future__ import annotations

import math
import os
import time
from typing import Any, Dict, List, Optional

from src.utils.logger import get_logger
from src.runtime.embedder import get_embedder

logger = get_logger(__name__)


def _cosine(a: List[float], b: List[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


class BaseMemoryStore:
    def add(
        self, session_key: str, content: str, kind: str = "fact", importance: float = 0.5, meta: Optional[Dict] = None
    ) -> int:
        raise NotImplementedError

    def search(self, embedding: List[float], k: int = 5, session_key: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def recent(self, session_key: str, k: int = 5) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def compact(self, session_key: str, max_entries: int = 50, summarizer: Any = None) -> Dict[str, Any]:
        """把超出 max_entries 的旧条目压缩（LLM 摘要）或按重要度裁剪。"""
        raise NotImplementedError

    def reset(self) -> None:
        pass


class InMemoryMemoryStore(BaseMemoryStore):
    def __init__(self):
        self._entries: List[Dict[str, Any]] = []
        self._next_id = 1

    def reset(self) -> None:
        self._entries.clear()
        self._next_id = 1

    def add(
        self, session_key: str, content: str, kind: str = "fact", importance: float = 0.5, meta: Optional[Dict] = None
    ) -> int:
        try:
            embedding = get_embedder().embed_one(content)
        except Exception:
            embedding = []
        entry = {
            "id": self._next_id,
            "session_key": session_key,
            "kind": kind,
            "content": content,
            "embedding": embedding,
            "importance": float(importance),
            "created_at": time.time(),
            "last_accessed_at": time.time(),
            "access_count": 0,
            "meta": meta or {},
        }
        self._next_id += 1
        self._entries.append(entry)
        return entry["id"]

    def search(self, embedding: List[float], k: int = 5, session_key: Optional[str] = None) -> List[Dict[str, Any]]:
        scored = []
        now = time.time()
        for e in self._entries:
            if session_key and e["session_key"] != session_key:
                continue
            if not e["embedding"]:
                continue
            sim = _cosine(embedding, e["embedding"])
            # 时间衰减：越久未访问，相关度越低；重要性作为乘数
            age_hours = (now - e["last_accessed_at"]) / 3600.0
            decay = 1.0 / (1.0 + age_hours / 24.0)
            score = sim * (0.5 + 0.5 * e["importance"]) * decay
            scored.append((score, e))
        scored.sort(key=lambda x: -x[0])
        # 命中后更新访问热度（LRU 感知）
        for _score, e in scored[:k]:
            e["access_count"] += 1
            e["last_accessed_at"] = now
        return [
            {
                "id": e["id"],
                "session_key": e["session_key"],
                "kind": e["kind"],
                "content": e["content"],
                "importance": e["importance"],
                "score": s,
                "meta": e["meta"],
            }
            for s, e in scored[:k]
        ]

    def recent(self, session_key: str, k: int = 5) -> List[Dict[str, Any]]:
        items = [e for e in self._entries if e["session_key"] == session_key]
        items.sort(key=lambda x: -x["created_at"])
        return [
            {
                "id": e["id"],
                "session_key": e["session_key"],
                "kind": e["kind"],
                "content": e["content"],
                "importance": e["importance"],
                "meta": e["meta"],
            }
            for e in items[:k]
        ]

    def compact(self, session_key: str, max_entries: int = 50, summarizer: Any = None) -> Dict[str, Any]:
        entries = [e for e in self._entries if e["session_key"] == session_key]
        if len(entries) <= max_entries:
            return {"compacted": 0, "kept": len(entries), "mode": "none"}
        # 优先级排序：重要度 + 新鲜度（保留最好的 max_entries 条）
        entries.sort(key=lambda e: (e["importance"], e["created_at"]), reverse=True)
        keep = entries[:max_entries]
        excess = entries[max_entries:]

        if summarizer is not None and excess:
            try:
                digest = "\n".join(e["content"] for e in reversed(excess))
                summary = summarizer(digest)
                summary = str(summary or "").strip()
                if summary:
                    self.add(
                        session_key,
                        summary,
                        kind="compressed",
                        importance=min(1.0, sum(e["importance"] for e in excess) / len(excess)),
                        meta={"compressed_from": len(excess)},
                    )
                    for e in excess:
                        self._entries.remove(e)
                    return {"compacted": len(excess), "kept": max_entries + 1, "mode": "summarize"}
            except Exception as e:
                logger.warning("记忆摘要失败，按重要度裁剪：%s", e)

        keep_ids = {e["id"] for e in keep}
        # 只能裁剪当前 session 的条目：_entries 是跨 session 的全局列表，
        # 按 keep_ids 过滤会把其他 session（其他股票）的记忆全部误删
        self._entries = [e for e in self._entries if e["session_key"] != session_key or e["id"] in keep_ids]
        return {"compacted": len(excess), "kept": len(keep), "mode": "prune"}


class PostgresMemoryStore(BaseMemoryStore):
    """Postgres + pgvector 长期记忆（同步 psycopg，供同步图节点调用）。"""

    def __init__(self, dsn: str, dim: int):
        import psycopg

        self._dim = dim
        self.conn = psycopg.Connection.connect(dsn, autocommit=True, connect_timeout=5)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS research_memory (
                  id SERIAL PRIMARY KEY,
                  session_key TEXT NOT NULL,
                  kind TEXT NOT NULL DEFAULT 'fact',
                  content TEXT NOT NULL,
                  embedding vector(%d),
                  importance REAL DEFAULT 0.5,
                  created_at TIMESTAMPTZ DEFAULT now(),
                  last_accessed_at TIMESTAMPTZ DEFAULT now(),
                  access_count INT DEFAULT 0,
                  meta JSONB
                )
                """ % self._dim)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_research_memory_session ON research_memory(session_key)")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_research_memory_vec ON research_memory USING ivfflat (embedding vector_cosine_ops)"
            )

    def add(
        self, session_key: str, content: str, kind: str = "fact", importance: float = 0.5, meta: Optional[Dict] = None
    ) -> int:
        import json as _json

        vec = get_embedder().embed_one(content)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO research_memory(session_key, kind, content, embedding, importance, meta) "
                "VALUES(%s, %s, %s, %s, %s, %s) RETURNING id",
                (session_key, kind, content, _vec_to_pg(vec), importance, _json.dumps(meta or {}, ensure_ascii=False)),
            )
            row = cur.fetchone()
            return int(row[0])

    def search(self, embedding: List[float], k: int = 5, session_key: Optional[str] = None) -> List[Dict[str, Any]]:
        import json as _json

        with self.conn.cursor() as cur:
            if session_key:
                cur.execute(
                    "SELECT id, session_key, kind, content, importance, meta, "
                    "(1 - (embedding <=> %s::vector)) * (0.5 + 0.5 * importance) AS score "
                    "FROM research_memory WHERE session_key=%s ORDER BY embedding <=> %s::vector LIMIT %s",
                    (_vec_to_pg(embedding), session_key, _vec_to_pg(embedding), k),
                )
            else:
                cur.execute(
                    "SELECT id, session_key, kind, content, importance, meta, "
                    "(1 - (embedding <=> %s::vector)) * (0.5 + 0.5 * importance) AS score "
                    "FROM research_memory ORDER BY embedding <=> %s::vector LIMIT %s",
                    (_vec_to_pg(embedding), _vec_to_pg(embedding), k),
                )
            return [
                {
                    "id": r[0],
                    "session_key": r[1],
                    "kind": r[2],
                    "content": r[3],
                    "importance": r[4],
                    "meta": _json.loads(r[5]) if r[5] else {},
                    "score": float(r[6]),
                }
                for r in cur.fetchall()
            ]

    def recent(self, session_key: str, k: int = 5) -> List[Dict[str, Any]]:
        import json as _json

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id, session_key, kind, content, importance, meta FROM research_memory "
                "WHERE session_key=%s ORDER BY created_at DESC LIMIT %s",
                (session_key, k),
            )
            return [
                {
                    "id": r[0],
                    "session_key": r[1],
                    "kind": r[2],
                    "content": r[3],
                    "importance": r[4],
                    "meta": _json.loads(r[5]) if r[5] else {},
                }
                for r in cur.fetchall()
            ]

    def compact(self, session_key: str, max_entries: int = 50, summarizer: Any = None) -> Dict[str, Any]:

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id, content, importance FROM research_memory WHERE session_key=%s "
                "ORDER BY importance DESC, created_at DESC",
                (session_key,),
            )
            rows = cur.fetchall()
        if len(rows) <= max_entries:
            return {"compacted": 0, "kept": len(rows), "mode": "none"}
        keep = rows[:max_entries]
        excess = rows[max_entries:]

        if summarizer is not None and excess:
            try:
                digest = "\n".join(r[1] for r in reversed(excess))
                summary = str(summarizer(digest) or "").strip()
                if summary:
                    self.add(
                        session_key,
                        summary,
                        kind="compressed",
                        importance=min(1.0, sum(r[2] for r in excess) / len(excess)),
                        meta={"compressed_from": len(excess)},
                    )
                    with self.conn.cursor() as cur:
                        cur.execute("DELETE FROM research_memory WHERE id = ANY(%s)", ([r[0] for r in excess],))
                    return {"compacted": len(excess), "kept": max_entries + 1, "mode": "summarize"}
            except Exception as e:
                logger.warning("记忆摘要失败，按重要度裁剪：%s", e)

        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM research_memory WHERE session_key=%s AND NOT (id = ANY(%s))",
                (session_key, [r[0] for r in keep]),
            )
        return {"compacted": len(excess), "kept": len(keep), "mode": "prune"}


def _vec_to_pg(vec: List[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


_store: Optional[BaseMemoryStore] = None


def get_memory_store() -> BaseMemoryStore:
    global _store
    if _store is None:
        backend = os.getenv("MEMORY_BACKEND", "memory").lower()
        dsn = (os.getenv("DATABASE_URL") or "").strip()
        if backend == "postgres" and dsn:
            try:
                _store = PostgresMemoryStore(dsn, get_embedder().dim)
                logger.info("长期记忆后端：Postgres(pgvector)。")
            except Exception as e:
                logger.warning("Postgres 长期记忆初始化失败，回退内存：%s", e)
                _store = InMemoryMemoryStore()
        else:
            _store = InMemoryMemoryStore()
    return _store


def reset_memory_store() -> None:
    global _store
    if _store is not None:
        _store.reset()
    _store = None
