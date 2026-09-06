"""
Prometheus 指标：任务状态、LLM 调用/token/耗时/成本、节点耗时、数据源错误、严格模式中止。
"""

from __future__ import annotations

import threading
from contextvars import ContextVar

from prometheus_client import Counter, Gauge, Histogram

from src.observability.pricing import compute_cost

RUN_STATUS = Counter("research_runs_total", "按状态统计的任务数", ["status"])
RUNS_IN_PROGRESS = Gauge("research_runs_in_progress", "进行中的任务数")
LLM_CALLS = Counter("research_llm_calls_total", "LLM 调用次数", ["provider", "model", "node"])
LLM_TOKENS = Counter("research_llm_tokens_total", "LLM token 数", ["provider", "model", "type", "node"])
# LLM 调用常见 10-60s，默认桶（最大 10s）会让所有样本落进 +Inf，失去区分度
LLM_LATENCY = Histogram(
    "research_llm_latency_seconds",
    "LLM 调用耗时",
    ["provider", "model"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300),
)
LLM_COST = Counter("research_llm_cost_total", "LLM 累计成本(USD)", ["provider", "model"])
NODE_DURATION = Histogram("research_node_duration_seconds", "图节点耗时", ["node", "status"])
SOURCE_ERRORS = Counter("research_data_source_errors_total", "数据源错误次数", ["source", "kind"])
STRICT_ABORTS = Counter("research_strict_aborts_total", "严格模式中止次数")


_node_ctx: ContextVar[str] = ContextVar("metrics_node", default="")


def set_node_node(node: str) -> None:
    _node_ctx.set(node or "")


def current_node() -> str:
    return _node_ctx.get()


def inc_llm_call(latency_sec: float, input_tokens: int, output_tokens: int, provider: str, model: str) -> float:
    node = current_node()
    LLM_CALLS.labels(provider, model, node).inc()
    LLM_TOKENS.labels(provider, model, "input", node).inc(input_tokens or 0)
    LLM_TOKENS.labels(provider, model, "output", node).inc(output_tokens or 0)
    LLM_LATENCY.labels(provider, model).observe(latency_sec)
    cost = compute_cost(model, input_tokens or 0, output_tokens or 0)
    LLM_COST.labels(provider, model).inc(cost)
    return cost


def observe_node(node: str, duration_sec: float, status: str) -> None:
    NODE_DURATION.labels(node, status).observe(duration_sec)


def inc_run_status(status: str) -> None:
    RUN_STATUS.labels(status).inc()


def set_runs_in_progress(value: int) -> None:
    RUNS_IN_PROGRESS.set(value)


def inc_source_error(source: str, kind: str = "__error__") -> None:
    SOURCE_ERRORS.labels(source, kind).inc()


def inc_strict_abort() -> None:
    STRICT_ABORTS.inc()


class _ActiveRuns:
    def __init__(self):
        self._count = 0
        self._lock = threading.Lock()

    def inc(self) -> None:
        with self._lock:
            self._count += 1
            set_runs_in_progress(self._count)

    def dec(self) -> None:
        with self._lock:
            self._count = max(0, self._count - 1)
            set_runs_in_progress(self._count)


ACTIVE_RUNS = _ActiveRuns()
