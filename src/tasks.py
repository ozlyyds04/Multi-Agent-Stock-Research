"""Celery 任务：把研究/审批执行交给 worker。

重试语义：
  - 只有基础设施类瞬时错误（网络/超时）才自动重试；
  - 业务失败（precheck 失败、无效代码、严格模式中止等）是确定性结果，
    重试只会重复消耗上游 API 配额并让 DB 状态震荡，直接进入死信；
  - resume 不做自动重试：interrupt 已被首次执行消费，重试在语义上不可能成功。
"""

from __future__ import annotations

from src.utils.logger import get_logger
from src.celery_app import celery_app
from src.runtime.runner import get_runner
from src.runtime import db

logger = get_logger(__name__)


@celery_app.task(
    name="src.tasks.run_research",
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
)
def run_research(self, run_id, symbol, days, outdir, human=False):
    status, retryable = get_runner().execute(run_id, symbol, days, outdir, human)
    if status == "failed" and retryable and self.request.retries < self.max_retries:
        raise self.retry(countdown=2 ** (self.request.retries + 1))
    if status == "failed":
        dead_letter_run.delay(run_id)
    return status


@celery_app.task(
    name="src.tasks.resume_research",
    bind=True,
    max_retries=0,
)
def resume_research(self, run_id, feedback):
    status, _retryable = get_runner().resume(run_id, feedback)
    if status == "failed":
        dead_letter_run.delay(run_id)
    return status


@celery_app.task(name="src.tasks.dead_letter_run")
def dead_letter_run(run_id):
    """重试耗尽后，把 run 标记为死信（failed + phase=dead_letter）。"""
    try:
        row = db.run_sync(db.get_run(run_id))
        reason = (row or {}).get("error") or "任务已进入死信队列（重试耗尽）。"
        db.run_sync(
            db.update_run(run_id, status="failed", phase="dead_letter", error=reason)
        )
        logger.warning("run %s 已转入死信：%s", run_id, reason)
    except Exception as e:
        logger.warning("run %s 死信处理失败：%s", run_id, e)
    return {"run_id": run_id}
