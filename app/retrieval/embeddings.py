"""Disk-cached embeddings. Cache miss with no API key raises; never embeds zeros."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from array import array
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.llm.openai_provider import EMBED_DIM, EMBED_MODEL

_REPO = Path(__file__).resolve().parents[2]
CACHE_PATH = _REPO / "data" / "embeddings" / "cache.jsonl"
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


class MissingEmbeddingError(RuntimeError):
    """Cache miss and no OPENAI_API_KEY. Refusing to invent a vector."""


def cache_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def encode_vector(values: list[float]) -> str:
    return base64.b64encode(array("f", values).tobytes()).decode("ascii")


def decode_vector(blob: str) -> list[float]:
    return array("f", base64.b64decode(blob)).tolist()


def _has_api_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


class EmbeddingCache:
    def __init__(
        self,
        path: Path | str = CACHE_PATH,
        *,
        model: str = EMBED_MODEL,
        embed_fn: EmbedFn | None = None,
        batch_size: int = 64,
    ) -> None:
        self.path = Path(path)
        self.model = model
        self.embed_fn = embed_fn
        self.batch_size = batch_size
        self._mem: dict[str, list[float]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("model") != self.model:
                    continue
                vector = decode_vector(row["vector"])
                if len(vector) != EMBED_DIM:
                    continue
                self._mem[row["key"]] = vector

    def _append(self, key: str, vector: list[float]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {"key": key, "model": self.model, "vector": encode_vector(vector)}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        self._mem[key] = vector

    async def embed(self, texts: list[str]) -> list[list[float]]:
        keys = [cache_key(self.model, text) for text in texts]
        missing_i = [i for i, key in enumerate(keys) if key not in self._mem]
        if missing_i:
            new_texts = [texts[i] for i in missing_i]
            vectors = await self._fill(new_texts)
            for i, vector in zip(missing_i, vectors, strict=True):
                self._append(keys[i], vector)
        return [self._mem[key] for key in keys]

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]

    async def _fill(self, texts: list[str]) -> list[list[float]]:
        if self.embed_fn is None and not _has_api_key():
            raise MissingEmbeddingError(
                f"{len(texts)} cache miss(es) for {self.model} and OPENAI_API_KEY is unset"
            )
        embed_fn = self.embed_fn
        if embed_fn is None:
            from app.llm.openai_provider import embed as openai_embed

            async def embed_fn(batch: list[str]) -> list[list[float]]:
                return await openai_embed(batch, model=self.model)

        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            out.extend(await embed_fn(batch))
        if len(out) != len(texts) or any(len(vec) != EMBED_DIM for vec in out):
            raise MissingEmbeddingError(
                f"embed returned {len(out)} vectors for {len(texts)} texts "
                f"(dim {EMBED_DIM} required)"
            )
        return out
