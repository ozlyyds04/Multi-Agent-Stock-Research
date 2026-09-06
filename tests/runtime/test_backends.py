import asyncio

from src.runtime import backends
from src.runtime.backends import (
    MemoryCache,
    MemoryEventBus,
    MemoryShortTermMemory,
    RedisCache,
    RedisShortTermMemory,
)


class FakeRedis:
    """极简 redis 客户端替身，用于验证 Redis 缓存/记忆的键与序列化逻辑。"""

    def __init__(self):
        self.data = {}
        self.lists = {}
        self.ttl = None

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.data[key] = value
        self.ttl = (key, ttl)

    def expire(self, key, ttl):
        self.ttl = (key, ttl)

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lrange(self, key, start, end):
        lst = self.lists.get(key, [])
        if start < 0:
            return lst[start:]
        return lst[start : end + 1] if end >= 0 else lst[start:]

    def incr(self, key):
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]

    def publish(self, chan, msg):
        self.pub = (chan, msg)


def test_factory_defaults_to_memory_when_no_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS", raising=False)
    backends.reset_backends()
    assert isinstance(backends.get_cache(), MemoryCache)
    assert isinstance(backends.get_short_term_memory(), MemoryShortTermMemory)
    assert isinstance(backends.get_event_bus(), MemoryEventBus)
    backends.reset_backends()


def test_memory_cache_respects_ttl():
    c = MemoryCache()
    c.set("k", {"v": 1}, -1)
    assert c.get("k") is None
    c.set("k2", 5, 100)
    assert c.get("k2") == 5


def test_memory_short_term_sliding_window():
    m = MemoryShortTermMemory()
    for i in range(5):
        m.record("run1", {"i": i})
    recent = m.recent("run1", 3)
    assert [x["i"] for x in recent] == [2, 3, 4]


def test_redis_cache_uses_prefix_and_roundtrip():
    fake = FakeRedis()
    c = RedisCache(client=fake)
    c.set("fund:AAPL", {"inc": [1], "km": [2]}, 60)
    assert fake.data.get("cache:fund:AAPL") is not None
    assert c.get("fund:AAPL") == {"inc": [1], "km": [2]}
    assert c.get("missing") is None


def test_redis_short_term_memory_roundtrip():
    fake = FakeRedis()
    m = RedisShortTermMemory(client=fake)
    m.record("run1", {"action": "reject", "comment": "保守"})
    m.record("run1", {"action": "approve"})
    recent = m.recent("run1", 5)
    assert len(recent) == 2
    assert recent[0]["action"] == "reject"
    assert recent[1]["action"] == "approve"


def test_memory_event_bus_replays_then_closes_on_done():
    bus = MemoryEventBus()

    async def _run():
        bus.set_loop(asyncio.get_running_loop())
        bus.publish("r1", {"type": "node", "node": "analyze"})
        bus.publish("r1", {"type": "done", "status": "success"})
        events = []
        async for e in bus.stream("r1"):
            events.append(e)
            if e.get("type") == "done":
                break
        return events

    events = asyncio.run(_run())
    assert [e["type"] for e in events] == ["node", "done"]


def test_memory_event_bus_live_delivery():
    bus = MemoryEventBus()

    async def _run():
        bus.set_loop(asyncio.get_running_loop())

        async def producer():
            bus.publish("r2", {"type": "node", "node": "analyze"})
            await asyncio.sleep(0.01)
            bus.publish("r2", {"type": "done", "status": "success"})

        task = asyncio.create_task(producer())
        events = []
        async for e in bus.stream("r2"):
            events.append(e)
            if e.get("type") == "done":
                break
        await task
        return events

    events = asyncio.run(_run())
    assert events and events[-1]["type"] == "done"
    assert any(e["type"] == "node" for e in events)
