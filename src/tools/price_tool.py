from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

from src.utils.logger import get_logger
from src.utils.resilience import RetryConfig, retry_call, RetryableError
from src.tools.alpha_vantage_tool import fetch_daily_series
from src.tools.hk_tool import fetch_hk_daily, is_hk_symbol

logger = get_logger(__name__)

# 本地价格缓存：避免同一代码在短时间内重复请求数据源（限流友好）
_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".cache",
    "prices",
)
_CACHE_TTL_HOURS = float(os.getenv("PRICE_CACHE_TTL_HOURS", "6"))


def _cache_path(symbol: str, days: int) -> str:
    return os.path.join(_CACHE_DIR, f"{symbol.upper()}_{days}d.json")


def _load_cache(symbol: str, days: int):
    path = _cache_path(symbol, days)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        cached_at = datetime.fromisoformat(payload.get("cached_at", ""))
        if datetime.now(timezone.utc) - cached_at > timedelta(hours=_CACHE_TTL_HOURS):
            return None
        return payload.get("data"), payload.get("meta") or {}
    except Exception as e:
        logger.warning("读取价格缓存失败，忽略缓存：%s", e)
        return None


def _save_cache(symbol: str, days: int, records: list, meta: Dict[str, Any]) -> None:
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        payload = {
            "symbol": symbol.upper(),
            "days": days,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "data": records,
            "meta": meta,
        }
        with open(_cache_path(symbol, days), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("保存价格缓存失败：%s", e)


def _normalize_order(records: list) -> list:
    """统一为时间升序（最旧在前、最新在后），兼容历史缓存中的倒序数据。"""
    try:
        return sorted(records, key=lambda r: str(r.get("Date") or ""))
    except Exception:
        return records


def fetch_price_history(
    symbol: str,
    days: int = 30,
    *,
    retry_cfg: Optional[RetryConfig] = None,
) -> Dict[str, Any]:
    """
    按市场获取价格历史（港股走 AKShare，美股/A 股走 Alpha Vantage），具备：
      - 有界重试 + 指数退避（仅针对网络/瞬时问题；限流不重试）
      - retry_call 强制单次尝试超时（基于线程）
      - 本地缓存（默认 6 小时），避免重复消耗免费额度
      - 失败时返回结构化的 __error__

    返回：
      成功时 {"symbol": symbol, "data": [...], "meta": {...}}
      失败时 {"symbol": symbol, "data": [], "__error__": {...}}
    """
    # 瞬时网络错误最多重试 2 次；Alpha Vantage 限流是每日配额，重试无意义，不重试
    cfg = retry_cfg or RetryConfig(max_retries=2, base_delay_sec=1.0, max_delay_sec=6.0, timeout_sec=25.0)

    logger.debug("正在获取 %s 的价格历史（%d 天）", symbol, days)

    cached = _load_cache(symbol, days)
    if cached is not None:
        records, meta = cached
        records = _normalize_order(records)
        meta = dict(meta)
        meta["cached"] = True
        logger.info("使用本地价格缓存：%s（%d 天）", symbol.upper(), days)
        return {"symbol": symbol, "data": records, "meta": meta}

    def _do():
        if is_hk_symbol(symbol):
            res = fetch_hk_daily(symbol, days=days)
            if not res.get("ok"):
                # 港股代码无效等确定性错误不重试
                raise RuntimeError(res.get("message", "港股价格获取失败"))
        else:
            res = fetch_daily_series(symbol, days=days)
        if res.get("ok"):
            return res
        if res.get("rate_limited"):
            # 每日配额用尽：重试无用，直接抛出由调用方处理
            raise RuntimeError(res.get("message", "Alpha Vantage 请求受限"))
        raise RetryableError(res.get("message", "未知错误"))

    try:
        result = retry_call(
            _do,
            cfg=cfg,
            op_name=f"price_history:{symbol}",
            logger=logger,
            retry_exceptions=(),
        )
    except Exception as e:
        logger.error("%s 的价格获取在重试后仍失败：%s", symbol, e)
        return {
            "symbol": symbol,
            "data": [],
            "__error__": {
                "where": "prices",
                "status": None,
                "message": str(e),
            },
            "note": "由于上游错误，价格数据不可用。",
            "meta": {"days": days},
        }

    records = result["data"]
    records = _normalize_order(records)
    logger.info("已获取 %s 的 %d 条记录", symbol, len(records))
    meta = {"days": days, "cached": False}
    _save_cache(symbol, days, records, meta)
    return {"symbol": symbol, "data": records, "meta": meta}
