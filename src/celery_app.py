"""
Celery 应用：Redis 作为 broker/backend。
队列：research（默认）、research_high（审批恢复，高优先）、dead_letter（重试耗尽的死信）。
"""

from __future__ import annotations

import os

from celery import Celery
from kombu import Queue


def _redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")


celery_app = Celery(
    "multiagent_stock",
    broker=_redis_url(),
    backend=_redis_url(),
    include=["src.tasks"],
)

celery_app.conf.update(
    task_default_queue="research",
    task_queues=(
        Queue("research", routing_key="research"),
        Queue("research_high", routing_key="research.high"),
        Queue("dead_letter", routing_key="dead.letter"),
    ),
    task_routes={
        "src.tasks.run_research": {"queue": "research"},
        "src.tasks.resume_research": {"queue": "research_high"},
        "src.tasks.dead_letter_run": {"queue": "dead_letter"},
    },
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # 流水线 p95 时长约 3-8 分钟：软限内 SoftTimeLimitExceeded 会被当作普通失败
    # 捕获并触发整图重试（烧 4 倍成本），所以软限直接中止、硬限兜底；
    # 需要更长的部署请同步调大这两个值。
    task_time_limit=1800,
    task_soft_time_limit=1500,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    worker_max_tasks_per_child=20,
    broker_connection_retry_on_startup=True,
)


def celery_enabled() -> bool:
    """是否启用 Celery 后端（CELERY_ENABLED=true）。未启用时回退进程内线程执行。"""
    return os.getenv("CELERY_ENABLED", "").lower() in ("1", "true", "yes", "on")
