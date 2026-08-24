from __future__ import annotations
import re
from typing import Iterable

# 拦截金融内容中的高风险或违规表述
DEFAULT_FORBIDDEN = [
    "guaranteed returns",
    "cannot go down",
    "inside information",
    "sure shot",
    "risk free",
    "100% profit",
]

# 移除可能破坏 markdown/pdf 渲染的控制字符
_CTRL = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")

# 可选：压缩超长重复字符（提示注入产物）
_REPEAT = re.compile(r"(.)\1{40,}")


def sanitize_text(text: str) -> str:
    if text is None:
        return ""
    t = str(text)
    t = _CTRL.sub("", t)
    t = _REPEAT.sub(r"\1" * 10, t)
    return t.strip()


def contains_forbidden(text: str, forbidden: Iterable[str]) -> bool:
    lower = text.lower()
    return any(term.lower() in lower for term in forbidden)


def enforce_neutrality(text: str, forbidden: Iterable[str] = DEFAULT_FORBIDDEN) -> str:
    """
    最终确定性过滤器：清洗 + 拦截禁用表述。
    若发现禁用表述，则将其删改。
    """
    t = sanitize_text(text)
    for term in forbidden:
        # 以不区分大小写的方式删改该表述
        t = re.sub(re.escape(term), "[REDACTED]", t, flags=re.IGNORECASE)
    return t
