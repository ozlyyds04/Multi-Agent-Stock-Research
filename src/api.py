import json
from pathlib import Path as PyPath

from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, Response, FileResponse
from pydantic import BaseModel
import os
from src.graph.orchestrator import run_pipeline, resume_pipeline
from src.graph.precheck import precheck_symbol
from src.utils.logger import get_logger
from src.guardrails.inputs import normalize_symbol, MIN_DAYS, MAX_DAYS
from src.runtime.run_manager import RUN_MANAGER
from src.runtime import db
from src.runtime.ratelimit import check_rate_limit, RateLimitExceeded
from dotenv import load_dotenv

logger = get_logger(__name__)

ARTIFACTS_DIR = os.getenv("ARTIFACTS_DIR", "artifacts")
API_KEY = os.getenv("API_KEY", "")

# 加载 .env（预检 fetch_quote 需要 ALPHA_VANTAGE_API_KEY / OPENAI_API_KEY）
load_dotenv()

app = FastAPI(
    title="自动股票研究多智能体系统",
    description="LangGraph 多智能体编排（数据、分析、合规、主管）",
    version="0.1.0",
)


def require_api_key(x_api_key: str | None = Header(default=None)):
    """可选鉴权：配置了 API_KEY 时，写操作用需要 X-API-Key 头且进行请求限流；未配置则放行（便于本地联调）。"""
    if not API_KEY:
        return
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="无效的 API Key")
    try:
        check_rate_limit("api")
    except RateLimitExceeded as e:
        raise HTTPException(status_code=429, detail=str(e))


class RunRequest(BaseModel):
    symbol: str
    days: int = 30
    human: bool = False
    outdir: str = "artifacts"  # 已废弃：服务端固定使用 ARTIFACTS_DIR，客户端传值被忽略


class ApproveRequest(BaseModel):
    run_id: str
    action: str = "approve"
    comment: str = ""
    edited_text: str = ""


class ResearchRequest(BaseModel):
    symbol: str
    days: int = 30
    human: bool = False
    outdir: str = "artifacts"  # 已废弃：服务端固定使用 ARTIFACTS_DIR，客户端传值被忽略


class ResearchDecision(BaseModel):
    action: str = "approve"
    comment: str = ""
    edited_text: str = ""


@app.get("/health", tags=["health"])
def health():
    """
    供 UI 和监控使用的轻量健康检查接口。
    不调用外部服务。
    """
    return {"status": "ok", "service": "multiagent-stock-research"}


@app.get("/metrics", include_in_schema=False, tags=["observability"])
def prometheus_metrics():
    """Prometheus 指标（任务状态、LLM 调用/token/耗时/成本、节点耗时、数据源错误）。"""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/artifacts/{path:path}", include_in_schema=False, tags=["artifacts"])
async def get_artifact(path: str):
    """下载/预览生成的产物（Markdown / PDF / 图表 / 原始 JSON）。"""
    base = PyPath(ARTIFACTS_DIR).resolve()
    target = (base / path).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return JSONResponse(status_code=404, content={"status": "error", "reason": "产物不存在。"})
    return FileResponse(target, filename=PyPath(path).name)


@app.post("/analyze", tags=["analysis"], dependencies=[Depends(require_api_key)])
def analyze_stock(req: RunRequest):
    symbol_uppercase = normalize_symbol(req.symbol)
    if not symbol_uppercase.replace("-", "").replace(".", "").isalnum():
        return JSONResponse(status_code=400, content={"status": "error", "reason": "股票代码格式无效。"})

    if req.days < MIN_DAYS or req.days > MAX_DAYS:
        return JSONResponse(
            status_code=400, content={"status": "error", "reason": f"天数必须在 {MIN_DAYS} 到 {MAX_DAYS} 之间。"}
        )

    # 预检：按市场验证股票代码（港股走 AKShare，其余走 Alpha Vantage）
    try:
        ok, error_message, suggested = precheck_symbol(symbol_uppercase)
        if not ok:
            logger.error("对 %s 的预检失败：%s", symbol_uppercase, error_message)
            return JSONResponse(
                status_code=400,
                content={"status": "error", "reason": error_message, "suggested_action": suggested},
            )
    except Exception as exc:
        logger.exception("对 %s 的预检发生异常：%s", symbol_uppercase, exc)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "reason": f"股票预检失败：{exc}",
                "suggested_action": "请检查 .env 中的 ALPHA_VANTAGE_API_KEY 配置并重启后端。",
            },
        )

    logger.info("收到 POST /analyze 请求：symbol=%s，days=%d", req.symbol, req.days)

    try:
        # outdir 由服务端固定：客户端可控行写任意目录（任意路径建目录/写文件）
        result = run_pipeline(symbol_uppercase, req.days, ARTIFACTS_DIR, req.human)

        # 人工审批：流水线在审批边暂停，返回草稿供前端审阅
        if isinstance(result, dict) and result.get("status") == "pending_approval":
            logger.info("报告草稿已生成，等待审批：run_id=%s", result.get("run_id"))
            return JSONResponse(status_code=200, content=result)

        # 如果严格模式返回了错误
        if isinstance(result, dict) and result.get("status") == "error":
            logger.error("严格模式流水线中止：%s", result.get("reason"))
            return JSONResponse(
                status_code=400,
                content={
                    "status": "error",
                    "symbol": result.get("symbol"),
                    "reason": result.get("reason"),
                    "suggested_action": result.get("suggested_action"),
                },
            )

        logger.info("已成功为 %s 生成报告", req.symbol)
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "symbol": symbol_uppercase,
                "report_path": result.get("report"),
                "plot_path": result.get("plot"),
                "raw_data": result.get("raw"),
                "pdf_path": result.get("pdf"),
                "message": "报告生成成功",
            },
        )

    except Exception:
        logger.exception("为 %s 生成报告时出错", req.symbol)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "reason": "生成报告时发生内部服务器错误。",
                "suggested_action": "请查看服务器日志以了解详情。",
            },
        )


@app.post("/analyze/approve", tags=["analysis"], dependencies=[Depends(require_api_key)])
def approve_analysis(req: ApproveRequest):
    """人工审批：批准 / 驳回（带回执意见）/ 修改后发布，并从暂停点恢复流水线。"""
    action = (req.action or "approve").lower()
    if action not in ("approve", "reject", "edit"):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "reason": "审批动作必须为 approve / reject / edit。"},
        )

    feedback = {"action": action, "comment": req.comment, "edited_text": req.edited_text}
    result = resume_pipeline(req.run_id, feedback)

    if result.get("status") == "error":
        logger.error("审批恢复失败：run_id=%s，reason=%s", req.run_id, result.get("reason"))
        return JSONResponse(status_code=400, content=result)

    logger.info("审批已提交：run_id=%s，action=%s", req.run_id, action)
    return JSONResponse(status_code=200, content=result)


def _sse_event(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def _db_row_snapshot(row: dict) -> dict:
    """把 asyncpg runs 行映射成与 RUN_MANAGER.snapshot 一致的结构。"""
    return {
        "run_id": row.get("run_id"),
        "symbol": row.get("symbol"),
        "days": row.get("days"),
        "human": row.get("human"),
        "status": row.get("status"),
        "phase": row.get("phase"),
        "progress": row.get("progress"),
        "draft": row.get("draft"),
        "result": row.get("result"),
        "error": row.get("error"),
        "suggested_action": row.get("suggested_action"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@app.post("/api/research", status_code=202, tags=["research"], dependencies=[Depends(require_api_key)])
async def submit_research(req: ResearchRequest):
    """
    异步提交研究任务：立即返回 run_id，不阻塞请求。
    """
    symbol_uppercase = normalize_symbol(req.symbol)
    if not symbol_uppercase.replace("-", "").replace(".", "").isalnum():
        return JSONResponse(status_code=400, content={"status": "error", "reason": "股票代码格式无效。"})
    if req.days < MIN_DAYS or req.days > MAX_DAYS:
        return JSONResponse(
            status_code=400, content={"status": "error", "reason": f"天数必须在 {MIN_DAYS} 到 {MAX_DAYS} 之间。"}
        )

    run_id = await RUN_MANAGER.submit(symbol_uppercase, req.days, ARTIFACTS_DIR, req.human)
    logger.info("已接受异步研究任务：run_id=%s，symbol=%s", run_id, symbol_uppercase)
    return {"status": "accepted", "run_id": run_id, "symbol": symbol_uppercase}


@app.get("/api/research", tags=["research"])
async def list_research(limit: int = 20, offset: int = 0):
    """列出历史任务（分页）。"""
    runs = await RUN_MANAGER.list_runs(min(limit, 100), offset)
    return {"status": "ok", "runs": runs}


@app.get("/api/research/{run_id}", tags=["research"])
async def get_research(run_id: str):
    """查询任务状态与结果。"""
    snapshot = RUN_MANAGER.snapshot(run_id)
    if db.configured():
        row = await db.get_run(run_id)
        if row:
            snapshot = _db_row_snapshot(row)
    if snapshot is None:
        return JSONResponse(status_code=404, content={"status": "error", "reason": "任务不存在。"})
    return {"status": "ok", "run": snapshot}


@app.get("/api/research/{run_id}/stream", tags=["research"])
async def research_stream(run_id: str):
    """SSE 实时进度推送。"""
    if RUN_MANAGER.get(run_id) is None:
        return JSONResponse(status_code=404, content={"status": "error", "reason": "任务不存在。"})

    async def gen():
        async for event in RUN_MANAGER.stream(run_id):
            yield _sse_event(event)
            if event.get("type") == "done":
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/research/{run_id}/decision", tags=["research"], dependencies=[Depends(require_api_key)])
async def research_decision(run_id: str, req: ResearchDecision):
    """审批决策：approve / reject / edit，从 LangGraph checkpoint 恢复。"""
    action = (req.action or "approve").lower()
    if action not in ("approve", "reject", "edit"):
        return JSONResponse(
            status_code=400, content={"status": "error", "reason": "action 必须为 approve / reject / edit。"}
        )

    ok = await RUN_MANAGER.resume(run_id, {"action": action, "comment": req.comment, "edited_text": req.edited_text})
    if not ok:
        return JSONResponse(status_code=409, content={"status": "error", "reason": "任务不存在或不在等待审批状态。"})
    logger.info("审批决策已提交：run_id=%s，action=%s", run_id, action)
    return {"status": "accepted", "run_id": run_id, "action": action}
