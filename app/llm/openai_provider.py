"""OpenAI SDK lives here and nowhere else.

Chat completion lands in S5. This module currently exposes embeddings only.
"""

from __future__ import annotations

from openai import AsyncOpenAI

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536


async def embed(texts: list[str], *, model: str = EMBED_MODEL) -> list[list[float]]:
    if not texts:
        return []
    client = AsyncOpenAI()
    response = await client.embeddings.create(model=model, input=texts)
    by_index = {item.index: item.embedding for item in response.data}
    return [by_index[i] for i in range(len(texts))]
