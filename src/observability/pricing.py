"""
LLM 成本估算：按 token 数 + 每百万 token 单价计算。

价格为占位近似值，可用环境变量覆盖（PRICING_<MODEL>_IN / PRICING_<MODEL>_OUT）。
"""

from __future__ import annotations

import os
from typing import Dict, Tuple

_PRICES: Dict[str, Tuple[float, float]] = {
    "deepseek-v4-flash": (0.28, 1.10),
    "deepseek-v4-pro": (0.55, 2.20),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-5": (1.25, 10.00),
}


def _price_for(model: str) -> Tuple[float, float]:
    key = "".join(ch for ch in model.upper() if ch.isalnum())
    in_env = os.getenv(f"PRICING_{key}_IN")
    out_env = os.getenv(f"PRICING_{key}_OUT")
    if in_env and out_env:
        return float(in_env), float(out_env)
    return _PRICES.get(model, (1.0, 1.0))


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_price, out_price = _price_for(model)
    return input_tokens / 1_000_000 * in_price + output_tokens / 1_000_000 * out_price
