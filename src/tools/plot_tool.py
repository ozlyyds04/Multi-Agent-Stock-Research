import os
import pandas as pd
import matplotlib
matplotlib.use(os.environ.get("MPLBACKEND", "Agg"))
import matplotlib.pyplot as plt
from typing import List
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


def save_price_plot(dates: List[str], closes: List[float], reported_currency, outpath: str) -> str:
    logger.debug("正在生成价格图表：%s", outpath)
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    try:
        df = pd.DataFrame({"date": dates, "close": closes})
        df["date"] = pd.to_datetime(df["date"],utc=True)
        plt.figure(figsize=(8, 4))
        plt.plot(df["date"], df["close"])
        plt.title("收盘价")
        plt.xlabel("日期")
        plt.ylabel(f"价格（{reported_currency}）")
        plt.tight_layout()
        plt.savefig(outpath, dpi=120)
        plt.close()
        logger.info("价格图表已保存到 %s", outpath)
        plt.close()
        return outpath
    except Exception as e:
        logger.error("图表生成失败：%s", e)
        return outpath
