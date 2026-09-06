from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from src.utils.logger import get_logger
from src.utils.resilience import RetryConfig, retry_call, RetryableError

logger = get_logger(__name__)


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


class SupervisorAgent:
    def __init__(self, llm: ChatOpenAI | ChatDeepSeek):
        self.llm = llm
        self.prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是专业股票研究报告的总编辑和最终审核人。"
                    "你不生成原始分析。你负责评估、重构并定稿其他智能体生成的内容。你的职责包括：\n"
                    "- 确保所有章节完整且撰写专业\n"
                    "- 删除任何不完整、重复或低质量的章节\n"
                    "- 执行清晰规范的机构级研究报告结构\n"
                    "- 跳过任何无法完整成文的章节\n\n"
                    "你绝不允许输出不完整的章节、占位符或未完成的标题。",
                ),
                (
                    "user",
                    """输入：
             - 股票代码：{symbol}
             - 数据摘要：{data_summary}
             - 合规备注：{final_note}

             你的任务：
             生成一份最终、可发布的 Markdown 研究报告。

             规则：
             1. 只包含能够完整完成的章节。
             2. 省略任何信息不足的章节。
             3. 统一语气、结构和格式。
             4. 确保输出呈现机构级研究报告的风格。

             必填章节（仅当内容完整时）：
             - 标题
             - 概览
             - 价格走势
             - 基本面
             - 估值/技术面
             - 新闻头条及解读
             - 关键关注事项
             - 风险
             - 短期展望
             - 数据来源

             只输出最终报告。
             """,
                ),
            ]
        )
        self.retry_cfg = RetryConfig(max_retries=2, base_delay_sec=0.6, max_delay_sec=6.0, timeout_sec=120.0)
        logger.info("主管智能体已初始化。")

    def run(self, symbol: str, data_summary: str, final_note: str) -> str:
        logger.info("正在为 %s 撰写最终报告", symbol)
        chain = self.prompt | self.llm
        payload = {"symbol": symbol, "data_summary": data_summary, "final_note": final_note}

        def _invoke():
            try:
                return chain.invoke(payload)
            except Exception as e:
                if _is_retryable_llm_exc(e):
                    raise RetryableError(str(e))
                raise

        out = retry_call(_invoke, cfg=self.retry_cfg, op_name="llm_supervisor_invoke", logger=logger)
        logger.info("%s 的最终报告已生成", symbol)
        return out.content
