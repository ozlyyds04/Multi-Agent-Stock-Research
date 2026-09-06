import os
import pandas as pd
import matplotlib

matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))
import matplotlib.pyplot as plt
from typing import List, Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 中文字体支持：图表标签已中文化（日期/价格等）
matplotlib.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "PingFang SC",
    "DejaVu Sans",
]
matplotlib.rcParams["axes.unicode_minus"] = False


def save_price_plot(dates: List[str], closes: List[float], reported_currency, outpath: str) -> Optional[str]:
    """
    生成价格图表。成功返回 outpath，失败返回 None（绝不返回未写出的路径，
    否则调用方会把指向不存在文件的 <img> 嵌进报告）。
    """
    logger.debug("正在生成价格图表：%s", outpath)
    out_dir = os.path.dirname(outpath)
    try:
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        df = pd.DataFrame({"date": dates, "close": closes})
        df["date"] = pd.to_datetime(df["date"], utc=True)
        plt.figure(figsize=(8, 4))
        plt.plot(df["date"], df["close"])
        plt.title("收盘价")
        plt.xlabel("日期")
        plt.ylabel(f"价格（{reported_currency}）")
        plt.tight_layout()
        plt.savefig(outpath, dpi=120)
        logger.info("价格图表已保存到 %s", outpath)
        return outpath
    except Exception as e:
        logger.error("图表生成失败：%s", e)
        return None
    finally:
        plt.close()  # 异常路径也要关闭 figure，否则 matplotlib figure 持续泄漏
