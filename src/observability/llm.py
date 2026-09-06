"""
LLM 计数包装：记录每次调用的耗时 / token / 成本，并为指标打上节点上下文。
"""

from __future__ import annotations

import time
from typing import Any

from src.utils.logger import get_logger
from src.observability import metrics

logger = get_logger(__name__)


def _extract_usage(resp: Any) -> tuple:
    usage = getattr(resp, "usage_metadata", None)
    if not usage:
        meta = getattr(resp, "response_metadata", None) or {}
        usage = meta.get("token_usage") or meta.get("usage") or {}
    return (
        int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
    )


class _BoundRunnable:
    """包装 bind_tools 返回的 runnable，使其 invoke 也被计数。"""

    def __init__(self, bound: Any, parent: "InstrumentedLLM"):
        self._bound = bound
        self._parent = parent

    def invoke(self, *args, **kwargs):
        return self._parent._invoke(self._bound.invoke, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._bound, name)


class InstrumentedLLM:
    def __init__(self, inner: Any, provider: str = "", model: str = ""):
        self._inner = inner
        self._provider = provider
        self._model = model

    def _invoke(self, fn, *args, **kwargs):
        start = time.monotonic()
        resp = fn(*args, **kwargs)
        latency = time.monotonic() - start
        in_tokens, out_tokens = _extract_usage(resp)
        cost = metrics.inc_llm_call(latency, in_tokens, out_tokens, self._provider, self._model)
        logger.info(
            "LLM 调用 node=%s provider=%s model=%s tokens=%d/%d latency=%.3fs cost=%.6f",
            metrics.current_node(),
            self._provider,
            self._model,
            in_tokens,
            out_tokens,
            latency,
            cost,
        )
        return resp

    def __call__(self, *args, **kwargs):
        # 使包装对象可被 langchain 的 coerce_to_runnable 接收（prompt | llm 会包成 RunnableLambda）
        return self._invoke(self._inner.invoke, *args, **kwargs)

    def invoke(self, *args, **kwargs):
        return self._invoke(self._inner.invoke, *args, **kwargs)

    def bind_tools(self, tools, **kwargs):
        return _BoundRunnable(self._inner.bind_tools(tools, **kwargs), self)

    def bind(self, **kwargs):
        return self._inner.bind(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)
