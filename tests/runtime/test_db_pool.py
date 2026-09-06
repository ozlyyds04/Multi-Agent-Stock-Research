"""覆盖 db.py 的 asyncpg 真实路径（通过 mock 连接池，无需真实 PostgreSQL）。"""

import asyncio


class _FakeConn:
    async def set_type_codec(self, *a, **k):
        return None

    async def execute(self, *a, **k):
        return "UPDATE 1"

    async def fetchrow(self, *a, **k):
        return {"run_id": "r1", "status": "pending"}

    async def fetch(self, *a, **k):
        return [{"run_id": "r1", "status": "pending"}]


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn
        self.closed = False

    def acquire(self):
        return _AcquireCtx(self._conn)

    async def close(self):
        self.closed = True


def _make_pool_factory():
    async def _factory(dsn, **kw):
        init = kw.get("init")
        conn = _FakeConn()
        if init:
            await init(conn)
        return _FakePool(conn)

    return _factory


def test_db_real_pool_paths(monkeypatch):
    from src.runtime import db

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/db")
    monkeypatch.setenv("CHECKPOINTER", "")
    monkeypatch.setattr(db.asyncpg, "create_pool", _make_pool_factory())

    async def run():
        pool = await db.get_pool()
        assert pool is not None
        # 命中 _pool_loop 不变、dsn 不变 -> 直接返回缓存池
        assert await db.get_pool() is pool
        await db.create_run("r1", "AAPL", 5, "artifacts", False)
        await db.update_run(
            "r1",
            status="running",
            phase="analyze",
            progress=10,
            draft="draft",
            result={"x": 1},
            error=None,
            suggested_action="continue",
        )
        await db.update_run("r1")  # 无字段 -> 提前返回
        assert await db.claim_resume("r1") is True
        row = await db.get_run("r1")
        assert row and row["run_id"] == "r1"
        rows = await db.list_runs(limit=1, offset=0)
        assert len(rows) == 1
        await db.reset_pool()

    asyncio.run(run())


def test_db_get_pool_connection_failure_falls_back(monkeypatch):
    from src.runtime import db

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/db")
    monkeypatch.setenv("CHECKPOINTER", "")

    async def _fail(*a, **k):
        raise ConnectionError("cannot connect")

    monkeypatch.setattr(db.asyncpg, "create_pool", _fail)

    async def run():
        assert await db.get_pool() is None

    asyncio.run(run())


def test_db_multiple_loops_rebuild_pool(monkeypatch):
    from src.runtime import db

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/db")
    monkeypatch.setenv("CHECKPOINTER", "")
    monkeypatch.setattr(db.asyncpg, "create_pool", _make_pool_factory())

    asyncio.run(db.get_pool())
    # 第二次 asyncio.run 走新 loop，命中跨 loop 重建分支
    asyncio.run(db.get_pool())
    asyncio.run(db.reset_pool())


def test_db_run_sync_no_pool(monkeypatch):
    from src.runtime import db

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CHECKPOINTER", "memory")
    assert db.run_sync(db.get_run("nope")) is None
    assert db.run_sync(db.list_runs()) == []
