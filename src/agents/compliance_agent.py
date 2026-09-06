from typing import List
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


class ComplianceAgent:
    def __init__(self, llm: ChatOpenAI | ChatDeepSeek, forbidden: List[str], disclosure: List[str]):
        self.llm = llm
        self.forbidden = forbidden
        self.disclosure = disclosure or []
        self.prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是一名合规官，负责执行法律安全、中性和信息披露要求。"
                    "你必须改写内容，删除被禁止的表述，在需要时添加披露声明，并确保语言符合监管要求。"
                    "你不得新增任何分析内容。",
                ),
                (
                    "user",
                    """分析师备注：
            {note}

            禁用表述：{forbidden}
            披露声明（必须原样保留在输出结尾，不得改写或省略）：
            {disclosure}
            请返回一份合规、中性的"最终备注"。""",
                ),
            ]
        )
        self.retry_cfg = RetryConfig(max_retries=2, base_delay_sec=0.6, max_delay_sec=6.0, timeout_sec=90.0)
        logger.info("合规智能体已初始化，共 %d 条禁用词、%d 条披露声明。", len(forbidden), len(self.disclosure))

    def run(self, note: str) -> str:
        logger.info("正在执行合规检查。")
        chain = self.prompt | self.llm
        payload = {"note": note, "forbidden": self.forbidden, "disclosure": "\n".join(self.disclosure) or "（无）"}

        def _invoke():
            try:
                return chain.invoke(payload)
            except Exception as e:
                if _is_retryable_llm_exc(e):
                    raise RetryableError(str(e))
                raise

        out = retry_call(_invoke, cfg=self.retry_cfg, op_name="llm_compliance_invoke", logger=logger)
        logger.info("合规检查完成。")
        return out.content
