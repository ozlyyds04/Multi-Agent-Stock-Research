from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence, Type, TypeVar

import concurrent.futures as cf

T = TypeVar("T")


class RetryableError(RuntimeError):
    """抛出此异常以触发带退避的重试。"""
    pass


@dataclass(frozen=True)
class RetryConfig:
    max_retries: int = 3
    base_delay_sec: float = 0.5
    backoff_factor: float = 2.0
    max_delay_sec: float = 8.0
    jitter_ratio: float = 0.15  # 15% 抖动
    retry_statuses: Sequence[int] = (408, 429, 500, 502, 503, 504)
    timeout_sec: Optional[float] = None  # 每次尝试的超时


def _sleep_with_jitter(seconds: float, jitter_ratio: float) -> None:
    jitter = seconds * jitter_ratio * random.random()
    time.sleep(seconds + jitter)


def _call_with_timeout(fn: Callable[[], T], timeout_sec: float) -> T:
    # 基于线程的超时足以防止生产运行中的卡死。
    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=timeout_sec)
        except cf.TimeoutError as e:
            # 转换为 RetryableError，使 retry_call 无论调用方传入什么
            # retry_exceptions，都能可靠地重试超时。
            raise RetryableError(f"操作在 {timeout_sec} 秒后超时") from e


def retry_call(
        fn: Callable[[], T],
        *,
        cfg: RetryConfig,
        op_name: str,
        logger,
        retry_exceptions: Iterable[Type[BaseException]] = (),
) -> T:
    """
    带指数退避 + 抖动 + 可选单次尝试超时的重试包装器。
    在以下情况重试：
      - fn 抛出 RetryableError
      - fn 抛出 retry_exceptions 中的任何异常
    """
    retry_exceptions = tuple(retry_exceptions)

    last_exc: Optional[BaseException] = None

    for attempt in range(cfg.max_retries + 1):
        try:
            if cfg.timeout_sec:
                return _call_with_timeout(fn, cfg.timeout_sec)
            return fn()

        except RetryableError as e:
            last_exc = e

        except retry_exceptions as e:
            last_exc = e

        # 不再重试
        if attempt >= cfg.max_retries:
            break

        delay = min(cfg.max_delay_sec, cfg.base_delay_sec * (cfg.backoff_factor ** attempt))
        logger.warning(
            "正在重试 op=%s 第 %d/%d 次，%.2f 秒后，原因：%s",
            op_name,
            attempt + 1,
            cfg.max_retries + 1,
            delay,
            str(last_exc),
        )
        _sleep_with_jitter(delay, cfg.jitter_ratio)

    # 重试已用尽
    logger.error("op=%s 重试次数已用尽（共 %d 次）。最后错误：%s", op_name, cfg.max_retries + 1,
                 str(last_exc))

    raise last_exc if last_exc else RuntimeError(f"op={op_name} 的 retry_call 执行失败")
