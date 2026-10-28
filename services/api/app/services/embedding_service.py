"""记忆向量 provider：本地确定性哈希（默认）与 OpenAI-compatible embedding API。

设计文档第 9.2 章要求混合检索；设计第 20 章允许在 pgvector 不可用时降级。这里把"向量从哪来"
独立出来：

- **默认 hash**：零网络、可重放，测试与离线验收不依赖外部服务。
- **openai_compatible**：配置 base_url/key/model 后调用 ``/embeddings``；失败或维度不符时
  **回退到 hash** 并记录 warning，绝不让记忆写入因为外部服务不可用而失败。
- 返回维度必须等于 ``memory_embedding_dim``；否则视为配置错误并回退。
"""

from __future__ import annotations

import logging
import re
from hashlib import sha256

from app.config import get_settings
from app.models.memory import EMBEDDING_DIM

logger = logging.getLogger("app.memory.embedding")

_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def hash_embed(text: str, *, dim: int = EMBEDDING_DIM) -> list[float]:
    """确定性哈希向量：拉丁词 + 中文字 + 中文二元组，SHA-256 投影后 L2 归一化。"""
    lowered = (text or "").casefold()
    latin = _WORD_RE.findall(lowered)
    cjk = _CJK_RE.findall(lowered)
    tokens = latin + cjk + [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    vector = [0.0] * dim
    for token in tokens:
        digest = sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = sum(value * value for value in vector) ** 0.5
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def embed_text(text: str, *, dim: int = EMBEDDING_DIM) -> list[float]:
    """按配置选择 provider；任何外部失败都回退到 hash。"""
    settings = get_settings()
    if settings.memory_embedding_provider != "openai_compatible":
        return hash_embed(text, dim=dim)

    base_url = (settings.memory_embedding_base_url or "").rstrip("/")
    api_key = settings.memory_embedding_api_key
    if not base_url or not api_key:
        logger.warning("memory embedding provider 未配置 base_url/key，回退到 hash")
        return hash_embed(text, dim=dim)

    try:
        import httpx

        response = httpx.post(
            f"{base_url}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": settings.memory_embedding_model, "input": text},
            timeout=15,
        )
        response.raise_for_status()
        vector = response.json()["data"][0]["embedding"]
        if len(vector) != dim:
            raise ValueError(f"embedding 维度 {len(vector)} != {dim}")
        return [float(value) for value in vector]
    except Exception as exc:  # noqa: BLE001 —— 记忆写入不能因外部服务失败而中断
        logger.warning("embedding provider 失败，回退到 hash：%s", exc)
        return hash_embed(text, dim=dim)


__all__ = ["embed_text", "hash_embed"]
