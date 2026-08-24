from typing import Dict, Any, List
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI
from src.utils.logger import get_logger
from src.tools.price_tool import fetch_price_history
from src.tools.fundamentals_tool import fetch_income_statement, fetch_key_metrics
from src.tools.news_tool import fetch_news_feeds

logger = get_logger(__name__)


class DataAgent:
    def __init__(self, llm: ChatOpenAI | ChatDeepSeek, rss_templates: List[str], max_news: int = 6):
        self.llm = llm
        self.rss = rss_templates
        self.max_news = max_news
        logger.info("数据智能体已初始化，max_news=%s", max_news)

    def run(self, symbol: str, days: int = 30) -> Dict[str, Any]:
        logger.info("正在为 %s 获取数据（最近 %d 天）", symbol, days)

        prices = fetch_price_history(symbol, days)
        inc = fetch_income_statement(symbol, limit=2)
        km = fetch_key_metrics(symbol)
        news = fetch_news_feeds(symbol, self.rss, max_items=self.max_news)

        # 收集非致命工具错误，供下游报告使用
        tool_errors = []
        if isinstance(prices, dict) and prices.get("__error__"):
            tool_errors.append(prices["__error__"])
        if isinstance(inc, dict) and inc.get("__error__"):
            tool_errors.append(inc["__error__"])
        if isinstance(km, dict) and km.get("__error__"):
            tool_errors.append(km["__error__"])
        # news_tool 可能在列表条目中返回错误标记
        if isinstance(news, list):
            for n in news:
                if isinstance(n, dict) and n.get("__error__"):
                    tool_errors.append({"where": "news", "message": n.get("__error__"), "source": n.get("source")})

        logger.info(
            "%s 数据获取完成：%d 条价格记录，%d 条新闻",
            symbol,
            len(prices.get("data", [])) if isinstance(prices, dict) else 0,
            len(news) if isinstance(news, list) else 0,
        )

        fundamentals = {
            "income_statement": inc.get("income_statement", []) if isinstance(inc, dict) else [],
            "key_metrics_ttm": km.get("key_metrics_ttm", []) if isinstance(km, dict) else [],
            "__errors__": [e for e in [inc.get("__error__") if isinstance(inc, dict) else None,
                                      km.get("__error__") if isinstance(km, dict) else None] if e],
        }

        return {
            "symbol": symbol,
            "prices": prices if isinstance(prices, dict) else {"symbol": symbol, "data": []},
            "fundamentals": fundamentals,
            "news": news if isinstance(news, list) else [],
            "__errors__": tool_errors,
        }
