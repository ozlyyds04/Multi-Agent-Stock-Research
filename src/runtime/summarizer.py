"""
记忆压缩的 LLM 摘要器：把一批旧记忆条目压缩成一条精炼要点。

- 使用当前配置的 LLM（provider/model 来自 settings.yaml，key 来自 .env）。
- 无可用 key 或调用失败时返回空串，由上层回退到“按重要度裁剪”。
- 构建是惰性 + 单例，构造不联网（仅设置 key），只有真正调用才发请求。
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

_PROMPT = "请把下面多条股票研究记忆压缩成一条精炼要点，" "保留关键数字与结论，120 字以内：\n\n{text}"

_summarizer: Optional[Callable[[str], str]] = None
_built = False


def _llm_cfg():
    import yaml

    path = os.path.join(os.path.dirname(__file__), "..", "config", "settings.yaml")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    llm = cfg.get("llm", {})
    model = os.getenv("LLM_MODEL") or llm.get("model", "deepseek-v4-flash")
    return llm.get("provider", "deepseek"), model


def _build_llm(provider: str, model: str):
    temperature = 1.0 if ("gpt-5" in model or "gpt-4o" in model) else 0.2
    if provider == "deepseek" and os.getenv("DEEPSEEK_API_KEY"):
        from langchain_deepseek import ChatDeepSeek

        kw = {"model": model, "temperature": temperature}
        if os.getenv("DEEPSEEK_BASE_URL"):
            kw["base_url"] = os.getenv("DEEPSEEK_BASE_URL")
        return ChatDeepSeek(**kw)
    if provider == "openai" and os.getenv("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI

        kw = {"model": model, "temperature": temperature}
        if os.getenv("OPENAI_BASE_URL"):
            kw["base_url"] = os.getenv("OPENAI_BASE_URL")
        return ChatOpenAI(**kw)
    return None


def get_summarizer() -> Optional[Callable[[str], str]]:
    """返回一个 summarize(text)->str 的可调用对象；无可用 LLM 时返回 None。"""
    global _summarizer, _built
    if _built:
        return _summarizer
    _built = True
    try:
        provider, model = _llm_cfg()
        llm = _build_llm(provider, model)
        if llm is None:
            logger.info("未配置可用 LLM，记忆压缩回退到按重要度裁剪。")
            _summarizer = None
            return None

        def summarize(text: str) -> str:
            resp = llm.invoke(_PROMPT.format(text=text[:8000]))
            return getattr(resp, "content", "") or ""

        _summarizer = summarize
        return summarize
    except Exception as e:
        logger.warning("记忆摘要器初始化失败，回退到按重要度裁剪：%s", e)
        _summarizer = None
        return None


def reset_summarizer() -> None:
    global _summarizer, _built
    _summarizer = None
    _built = False
