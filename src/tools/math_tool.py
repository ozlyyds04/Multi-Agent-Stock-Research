import numpy as np
from typing import List, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


def basic_return_stats(closes: List[float]) -> Dict[str, Any]:
    logger.debug("正在为 %d 个数据点计算收益率统计", len(closes))
    # 过滤缺失/非正值：价格 0 会让收益率除 0 产生 inf，污染全部统计
    arr = np.array([c for c in closes if isinstance(c, (int, float)) and c > 0], dtype=float)
    if arr.size < 2:
        logger.warning("数据不足，无法计算统计指标")
        return {"mean": None, "vol": None, "count": int(arr.size)}
    rets = np.diff(arr) / arr[:-1]
    stats = {
        "mean": float(np.mean(rets)),
        "vol": float(np.std(rets)),
        "min": float(np.min(rets)),
        "max": float(np.max(rets)),
        "count": int(rets.size),
    }
    logger.debug("收益率统计已计算完成：%s", stats)
    return stats
