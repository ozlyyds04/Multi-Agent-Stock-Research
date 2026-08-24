from __future__ import annotations

from typing import Any, Dict

from src.tools.alpha_vantage_tool import fetch_quote as fetch_av_quote
from src.tools.hk_tool import fetch_hk_quote, is_hk_symbol


def fetch_quote(symbol: str, api_key: Any = None) -> Dict[str, Any]:
    """
    按市场分发预检：
      - 港股：AKShare（东方财富）验证代码有效性
      - 其余（美股/A 股）：Alpha Vantage GLOBAL_QUOTE
    """
    if is_hk_symbol(symbol):
        return fetch_hk_quote(symbol)
    return fetch_av_quote(symbol, api_key)
