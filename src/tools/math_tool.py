import numpy as np
from typing import List, Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


def basic_return_stats(closes: List[float]) -> Dict[str, Any]:
    logger.debug("正在为 %d 个数据点计算收益率统计", len(closes))
    arr = np.array(closes, dtype=float)
    if arr.size < 2:
        logger.warning("数据不足，无法计算统计指标")
        return {"mean": None, "vol": None, "count": int(arr.size)}
    rets = np.diff(arr) / arr[:-1]
    stats = {
        "mean": float(np.mean(rets)),
        "vol": float(np.std(rets)),
        "min": float(np.min(rets)),
        "max": float(np.max(rets)),
        "count": int(rets.size)
    }
    logger.debug("收益率统计已计算完成：%s", stats)
    return stats
