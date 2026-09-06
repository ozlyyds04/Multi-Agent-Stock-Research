"""股票代码有效性预检的共享实现（供 API、RunManager、Celery runner 复用）。"""

from __future__ import annotations

import os

from src.tools.quote_tool import fetch_quote


def precheck_symbol(symbol: str) -> tuple:
    """返回 (ok, error, suggested_action)。ok=False 表示无效/不可用，error 为原因。"""
    try:
        quote = fetch_quote(symbol)
    except Exception as e:
        # 底层异常（如缺少 API key）转结构化返回，不向上穿透
        return False, f"预检请求失败：{e}", "请检查 .env 中的数据源配置（如 ALPHA_VANTAGE_API_KEY）。"
    if quote.get("ok") and not quote.get("valid"):
        return (
            False,
            f"股票代码 {symbol} 无效或没有当前市场数据（可能已退市或不活跃）。",
            "请核实股票代码或尝试其他股票。",
        )
    if not quote.get("ok"):
        skip = os.getenv("SKIP_PRECHECK_ON_RATELIMIT", "true").lower() not in ("0", "false", "no")
        if quote.get("rate_limited") and skip:
            return True, None, None
        return False, f"无法验证股票代码 {symbol}：{quote.get('message')}", "请检查 .env 中的数据源配置。"
    return True, None, None
