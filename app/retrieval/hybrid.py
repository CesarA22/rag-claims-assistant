"""Hybrid retriever: Portuguese tsvector + pgvector cosine, fused with RRF."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Literal

import asyncpg

from app.domain.models import Evidence

Arm = Literal["lexical", "vector", "hybrid"]
EmbedQuery = Callable[[str], Awaitable[list[float]]]

ROLE_WEIGHTS: dict[str, float] = {
    "normative": 1.00,
    "minutes": 0.90,
    "pointer": 0.80,
    "glossary": 0.70,
}

RRF_K = 15
CANDIDATES = 50
TS_RANK_NORMALIZATION = 32

# plainto_tsquery ANDs every lexeme; BM25 sums partial matches. OR the lexemes
# and let ts_rank_cd rank. websearch_to_tsquery also ANDs bare terms — don't.
OR_TSQUERY = "replace(plainto_tsquery('portuguese', $1)::text, '&', '|')::tsquery"

SEARCH_SQL = f"""
WITH q AS (
  SELECT {OR_TSQUERY} AS tsq
),
filtered AS (
  SELECT *
  FROM chunks
  WHERE ($2::boolean OR NOT superseded)
    AND ($3::text IS NULL OR product = $3 OR product = 'All')
    AND (NOT $4::boolean OR NOT contains_pii)
),
lex AS (
  SELECT c.id,
         row_number() OVER (
           ORDER BY ts_rank_cd(c.tsv, q.tsq, {TS_RANK_NORMALIZATION}) DESC, c.id
         ) AS rank
  FROM filtered c, q
  WHERE $5::boolean
    AND q.tsq::text <> ''
    AND c.tsv @@ q.tsq
  ORDER BY rank
  LIMIT $6
),
vec AS (
  SELECT c.id,
         row_number() OVER (ORDER BY c.embedding <=> $7::vector, c.id) AS rank
  FROM filtered c
  WHERE $8::boolean
    AND c.embedding IS NOT NULL
    AND $7::vector IS NOT NULL
  ORDER BY rank
  LIMIT $6
),
fused AS (
  SELECT COALESCE(lex.id, vec.id) AS id,
         COALESCE(1.0 / ($9::double precision + lex.rank), 0)
       + COALESCE(1.0 / ($9::double precision + vec.rank), 0) AS rrf
  FROM lex
  FULL OUTER JOIN vec ON lex.id = vec.id
)
SELECT
  c.id, c.document_code, c.document_title, c.section, c.version,
  c.effective_date, c.product, c.text, c.superseded, c.doc_role,
  c.contains_pii, f.rrf
FROM fused f
JOIN chunks c ON c.id = f.id
ORDER BY f.rrf DESC, c.id
LIMIT $6
"""


def rewrite_and_to_or(tsquery_text: str) -> str:
    """Mirror of the SQL replace: AND lexemes become OR lexemes."""
    return tsquery_text.replace("&", "|")


def rrf_score(rank: int | None, k: int = RRF_K) -> float:
    if rank is None:
        return 0.0
    return 1.0 / (k + rank)


def apply_role_boost(
    ranked: list[tuple[float, Evidence]],
    *,
    weights: dict[str, float] | None = None,
    k: int = 5,
) -> list[tuple[float, Evidence]]:
    table = ROLE_WEIGHTS if weights is None else weights
    boosted = [
        (score * table.get(item.doc_role, 1.0), item) for score, item in ranked
    ]
    boosted.sort(key=lambda pair: (-pair[0], pair[1].id))
    return boosted[:k]


def _row_to_evidence(row: asyncpg.Record) -> Evidence:
    return Evidence(
        id=row["id"],
        document_code=row["document_code"],
        document_title=row["document_title"],
        section=row["section"],
        version=row["version"],
        effective_date=row["effective_date"]
        if isinstance(row["effective_date"], date)
        else date.fromisoformat(str(row["effective_date"])),
        product=row["product"],
        text=row["text"],
        superseded=row["superseded"],
        doc_role=row["doc_role"],
        contains_pii=row["contains_pii"],
    )


class HybridRetriever:
    def __init__(
        self,
        pool: asyncpg.Pool,
        embed_query: EmbedQuery | None = None,
        *,
        arm: Arm = "hybrid",
        rrf_k: int = RRF_K,
        candidates: int = CANDIDATES,
        role_weights: dict[str, float] | None = None,
    ) -> None:
        self.pool = pool
        self.embed_query = embed_query
        self.arm = arm
        self.rrf_k = rrf_k
        self.candidates = candidates
        self.role_weights = ROLE_WEIGHTS if role_weights is None else role_weights

    async def search(
        self,
        query: str,
        *,
        product: str | None = None,
        k: int = 5,
        include_superseded: bool = False,
        exclude_pii: bool = False,
    ) -> list[Evidence]:
        ranked = await self.ranked(
            query,
            product=product,
            k=k,
            include_superseded=include_superseded,
            exclude_pii=exclude_pii,
        )
        return [item for _, item in ranked]

    async def ranked(
        self,
        query: str,
        *,
        product: str | None = None,
        k: int = 5,
        include_superseded: bool = False,
        exclude_pii: bool = False,
    ) -> list[tuple[float, Evidence]]:
        use_lex = self.arm in ("lexical", "hybrid")
        use_vec = self.arm in ("vector", "hybrid")
        vector: list[float] | None = None
        if use_vec:
            if self.embed_query is None:
                raise RuntimeError("vector arm requires embed_query")
            vector = await self.embed_query(query)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                SEARCH_SQL,
                query,
                include_superseded,
                product,
                exclude_pii,
                use_lex,
                self.candidates,
                vector,
                use_vec,
                float(self.rrf_k),
            )
        ranked = [(float(row["rrf"]), _row_to_evidence(row)) for row in rows]
        return apply_role_boost(ranked, weights=self.role_weights, k=k)

    async def corpus_stats(self) -> tuple[int, int]:
        async with self.pool.acquire() as conn:
            n_chunks = await conn.fetchval("SELECT count(*) FROM chunks")
            n_docs = await conn.fetchval(
                "SELECT count(DISTINCT (document_code, version)) FROM chunks"
            )
        return int(n_chunks), int(n_docs)
