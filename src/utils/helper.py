def safe_num(val, reported_currency: str = ""):
    """
    使用千位分隔符格式化数值，并在前面加上报告货币
    （如果提供且有效）。对于缺失或无效的输入返回 'N/A'。
    """
    if isinstance(val, (int, float)):
        prefix = f" {reported_currency}" if reported_currency else ""
        return f"{val:,.0f}{prefix}"
    return "N/A"


def is_rate_limit_error(exc: Exception) -> bool:
    """
    判断异常/错误信息是否为数据源限流错误（429、每日配额等）。
    用于预检降级：仅在明确限流时跳过预检继续执行。
    """
    msg = str(exc).lower()
    return any(k in msg for k in ("too many requests", "rate limit", "rate limited", "429", "25 requests"))

