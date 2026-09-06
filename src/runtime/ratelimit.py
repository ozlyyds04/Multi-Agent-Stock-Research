"""
主动限流器：按数据源限速（每秒请求数 + 每日配额），防止上游 429/402。

- 内存实现：固定窗口计数（进程内），用于本地/测试；
- Redis 实现：用 Lua 脚本原子 INCR + EXPIRE，多进程/多 worker 共享额度。

配置（每个源）：
  RL_<SOURCE>_QPS    每秒请求上限（默认见 DEFAULT_QPS）
  RL_<SOURCE>_DAILY  每日请求上限（默认见 DEFAULT_DAILY）

注意：缓存是“第一道盾”（少打上游），限流器是“背板”（防止超过配额）。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


# 各数据源默认上限（可按需用环境变量覆盖）
DEFAULT_QPS: Dict[str, int] = {
    "alpha_vantage": 1,
    "eastmoney": 5,
    "news": 2,
}
DEFAULT_DAILY: Dict[str, int] = {
    "alpha_vantage": 25,  # Alpha Vantage 免费版约为 25 次/天
    "eastmoney": 5000,
    "news": 2000,
}


def _qps(source: str) -> int:
    return int(os.getenv(f"RL_{source.upper()}_QPS", str(DEFAULT_QPS.get(source, 5))))


def _daily(source: str) -> int:
    return int(os.getenv(f"RL_{source.upper()}_DAILY", str(DEFAULT_DAILY.get(source, 5000))))


class RateLimitExceeded(RuntimeError):
    """请求被本地限流器拒绝。"""


class BaseRateLimiter:
    def allow(self, source: str) -> bool:
        raise NotImplementedError

    def reset(self) -> None:
        pass


class MemoryRateLimiter(BaseRateLimiter):
    """固定窗口计数（进程内）。"""

    def __init__(self):
        self._state: Dict[str, Dict[str, int]] = {}
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self._state.clear()

    def allow(self, source: str) -> bool:
        now = time.time()
        sec = int(now)
        day = int(now // 86400)
        qps = _qps(source)
        daily = _daily(source)
        with self._lock:
            st = self._state.setdefault(source, {"sec": sec, "sec_n": 0, "day": day, "day_n": 0})
            if st["sec"] != sec:
                st["sec"] = sec
                st["sec_n"] = 0
            if st["day"] != day:
                st["day"] = day
                st["day_n"] = 0
            if st["sec_n"] >= qps or st["day_n"] >= daily:
                return False
            st["sec_n"] += 1
            st["day_n"] += 1
            return True


class RedisRateLimiter(BaseRateLimiter):
    """Redis Lua 原子计数器（多进程共享配额）。"""

    _LUA = """
local s = redis.call('INCR', KEYS[1])
local d = redis.call('INCR', KEYS[2])
if s == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
if d == 1 then redis.call('EXPIRE', KEYS[2], ARGV[2]) end
if s > tonumber(ARGV[3]) or d > tonumber(ARGV[4]) then
  redis.call('DECR', KEYS[1])
  redis.call('DECR', KEYS[2])
  return 0
end
return 1
"""

    def __init__(self, client=None):
        if client is None:
            from src.runtime.backends import _get_sync_client

            client = _get_sync_client()
        self.client = client

    def allow(self, source: str) -> bool:
        now = time.time()
        sec = int(now)
        day = int(now // 86400)
        keys = [f"rl:{source}:sec:{sec}", f"rl:{source}:day:{day}"]
        args = [2, 86400, _qps(source), _daily(source)]
        try:
            return bool(self.client.eval(self._LUA, len(keys), *keys, *args))
        except Exception as e:
            logger.warning("RedisRateLimiter 执行失败（按允许处理）：%s", e)
            return True


_limiter: Optional[BaseRateLimiter] = None


def get_rate_limiter() -> BaseRateLimiter:
    global _limiter
    if _limiter is None:
        from src.runtime.backends import redis_enabled

        _limiter = RedisRateLimiter() if redis_enabled() else MemoryRateLimiter()
    return _limiter


def reset_rate_limiter() -> None:
    global _limiter
    if _limiter is not None:
        _limiter.reset()
    _limiter = None


def check_rate_limit(source: str) -> None:
    """若超过配额则抛出 RateLimitExceeded（供工具在真正发起网络请求前校验）。"""
    if not get_rate_limiter().allow(source):
        raise RateLimitExceeded(f"数据源 {source} 请求超过当前窗口配额（QPS={_qps(source)}，每日={_daily(source)}）")
