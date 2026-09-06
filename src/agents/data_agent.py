import concurrent.futures as cf
from typing import Dict, Any, List

from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from src.utils.logger import get_logger
from src.observability import metrics
from src.runtime.backends import get_cache
from src.tools.price_tool import fetch_price_history
from src.tools.fundamentals_tool import fetch_income_statement, fetch_key_metrics
from src.tools.news_tool import fetch_news_feeds

logger = get_logger(__name__)

# 基本面（财报）缓存：同一股票短期内不重复请求上游，缓解 402/429
_FUND_TTL_SEC = 6 * 3600


def reset_fund_cache() -> None:
    """测试用：清空基本面缓存，避免跨用例污染。"""
    try:
        get_cache().reset()
    except Exception:
        pass


class DataAgent:
    def __init__(self, llm: ChatOpenAI | ChatDeepSeek, rss_templates: List[str], max_news: int = 6):
        self.llm = llm
        self.rss = rss_templates
        self.max_news = max_news
        logger.info("数据智能体已初始化，max_news=%s", max_news)

    def run(self, symbol: str, days: int = 30) -> Dict[str, Any]:
        logger.info("正在为 %s 获取数据（最近 %d 天）", symbol, days)

        cache = get_cache()
        cache_key = f"fund:{symbol}"
        cached = cache.get(cache_key)
        if cached and "inc" in cached and "km" in cached:
            inc, km = cached["inc"], cached["km"]
            logger.info("命中基本面缓存：%s", symbol)
            fetch_fund = False
        else:
            inc = km = None
            fetch_fund = True

        # 并行拉取价格 / 基本面 / 新闻（各自带错误兜底，避免单源失败拖垮整条）
        with cf.ThreadPoolExecutor(max_workers=4) as ex:
            f_prices = ex.submit(self._safe_call, fetch_price_history, symbol, days)
            f_news = ex.submit(
                self._safe_call,
                fetch_news_feeds,
                symbol,
                self.rss,
                self.max_news,
                is_news=True,
            )
            f_inc = f_km = None
            if fetch_fund:
                f_inc = ex.submit(self._safe_call, fetch_income_statement, symbol, 2)
                f_km = ex.submit(self._safe_call, fetch_key_metrics, symbol)

            prices = f_prices.result()
            news = f_news.result()
            if f_inc is not None:
                inc = f_inc.result()
                km = f_km.result()
                # 仅缓存成功的基本面，失败则下次重试
                if not self._has_error(inc) and not self._has_error(km):
                    cache.set(cache_key, {"inc": inc, "km": km}, _FUND_TTL_SEC)

        # 收集非致命工具错误，供下游报告使用
        tool_errors = []
        if isinstance(prices, dict) and prices.get("__error__"):
            tool_errors.append(prices["__error__"])
            metrics.inc_source_error("price")
        if isinstance(inc, dict) and inc.get("__error__"):
            tool_errors.append(inc["__error__"])
            metrics.inc_source_error("fundamentals_income")
        if isinstance(km, dict) and km.get("__error__"):
            tool_errors.append(km["__error__"])
            metrics.inc_source_error("fundamentals_metrics")
        # news_tool 可能在列表条目中返回错误标记
        if isinstance(news, list):
            for n in news:
                if isinstance(n, dict) and n.get("__error__"):
                    tool_errors.append({"where": "news", "message": n.get("__error__"), "source": n.get("source")})
                    metrics.inc_source_error("news")

        logger.info(
            "%s 数据获取完成：%d 条价格记录，%d 条新闻",
            symbol,
            len(prices.get("data", [])) if isinstance(prices, dict) else 0,
            len(news) if isinstance(news, list) else 0,
        )

        fundamentals = {
            "income_statement": inc.get("income_statement", []) if isinstance(inc, dict) else [],
            "key_metrics_ttm": km.get("key_metrics_ttm", []) if isinstance(km, dict) else [],
            "__errors__": [
                e
                for e in [
                    inc.get("__error__") if isinstance(inc, dict) else None,
                    km.get("__error__") if isinstance(km, dict) else None,
                ]
                if e
            ],
        }

        return {
            "symbol": symbol,
            "prices": prices if isinstance(prices, dict) else {"symbol": symbol, "data": []},
            "fundamentals": fundamentals,
            "news": news if isinstance(news, list) else [],
            "__errors__": tool_errors,
        }

    def _safe_call(self, fn, *args, is_news: bool = False):
        """调用数据工具并兜底：异常转为错误结构，而不是让整条流水线崩溃。"""
        try:
            return fn(*args)
        except Exception as e:
            logger.error("%s 调用失败：%s", getattr(fn, "__name__", "工具"), e)
            if is_news:
                return [{"__error__": str(e), "source": "news"}]
            return {"__error__": {"where": getattr(fn, "__name__", "工具"), "message": str(e)}}

    @staticmethod
    def _has_error(obj) -> bool:
        if isinstance(obj, dict):
            return bool(obj.get("__error__"))
        if isinstance(obj, list):
            return any(isinstance(n, dict) and n.get("__error__") for n in obj)
        return False
