"""
可插拔的后端抽象：事件总线 / 缓存 / 短期记忆。

- 配置了 REDIS_URL 且未显式禁用（REDIS != none）时走 Redis，否则全部回退内存实现，
  与 asyncpg(checkpointer)、DATABASE_URL 的取舍保持一致，保证本地/测试零依赖。

供上层使用：
  - events: DataAgent / RunManager 发布节点事件、SSE 实时流；
  - cache:  基本面等需要短时重用的数据（缓解上游 429/402）；
  - memory: 短期记忆（HITL 驳回循环的上下文滑动窗口）。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


_DEFAULT_TTL_SEC = 6 * 3600
_EVENT_TTL_SEC = 3600
_MEMORY_TTL_SEC = 3600


def _redis_url() -> str:
    return (os.getenv("REDIS_URL") or "").strip()


def redis_enabled() -> bool:
    """是否启用 Redis 后端。未配置 REDIS_URL 或用 REDIS=none 时返回 False。"""
    return bool(_redis_url()) and os.getenv("REDIS", "").lower() not in ("none", "off", "0", "false", "no")


_sync_client = None
_async_client = None


def _get_sync_client():
    global _sync_client
    if _sync_client is None:
        import redis as redis_sync

        _sync_client = redis_sync.Redis.from_url(_redis_url(), decode_responses=True)
    return _sync_client


def _get_async_client():
    global _async_client
    if _async_client is None:
        import redis.asyncio as redis_async

        _async_client = redis_async.Redis.from_url(_redis_url(), decode_responses=True)
    return _async_client


# ------------------------------
# 事件总线
# ------------------------------
class BaseEventBus:
    def publish(self, run_id: str, event: Dict[str, Any]) -> None:
        raise NotImplementedError

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        pass

    async def stream(self, run_id: str) -> AsyncIterator[Dict[str, Any]]:
        raise NotImplementedError
        yield {}  # pragma: no cover

    def reset(self) -> None:
        pass


class MemoryEventBus(BaseEventBus):
    """进程内事件总线：事件列表 + 订阅者队列，行为与旧的内嵌实现一致。"""

    def __init__(self):
        self._events: Dict[str, List[Dict[str, Any]]] = {}
        self._queues: Dict[str, List[asyncio.Queue]] = {}
        self._last_publish: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop):
        self._loop = loop

    def reset(self):
        with self._lock:
            self._events.clear()
            self._queues.clear()
            self._last_publish.clear()

    def publish(self, run_id: str, event: Dict[str, Any]) -> None:
        now = time.time()
        with self._lock:
            self._events.setdefault(run_id, []).append(event)
            self._last_publish[run_id] = now
            # 长驻进程内存防护：过期 run 的事件清掉；单个 run 事件数封顶
            for rid in [r for r, ts in self._last_publish.items() if now - ts > _EVENT_TTL_SEC]:
                self._events.pop(rid, None)
                self._last_publish.pop(rid, None)
            for rid, evs in self._events.items():
                if len(evs) > 500:
                    del evs[: len(evs) - 500]
            subs = list(self._queues.get(run_id, []))
        if self._loop is not None:
            for queue in subs:
                try:
                    self._loop.call_soon_threadsafe(queue.put_nowait, event)
                except Exception:
                    pass

    async def stream(self, run_id: str) -> AsyncIterator[Dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            events = self._events.setdefault(run_id, [])
            start = len(events)
            self._queues.setdefault(run_id, []).append(queue)
        try:
            replay = list(events[:start])
            replayed_done = any(e.get("type") == "done" for e in replay)
            for event in replay:
                yield event
            if replayed_done:
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield {"type": "keepalive", "ts": time.time()}
                    continue
                yield event
                if event.get("type") == "done":
                    return
        finally:
            with self._lock:
                if queue in self._queues.get(run_id, []):
                    self._queues.get(run_id, []).remove(queue)


class RedisEventBus(BaseEventBus):
    """Redis 事件总线：List 存储历史（可回放）+ Pub/Sub 实时通知（跨 worker）。"""

    def __init__(self, sync_client=None, async_client=None):
        self.sync = sync_client or _get_sync_client()
        self.async_client = async_client or _get_async_client()

    def _key(self, run_id: str) -> str:
        return f"research:events:{run_id}"

    def _chan(self, run_id: str) -> str:
        return f"research:chan:{run_id}"

    def publish(self, run_id: str, event: Dict[str, Any]) -> None:
        try:
            key = self._key(run_id)
            seq = self.sync.incr(f"research:seq:{run_id}")
            self.sync.expire(f"research:seq:{run_id}", _EVENT_TTL_SEC)
            payload = json.dumps({**event, "seq": seq}, ensure_ascii=False)
            self.sync.rpush(key, payload)
            self.sync.expire(key, _EVENT_TTL_SEC)
            self.sync.publish(self._chan(run_id), payload)
        except Exception as e:
            logger.warning("RedisEventBus 发布失败（忽略）：%s", e)

    async def stream(self, run_id: str) -> AsyncIterator[Dict[str, Any]]:
        ps = self.async_client.pubsub()
        await ps.subscribe(self._chan(run_id))
        try:
            key = self._key(run_id)
            base = await self.async_client.llen(key)
            rows = await self.async_client.lrange(key, 0, base - 1)
            replayed_done = False
            for row in rows:
                event = json.loads(row)
                yield event
                if event.get("type") == "done":
                    replayed_done = True
            if replayed_done:
                return
            while True:
                try:
                    msg = await ps.get_message(ignore_subscribe_messages=True, timeout=15)
                except Exception as e:
                    logger.warning("RedisEventBus 订阅异常（忽略）：%s", e)
                    msg = None
                if msg is None:
                    yield {"type": "keepalive", "ts": time.time()}
                    continue
                try:
                    event = json.loads(msg.get("data"))
                except Exception:
                    continue
                seq = int(event.get("seq", 0))
                if seq <= base:
                    continue
                base = max(base, seq)
                yield event
                if event.get("type") == "done":
                    return
        finally:
            try:
                await ps.unsubscribe(self._chan(run_id))
                await ps.aclose()
            except Exception:
                pass


# ------------------------------
# 缓存（基本面/价格等短时重用）
# ------------------------------
class BaseCache:
    def get(self, key: str) -> Optional[Any]:
        raise NotImplementedError

    def set(self, key: str, value: Any, ttl: int) -> None:
        raise NotImplementedError

    def reset(self) -> None:
        pass


class MemoryCache(BaseCache):
    def __init__(self):
        self._data: Dict[str, tuple] = {}

    def reset(self):
        self._data.clear()

    def get(self, key: str) -> Optional[Any]:
        item = self._data.get(key)
        if not item:
            return None
        expires_at, value = item
        if time.time() > expires_at:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: int) -> None:
        self._data[key] = (time.time() + ttl, value)


class RedisCache(BaseCache):
    def __init__(self, client=None):
        self.client = client or _get_sync_client()

    def get(self, key: str) -> Optional[Any]:
        raw = self.client.get(f"cache:{key}")
        if raw is None:
            return None
        return json.loads(raw)

    def set(self, key: str, value: Any, ttl: int) -> None:
        self.client.setex(f"cache:{key}", ttl, json.dumps(value, ensure_ascii=False))


# ------------------------------
# 短期记忆（滑动窗口）
# ------------------------------
class BaseShortTermMemory:
    def record(self, session_key: str, message: Any, ttl: int = _MEMORY_TTL_SEC) -> None:
        raise NotImplementedError

    def recent(self, session_key: str, n: int) -> List[Any]:
        raise NotImplementedError

    def reset(self) -> None:
        pass


class MemoryShortTermMemory(BaseShortTermMemory):
    def __init__(self):
        self._data: Dict[str, List[tuple]] = {}
        self._max = 20

    def reset(self):
        self._data.clear()

    def record(self, session_key: str, message: Any, ttl: int = _MEMORY_TTL_SEC) -> None:
        self._data.setdefault(session_key, []).append((time.time(), message))
        self._data[session_key] = self._data[session_key][-self._max :]

    def recent(self, session_key: str, n: int) -> List[Any]:
        return [msg for _ts, msg in self._data.get(session_key, [])[-n:]]


class RedisShortTermMemory(BaseShortTermMemory):
    def __init__(self, client=None):
        self.client = client or _get_sync_client()

    def record(self, session_key: str, message: Any, ttl: int = _MEMORY_TTL_SEC) -> None:
        key = f"mem:{session_key}"
        self.client.rpush(key, json.dumps(message, ensure_ascii=False))
        self.client.expire(key, ttl)

    def recent(self, session_key: str, n: int) -> List[Any]:
        rows = self.client.lrange(f"mem:{session_key}", -n, -1)
        return [json.loads(r) for r in rows]


# ------------------------------
# 工厂（单例，且可重置供测试）
# ------------------------------
_cache: Optional[BaseCache] = None
_memory: Optional[BaseShortTermMemory] = None
_bus: Optional[BaseEventBus] = None


def get_cache() -> BaseCache:
    global _cache
    if _cache is None:
        _cache = RedisCache() if redis_enabled() else MemoryCache()
        logger.info("缓存后端：%s", "redis" if redis_enabled() else "memory")
    return _cache


def get_short_term_memory() -> BaseShortTermMemory:
    global _memory
    if _memory is None:
        _memory = RedisShortTermMemory() if redis_enabled() else MemoryShortTermMemory()
    return _memory


def get_event_bus() -> BaseEventBus:
    global _bus
    if _bus is None:
        _bus = RedisEventBus() if redis_enabled() else MemoryEventBus()
    return _bus


def reset_backends() -> None:
    """测试用：重置所有单例与内存数据。"""
    global _cache, _memory, _bus, _sync_client, _async_client
    if _cache is not None:
        _cache.reset()
    if _memory is not None:
        _memory.reset()
    if _bus is not None:
        _bus.reset()
    _cache = None
    _memory = None
    _bus = None
    _sync_client = None
    _async_client = None
