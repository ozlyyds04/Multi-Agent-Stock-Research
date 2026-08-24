from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import os
from src.graph.orchestrator import run_pipeline, resume_pipeline
from src.utils.logger import get_logger
from src.tools.quote_tool import fetch_quote
from src.guardrails.inputs import normalize_symbol
from dotenv import load_dotenv

logger = get_logger(__name__)

# 加载 .env（预检 fetch_quote 需要 ALPHA_VANTAGE_API_KEY / OPENAI_API_KEY）
load_dotenv()

app = FastAPI(
    title="自动股票研究多智能体系统",
    description="LangGraph 多智能体编排（数据、分析、合规、主管）",
    version="0.1.0",
)


class RunRequest(BaseModel):
    symbol: str
    days: int = 30
    human: bool = False
    outdir: str = "artifacts"


class ApproveRequest(BaseModel):
    run_id: str
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



@app.post("/analyze", tags=["analysis"])
def analyze_stock(req: RunRequest):
    symbol_uppercase = normalize_symbol(req.symbol)
    if not symbol_uppercase.replace("-", "").replace(".", "").isalnum():
        return JSONResponse(status_code=400, content={"status": "error", "reason": "股票代码格式无效。"})

    if req.days < 5 or req.days > 15:
        return JSONResponse(status_code=400, content={"status": "error", "reason": "天数必须在 5 到 15 之间。"})

    # 预检：按市场验证股票代码（港股走 AKShare，其余走 Alpha Vantage）
    try:
        quote = fetch_quote(symbol_uppercase)
        if quote.get("ok") and not quote.get("valid"):
            error_message = (
                f"股票代码 '{symbol_uppercase}' 无效或没有当前市场数据（可能已退市）。"
                "请核实股票代码或尝试其他股票。"
            )
            logger.error(error_message)
            return JSONResponse(
                status_code=400,
                content={"status": "error", "reason": error_message}
            )
        if not quote.get("ok"):
            skip_on_limit = os.getenv("SKIP_PRECHECK_ON_RATELIMIT", "true").lower() not in ("0", "false", "no")
            if quote.get("rate_limited") and skip_on_limit:
                logger.warning(
                    "对 %s 的预检因行情源限流失败，跳过预检继续执行：%s",
                    symbol_uppercase,
                    quote.get("message"),
                )
            else:
                logger.error("对 %s 的预检出错：%s", symbol_uppercase, quote.get("message"))
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "reason": f"无法验证股票代码 {symbol_uppercase}：{quote.get('message')}"}
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
        os.makedirs(req.outdir, exist_ok=True)
        result = run_pipeline(symbol_uppercase, req.days, req.outdir, req.human)

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
                    "suggested_action": result.get("suggested_action")
                }
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
                "message": "报告生成成功"
            }
        )

    except Exception as e:
        logger.exception("为 %s 生成报告时出错", req.symbol)
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "reason": "生成报告时发生内部服务器错误。",
                "suggested_action": "请查看服务器日志以了解详情。"
            }
        )


@app.post("/analyze/approve", tags=["analysis"])
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

