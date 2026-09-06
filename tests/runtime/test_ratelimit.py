import pytest

from src.runtime.ratelimit import (
    MemoryRateLimiter,
    RateLimitExceeded,
    RedisRateLimiter,
    check_rate_limit,
    get_rate_limiter,
    reset_rate_limiter,
)


class FakeRedisEval:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def eval(self, script, numkeys, *keys_and_args):
        self.calls.append((script, numkeys, keys_and_args))
        return self.result


def test_memory_limiter_respects_daily_quota(monkeypatch):
    monkeypatch.setenv("RL_TEST_QPS", "1000")
    monkeypatch.setenv("RL_TEST_DAILY", "2")
    rl = MemoryRateLimiter()
    assert rl.allow("test") is True
    assert rl.allow("test") is True
    assert rl.allow("test") is False


def test_memory_limiter_resets():
    rl = MemoryRateLimiter()
    assert rl.allow("x") is True
    rl.reset()
    # 默认 x 源 QPS/Daily 足够，reset 后计数器清零
    assert rl.allow("x") is True


def test_check_rate_limit_raises_when_exceeded(monkeypatch):
    monkeypatch.setenv("RL_TEST_QPS", "1000")
    monkeypatch.setenv("RL_TEST_DAILY", "1")
    reset_rate_limiter()
    check_rate_limit("test")  # 第 1 次放行
    with pytest.raises(RateLimitExceeded):
        check_rate_limit("test")  # 第 2 次触发每日配额
    reset_rate_limiter()


def test_redis_limiter_allows_when_eval_returns_1():
    fake = FakeRedisEval(1)
    rl = RedisRateLimiter(client=fake)
    assert rl.allow("news") is True
    assert fake.calls


def test_redis_limiter_denies_when_eval_returns_0():
    fake = FakeRedisEval(0)
    rl = RedisRateLimiter(client=fake)
    assert rl.allow("news") is False


def test_redis_limiter_fails_open_on_client_error():
    class Boom:
        def eval(self, *a, **k):
            raise RuntimeError("redis down")

    rl = RedisRateLimiter(client=Boom())
    assert rl.allow("news") is True


def test_default_limiter_is_memory_without_redis(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS", raising=False)
    reset_rate_limiter()
    assert isinstance(get_rate_limiter(), MemoryRateLimiter)
    reset_rate_limiter()
