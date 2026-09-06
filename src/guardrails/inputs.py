from __future__ import annotations
import os
import re
from dataclasses import dataclass

_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")  # 允许 BRK.B、RDS-A 等
_CN6_RE = re.compile(r"^\d{6}$")  # A 股 6 位代码
_HK_RE = re.compile(r"^\d{4,5}$")  # 港股：1810 / 0700 / 00700
_HK_SUFFIX_RE = re.compile(r"^(\d{4,5})\.HK$")  # 港股带后缀：1810.HK / 00700.HK / 9988.HK

# 天数范围的单一事实来源：API 校验、sanitize_days、CLI 默认值必须都引用这里
MIN_DAYS = 5
MAX_DAYS = 15
DEFAULT_DAYS = 10


@dataclass(frozen=True)
class ValidatedRequest:
    symbol: str
    days: int
    outdir: str


def normalize_symbol(symbol: str) -> str:
    """
    规范化股票代码：
    - A 股 6 位裸代码自动补交易所后缀（6/9 开头 → 沪市 .SHH；0/2/3 开头 → 深市 .SHZ）
    - 港股统一为 5 位补零裸代码（1810/0700/00700/1810.HK/9988.HK → 01810/00700/09988）
    - 已带后缀或非 A 股代码原样返回
    """
    s = str(symbol or "").strip().upper()
    hk_match = _HK_RE.match(s)
    if hk_match:
        return f"{int(hk_match.group(0)):05d}"
    hk_match = _HK_SUFFIX_RE.match(s)
    if hk_match:
        return f"{int(hk_match.group(1)):05d}"
    if not s or "." in s or ":" in s:
        return s
    if _CN6_RE.match(s):
        if s[0] in ("6", "9"):
            return f"{s}.SHH"
        if s[0] in ("0", "2", "3"):
            return f"{s}.SHZ"
    return s


def sanitize_symbol(symbol: str) -> str:
    if symbol is None:
        raise ValueError("缺少股票代码参数")
    s = str(symbol).strip().upper()
    s = s.replace("/", ".")  # 防御性规范化
    if not _TICKER_RE.match(s):
        raise ValueError("股票代码格式无效。允许：1-10 个字符 [A-Z0-9.-]（示例：AAPL、BRK.B）。")
    return normalize_symbol(s)


def sanitize_days(days: int, *, min_days: int = MIN_DAYS, max_days: int = MAX_DAYS) -> int:
    try:
        d = int(days)
    except Exception:
        raise ValueError("天数必须是整数")

    if d < min_days or d > max_days:
        raise ValueError(f"天数超出范围（{min_days}-{max_days}）")
    return d


def sanitize_outdir(outdir: str) -> str:
    if outdir is None:
        raise ValueError("缺少输出目录参数")
    base = os.path.abspath(str(outdir).strip())
    # 防止异常字符和意外的根目录写入
    if any(c in base for c in ["\0", "\n", "\r"]):
        raise ValueError("输出目录包含无效字符")
    return base


def validate_request(symbol: str, days: int, outdir: str) -> ValidatedRequest:
    return ValidatedRequest(
        symbol=sanitize_symbol(symbol),
        days=sanitize_days(days),
        outdir=sanitize_outdir(outdir),
    )
