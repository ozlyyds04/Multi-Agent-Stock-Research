from __future__ import annotations

import re
from typing import Any, Dict, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 港股代码：AKShare 使用 5 位补零裸代码（如 00700/01810），接受 4-5 位裸码或带 .HK 后缀
_HK_RE = re.compile(r"^\d{4,5}(\.HK)?$", re.IGNORECASE)


def is_hk_symbol(symbol: str) -> bool:
    """判断股票代码是否为港股（1810 / 01810 / 1810.HK / 9988.HK 等）。"""
    s = (symbol or "").strip().upper()
    return bool(_HK_RE.match(s))


def to_hk_bare(symbol: str) -> str:
    """转为 AKShare 使用的 5 位补零裸代码：1810/01810/1810.HK -> 01810；9988.HK -> 09988。"""
    s = (symbol or "").strip().upper().split(".")[0]
    if not s.isdigit():
        raise ValueError(f"非法的港股代码：{symbol}")
    return f"{int(s):05d}"


def _num(v: Any) -> Optional[float]:
    """解析数值；缺失/非法返回 None（绝不能返回 0.0——价格 0 会被当作真实行情污染统计）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    if f in (float("inf"), float("-inf")):
        return None
    return f


def _is_transient_error(msg: str) -> bool:
    """按错误消息粗分：网络/超时类瞬时错误可重试，其余视为确定性失败。"""
    low = msg.lower()
    return any(
        k in low
        for k in ("timeout", "timed out", "connection", "temporarily", "reset by peer", "502", "503", "504")
    )


def fetch_hk_daily(symbol: str, days: int = 30) -> Dict[str, Any]:
    """
    通过 AKShare（东方财富）获取港股日线，返回与 Alpha Vantage 一致的记录格式。

    返回：
      {"ok": True, "data": [{"Date", "Open", "High", "Low", "Close", "Volume"}, ...]}
      {"ok": False, "retryable": bool, "message": ...}
    """
    bare = to_hk_bare(symbol)
    try:
        import akshare as ak

        df = ak.stock_hk_daily(symbol=bare)
    except Exception as e:
        retryable = _is_transient_error(str(e))
        logger.warning("获取 %s 的港股日线失败（retryable=%s）：%s", bare, retryable, e)
        return {
            "ok": False,
            "rate_limited": False,
            "retryable": retryable,
            "message": str(e),
        }

    if df is None or getattr(df, "empty", True):
        return {
            "ok": False,
            "rate_limited": False,
            "retryable": False,
            "message": f"未获取到 {bare} 的港股日线数据（代码可能无效）",
        }

    try:
        df = df.sort_values("date")
    except Exception:
        pass

    records = []
    for _, row in df.tail(days).iterrows():
        close = _num(row.get("close"))
        if close is None:
            continue  # 缺收盘价的行没有分析价值，跳过而不是伪装成 0
        records.append(
            {
                "Date": str(row.get("date", "")),
                "Open": _num(row.get("open")),
                "High": _num(row.get("high")),
                "Low": _num(row.get("low")),
                "Close": close,
                "Volume": _num(row.get("volume")),
            }
        )

    logger.info("已从 AKShare 获取 %s 的港股日线：%d 条记录", bare, len(records))
    return {"ok": True, "data": records}


def fetch_hk_quote(symbol: str) -> Dict[str, Any]:
    """
    预检港股代码：验证代码是否存在，并返回最新收盘价。

    返回：
      {"ok": True, "valid": True, "symbol": ..., "price": ...}  有效
      {"ok": True, "valid": False, "message": ...}              无效
      {"ok": False, "rate_limited": bool, "message": ...}       请求失败
    """
    try:
        res = fetch_hk_daily(symbol, days=1)
    except Exception as e:
        return {"ok": False, "rate_limited": False, "message": str(e)}

    if not res.get("ok") or not res.get("data"):
        msg = res.get("message") or "港股代码无效或没有当前市场数据"
        return {"ok": True, "valid": False, "message": str(msg)}

    last = res["data"][-1]
    return {
        "ok": True,
        "valid": True,
        "symbol": to_hk_bare(symbol),
        "price": last["Close"],
    }
