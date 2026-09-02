from datetime import date
from pathlib import Path

import pytest

from app.domain.models import Evidence
from app.retrieval.embeddings import (
    EmbeddingCache,
    MissingEmbeddingError,
    decode_vector,
    encode_vector,
)
from app.retrieval.hybrid import (
    OR_TSQUERY,
    RRF_K,
    SEARCH_SQL,
    apply_role_boost,
    rewrite_and_to_or,
    rrf_score,
)

VERSION_TRAP = (
    "Em quantos dias o segurado precisa avisar a seguradora sobre um sinistro?"
)


def _chunk(
    *,
    id: str = "x",
    code: str = "CG-AUTO-2024",
    role: str = "normative",
    version: str = "1.0",
) -> Evidence:
    return Evidence(
        id=id,
        document_code=code,
        document_title=code,
        section="1",
        version=version,
        effective_date=date(2024, 1, 1),
        product="Auto",
        text="...",
        doc_role=role,  # type: ignore[arg-type]
    )


def test_or_tsquery_rewrites_and_to_or() -> None:
    """T-19 / R-09: lexical arm ORs lexemes; AND would zero most golden questions."""
    assert rewrite_and_to_or("'aviso' & 'sinistro'") == "'aviso' | 'sinistro'"
    assert "&', '|')" in OR_TSQUERY
    assert OR_TSQUERY in SEARCH_SQL
    assert "websearch_to_tsquery" not in SEARCH_SQL


def test_rrf_k_discriminates_at_this_scale() -> None:
    """T-19 / R-09: rrf_k=15, not 60, so ranks 1 and 50 still differ usefully."""
    ratio_15 = rrf_score(1, 15) / rrf_score(50, 15)
    ratio_60 = rrf_score(1, 60) / rrf_score(50, 60)
    assert RRF_K == 15
    assert ratio_15 > 3
    assert ratio_60 < 2


def test_role_boost_prefers_normative_over_glossary() -> None:
    """T-19 / R-09: doc_role boost is a Python multiplier, testable without Postgres."""
    tied = [
        (1.0, _chunk(id="g", role="glossary")),
        (1.0, _chunk(id="n", role="normative")),
        (1.0, _chunk(id="p", role="pointer")),
    ]
    order = [item.id for _, item in apply_role_boost(tied, k=3)]
    assert order == ["n", "p", "g"]


def test_vector_codec_roundtrip() -> None:
    """T-19 / R-09: cache stores vectors as base64 array('f'), no numpy."""
    original = [0.0, -1.5, 2.25, 1e-6]
    assert decode_vector(encode_vector(original)) == pytest.approx(original)


async def test_cache_hit_skips_embed_fn(tmp_path: Path) -> None:
    """T-19 / R-09: a cached vector is returned without calling the provider."""
    path = tmp_path / "cache.jsonl"
    calls: list[list[str]] = []

    async def embed_fn(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[0.1] * 1536 for _ in texts]

    cache = EmbeddingCache(path, embed_fn=embed_fn)
    first = await cache.embed(["hello"])
    second = await cache.embed(["hello"])
    assert first[0] == pytest.approx(second[0])
    assert len(calls) == 1


async def test_cache_miss_without_key_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-19 / R-09: cache miss with no API key raises; never embeds zeros."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cache = EmbeddingCache(tmp_path / "cache.jsonl")
    with pytest.raises(MissingEmbeddingError):
        await cache.embed(["uncached text"])


@pytest.mark.db
async def test_ni014_v1_excluded_unless_historical() -> None:
    """T-19 / R-09: include_superseded=False returns zero NI-014 v1.0 chunks."""
    from dotenv import load_dotenv

    from app.retrieval.hybrid import HybridRetriever
    from app.storage.db import connect

    load_dotenv()
    async with connect() as pool:
        retriever = HybridRetriever(pool, arm="lexical")
        current = await retriever.search(VERSION_TRAP, k=50, include_superseded=False)
        assert not any(
            item.document_code == "NI-014" and item.version == "1.0" for item in current
        )
        assert any(
            item.document_code == "NI-014" and item.version == "2.0" for item in current
        )
        historical = await retriever.search(VERSION_TRAP, k=50, include_superseded=True)
        assert any(
            item.document_code == "NI-014" and item.version == "1.0" for item in historical
        )
