from __future__ import annotations

import os
from typing import Any, Dict, Optional

import requests

from src.utils.helper import is_rate_limit_error
from src.utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://www.alphavantage.co/query"
API_KEY_ENV = "ALPHA_VANTAGE_API_KEY"


def get_api_key(api_key: Optional[str] = None) -> str:
    """返回 API 密钥，优先取参数，其次读 .env 中的 ALPHA_VANTAGE_API_KEY。"""
    key = (api_key or os.getenv(API_KEY_ENV) or "").strip()
    if not key:
        raise ValueError(f"缺少 Alpha Vantage API 密钥（请配置 {API_KEY_ENV}）")
    return key


def _request(params: Dict[str, Any], api_key: Optional[str] = None) -> Dict[str, Any]:
    key = get_api_key(api_key)
    try:
        r = requests.get(BASE_URL, params={**params, "apikey": key}, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("Alpha Vantage 请求失败：%s", e)
        return {"Error Message": str(e)}


def is_rate_limited_response(data: Dict[str, Any]) -> bool:
    """
    Alpha Vantage 限流/Key 异常时返回 Note 或 Information 字段，而不是数据。
    """
    if "Note" in data or "Information" in data:
        return True
    return is_rate_limit_error(ValueError(str(data)))


def fetch_quote(symbol: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    获取最新报价，用于预检股票代码。

    返回：
      {"ok": True, "valid": True, "symbol": ..., "price": ...}  有效
      {"ok": True, "valid": False, "message": ...}              无效/无行情
      {"ok": False, "rate_limited": bool, "message": ...}       请求失败/限流
    """
    data = _request({"function": "GLOBAL_QUOTE", "symbol": symbol}, api_key)
    if is_rate_limited_response(data):
        msg = data.get("Note") or data.get("Information") or "Alpha Vantage 请求受限"
        logger.warning("Alpha Vantage 预检请求受限（%s）：%s", symbol, msg)
        return {"ok": False, "rate_limited": True, "message": str(msg)}

    if "Error Message" in data:
        return {"ok": False, "rate_limited": False, "message": str(data["Error Message"])}

    quote = data.get("Global Quote") or {}
    price = quote.get("05. price")
    if not quote or not price:
        return {"ok": True, "valid": False, "message": "股票代码无效或没有当前市场数据"}

    return {
        "ok": True,
        "valid": True,
        "symbol": quote.get("01. symbol", symbol),
        "price": price,
    }


def fetch_daily_series(symbol: str, days: int = 30, api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    获取最近 N 个交易日的日线行情（compact 模式约 100 天，足够本项目使用）。

    返回：
      {"ok": True, "data": [{"Date", "Open", "High", "Low", "Close", "Volume"}, ...]}
      {"ok": False, "rate_limited": bool, "message": ...}
    """
    data = _request(
        {"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "compact"},
        api_key,
    )
    if is_rate_limited_response(data):
        msg = data.get("Note") or data.get("Information") or "Alpha Vantage 请求受限"
        return {"ok": False, "rate_limited": True, "message": str(msg)}

    series = data.get("Time Series (Daily)")
    if not series:
        msg = data.get("Error Message") or f"未获取到 {symbol} 的日线数据"
        return {"ok": False, "rate_limited": False, "message": str(msg)}

    def _num(v: Any) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    records = []
    # 统一为时间升序（最旧在前、最新在后），与 AKShare 港股日线保持一致
    for date, bar in reversed(sorted(series.items(), reverse=True)[:days]):
        records.append({
            "Date": date,
            "Open": _num(bar.get("1. open")),
            "High": _num(bar.get("2. high")),
            "Low": _num(bar.get("3. low")),
            "Close": _num(bar.get("4. close")),
            "Volume": _num(bar.get("5. volume")),
        })

    logger.info("已从 Alpha Vantage 获取 %s 的 %d 条日线记录", symbol, len(records))
    return {"ok": True, "data": records}
