from typing import Dict, Any, List

import json

from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from src.utils.logger import get_logger
from src.utils.resilience import RetryConfig, retry_call, RetryableError

logger = get_logger(__name__)

_TOOL_SYSTEM_HINT = (
    "如果输入的价格、基本面或新闻数据缺失、为空或明显不完整，你可以调用工具补充数据；"
    "数据足够时直接分析，不要调用工具。最终只输出分析文本（不超过 250 字），不要输出 JSON。"
)


def _is_retryable_llm_exc(e: Exception) -> bool:
    msg = str(e).lower()
    return any(
        k in msg
        for k in [
            "timeout",
            "timed out",
            "rate limit",
            "429",
            "temporarily",
            "unavailable",
            "502",
            "503",
            "504",
            "connection",
            "server error",
        ]
    )


@tool
def fetch_quote(symbol: str) -> str:
    """获取股票的最新报价，用于补充缺失的价格信息或核对最新价。"""
    from src.tools.quote_tool import fetch_quote as _fetch_quote

    return json.dumps(_fetch_quote(symbol), ensure_ascii=False)


@tool
def fetch_price_history(symbol: str, days: int = 30) -> str:
    """获取股票最近 N 个交易日的日线价格数据（Date/Open/High/Low/Close/Volume）。"""
    from src.tools.price_tool import fetch_price_history as _fetch_price_history

    return json.dumps(_fetch_price_history(symbol, days), ensure_ascii=False)


@tool
def fetch_income_statement(symbol: str) -> str:
    """获取股票利润表摘要（营收、净利润、每股收益）。"""
    from src.tools.fundamentals_tool import fetch_income_statement as _fetch_inc

    return json.dumps(_fetch_inc(symbol, limit=2), ensure_ascii=False)


@tool
def fetch_key_metrics(symbol: str) -> str:
    """获取股票最新关键指标（ROE、毛利率等）。"""
    from src.tools.fundamentals_tool import fetch_key_metrics as _fetch_km

    return json.dumps(_fetch_km(symbol), ensure_ascii=False)


_ANALYST_TOOLS: List = [
    fetch_quote,
    fetch_price_history,
    fetch_income_statement,
    fetch_key_metrics,
]
_TOOL_MAP = {t.name: t for t in _ANALYST_TOOLS}


class AnalystAgent:
    def __init__(self, llm: ChatOpenAI | ChatDeepSeek):
        self.llm = llm
        self.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "你是一名股票研究分析师。请撰写简洁、专业的分析。"),
                (
                    "user",
                    """输入：
                    - 股票代码：{symbol}
                    - 价格数据（近期）：{price_excerpt}
                    - 基本面（利润表摘要）：{is_excerpt}
                    - 关键指标 TTM 摘要：{km_excerpt}
                    - 最新头条新闻：{news_excerpt}

                    任务：
                    1) 总结最近 {days} 天的价格走势。
                    2) 提炼 2-3 条关键基本面要点。
                    3) 指出 2 条值得关注的新闻标题及其影响。
                    4) 提供一份中性的"分析师备注"（不超过 250 字）。

                    人工审批意见（如有，请据此修改分析）：{feedback}""",
                ),
            ]
        )
        self.retry_cfg = RetryConfig(max_retries=2, base_delay_sec=0.6, max_delay_sec=6.0, timeout_sec=90.0)
        # 3 轮 = 模型最多 3 次 invoke：留一次让模型消化工具结果后再作答
        self.max_tool_rounds = 3
        logger.info("分析智能体已初始化。")

    @staticmethod
    def _has_data_gaps(bundle: Dict[str, Any]) -> bool:
        """价格、利润表摘要、关键指标任一为空，即视为数据缺口，需要工具补充。"""
        prices = (bundle.get("prices") or {}).get("data") or []
        fundamentals = bundle.get("fundamentals") or {}
        inc = fundamentals.get("income_statement")
        km = fundamentals.get("key_metrics_ttm")
        if isinstance(inc, dict):
            inc = inc.get("income_statement", [])
        if isinstance(km, dict):
            km = km.get("key_metrics_ttm", [])
        return not (prices and inc and km)

    def _user_messages(self, payload: Dict[str, Any]):
        base = self.prompt.format_messages(**payload)
        system_text = str(base[0].content) + " " + _TOOL_SYSTEM_HINT
        return SystemMessage(content=system_text), HumanMessage(content=str(base[1].content))

    def _run_plain(self, payload: Dict[str, Any]) -> str:
        chain = self.prompt | self.llm

        def _invoke():
            try:
                return chain.invoke(payload)
            except Exception as e:
                if _is_retryable_llm_exc(e):
                    raise RetryableError(str(e))
                raise

        out = retry_call(_invoke, cfg=self.retry_cfg, op_name="llm_analyst_invoke", logger=logger)
        return out.content

    def _run_with_tools(self, payload: Dict[str, Any]) -> str:
        """工具调用模式：模型可自主调用工具补充缺失数据。"""
        llm_with_tools = self.llm.bind_tools(_ANALYST_TOOLS)
        system_msg, user_msg = self._user_messages(payload)
        messages = [system_msg, user_msg]

        def _invoke():
            last_content = ""
            for _ in range(self.max_tool_rounds):
                try:
                    resp = llm_with_tools.invoke(messages)
                except Exception as e:
                    if _is_retryable_llm_exc(e):
                        raise RetryableError(str(e))
                    raise
                calls = getattr(resp, "tool_calls", None) or []
                if not calls:
                    return resp.content if resp.content else ""
                last_content = resp.content or last_content
                for tc in calls:
                    logger.info("分析智能体调用工具：%s（参数：%s）", tc.get("name"), tc.get("args"))
                messages.append(resp)
                for tc in calls:
                    name = tc.get("name", "")
                    args = tc.get("args", {}) or {}
                    tool_fn = _TOOL_MAP.get(name)
                    if tool_fn is None:
                        result = json.dumps({"error": f"未知工具：{name}"}, ensure_ascii=False)
                    else:
                        try:
                            result = tool_fn.invoke(args)
                        except Exception as e:
                            result = json.dumps({"error": str(e)}, ensure_ascii=False)
                    messages.append(ToolMessage(content=result, tool_call_id=tc.get("id", "")))
            # 轮次用尽是确定性结果，不值得整轮重试：返回已有文本（通常非空），
            # 空文本时抛非重试异常直接走 _run_plain 回退
            if last_content.strip():
                logger.warning("分析智能体工具调用轮次用尽，返回已有文本")
                return last_content
            raise RuntimeError("分析智能体工具调用轮次用尽，未得到最终文本")

        return retry_call(_invoke, cfg=self.retry_cfg, op_name="llm_analyst_tools", logger=logger)

    def run(self, bundle: Dict[str, Any], days: int, feedback: str = "") -> str:
        symbol = bundle["symbol"]
        logger.info("正在为 %s 生成分析师备注", symbol)

        payload = {
            "symbol": symbol,
            "price_excerpt": str(bundle["prices"].get("data", [])[-5:]),
            "is_excerpt": str(bundle["fundamentals"].get("income_statement", [])[:1]),
            "km_excerpt": str(bundle["fundamentals"].get("key_metrics_ttm", [])[:1]),
            "news_excerpt": str(bundle.get("news", [])[:3]),
            "days": days,
            "feedback": feedback or "",
        }

        if self._has_data_gaps(bundle):
            try:
                return self._run_with_tools(payload)
            except Exception as e:
                logger.warning("分析智能体工具调用失败，回退到直接分析：%s", e)
                # 回退时显式声明数据缺失，防止模型基于空 excerpts 编造走势
                payload["feedback"] = (
                    (feedback or "")
                    + "\n\n（注意：价格/基本面/新闻数据存在缺失，无法调用工具补充。"
                    + "对缺失的数据请直接说明『数据不可用』，不要编造走势或数字。）"
                ).strip()

        return self._run_plain(payload)
