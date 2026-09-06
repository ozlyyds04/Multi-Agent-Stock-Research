"""
文本嵌入器：把记忆/查询文本转成向量，用于长期记忆的相似度检索。

- OpenAIEmbedder：配置了 OPENAI_API_KEY 时使用（text-embedding-3-small，1536 维）。
- HashEmbedder：离线确定性嵌入（测试/无 key 回退），保证本地零网络、零依赖。

维度必须与 pgvector 建表时的维度一致，因此生产使用固定一个嵌入器。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from typing import List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


class BaseEmbedder:
    @property
    def dim(self) -> int:
        raise NotImplementedError

    def embed(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]


class HashEmbedder(BaseEmbedder):
    """确定性离线嵌入：对词/字做加盐哈希累加到固定维度，并 L2 归一化。"""

    def __init__(self, dim: int = 256):
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def _tokens(self, text: str) -> List[str]:
        return re.findall(r"[\w\u4e00-\u9fff]+", (text or "").lower())

    def embed(self, texts: List[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for text in texts:
            vec = [0.0] * self._dim
            for token in self._tokens(text):
                h = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:8], 16)
                vec[h % self._dim] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


class OpenAIEmbedder(BaseEmbedder):
    def __init__(self, model: str = "text-embedding-3-small"):
        from langchain_openai import OpenAIEmbeddings

        self._emb = OpenAIEmbeddings(model=model)
        self._dim: Optional[int] = None

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_one("dim"))
        return self._dim

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [list(map(float, v)) for v in self._emb.embed_documents(texts)]


_embedder: Optional[BaseEmbedder] = None


def get_embedder() -> BaseEmbedder:
    global _embedder
    if _embedder is None:
        if os.getenv("OPENAI_API_KEY"):
            try:
                _embedder = OpenAIEmbedder()
                logger.info("使用 OpenAI 文本嵌入（dim=%s）。", _embedder.dim)
            except Exception as e:
                logger.warning("OpenAI 嵌入初始化失败，回退哈希嵌入：%s", e)
                _embedder = HashEmbedder()
        else:
            _embedder = HashEmbedder()
            logger.info("未配置 OPENAI_API_KEY，使用离线哈希嵌入。")
    return _embedder


def reset_embedder() -> None:
    global _embedder
    _embedder = None
