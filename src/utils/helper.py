import math


def safe_num(val, reported_currency: str = "", decimals: int = 0):
    """
    使用千位分隔符格式化数值，并在前面加上报告货币
    （如果提供且有效）。对于缺失或无效的输入返回 'N/A'。

    decimals 控制小数位：金额/量类指标用默认 0，
    EPS、价格、比率等小数敏感指标请显式传 decimals=2。
    """
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return "N/A"
    if not math.isfinite(val):
        return "N/A"
    prefix = f" {reported_currency}" if reported_currency else ""
    return f"{val:,.{decimals}f}{prefix}"


def is_rate_limit_error(exc: Exception) -> bool:
    """
    判断异常消息文本是否为数据源限流错误（429、每日配额等）。
    仅用于异常消息文本，绝不能对成功响应体做子串匹配——
    正常行情数字中很容易出现 "429" 等子串（如 volume=10429300）。
    """
    msg = str(exc).lower()
    return any(k in msg for k in ("too many requests", "rate limit", "rate limited", "429", "25 requests"))
