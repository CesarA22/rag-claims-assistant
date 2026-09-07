"""Where the answering chunk actually lands, for the two cases that miss.

Finding 2 in EVALS.md used to say that `plainto_tsquery` ANDs every term and so
excludes the gs-005 chunk outright. That was written against a system that no
longer exists: `hybrid.py` has rewritten `&` to `|` since S3 (`OR_TSQUERY`), and
the finding's own proposed repair — "OR-with-ranking instead of AND" — had
already shipped. A generated document asserting a mechanism the code does not
have is worse than one asserting nothing, so this probe measures the mechanism
instead, and the finding now carries these numbers.

It prints, per case:

    AND / OR match counts   how many chunks each tsquery semantics admits
    top-5 as shipped        what `HybridRetriever.search` actually returns
    top-10 uncapped         the same fused+boosted list with MAX_PER_DOCUMENT off

The third is the one that separates the two cases. gs-005's answering chunk is
inside the uncapped top five and is removed by the per-document diversity cap;
au-003's is not in the uncapped top ten at all. They are two defects, not one.

Needs a corpus-indexed Postgres. No API key — the lexical arm embeds nothing:

    python -m scripts.chunk_rank_probe
"""

from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

from app.retrieval.hybrid import (
    OR_TSQUERY,
    SEARCH_SQL,
    HybridRetriever,
    _row_to_evidence,
    apply_role_boost,
)
from app.storage.db import create_pool

# The two failures reported by tier 2 and the boundary suite, with the chunk that
# carries the number each question asks for.
CASES = [
    (
        "gs-005",
        "Qual é o prazo de regulação para sinistros de roubo e furto de veículo?",
        "man-sin-2025#v2.0#tabela-1",
    ),
    (
        "au-003",
        "Qual é o prazo para comunicar um sinistro, conforme o normativo NI-014?",
        "ni-014#v2.0#3-prazo-de-comunica-o",
    ),
]

COUNT_AND = "SELECT count(*) FROM chunks WHERE tsv @@ plainto_tsquery('portuguese', $1)"
COUNT_OR = f"SELECT count(*) FROM chunks WHERE tsv @@ ({OR_TSQUERY})"


async def _fused(retriever: HybridRetriever, query: str):
    """The fused rows as the SQL returns them, before `apply_role_boost`."""
    async with retriever.pool.acquire() as conn:
        rows = await conn.fetch(
            SEARCH_SQL,
            query,
            False,  # include_superseded
            None,  # product
            False,  # exclude_pii
            True,  # use_lex
            retriever.candidates,
            None,  # vector
            False,  # use_vec
            float(retriever.rrf_k),
        )
    return [(float(row["rrf"]), _row_to_evidence(row)) for row in rows]


def _show(title: str, ranked, target: str) -> None:
    print(f"  {title}")
    for position, (_, evidence) in enumerate(ranked, 1):
        mark = "  <-- the chunk that answers" if evidence.id == target else ""
        print(f"    {position}. {evidence.id}{mark}")


async def main() -> int:
    load_dotenv()
    pool = await create_pool()
    retriever = HybridRetriever(pool, None, arm="lexical")
    try:
        total = await pool.fetchval("SELECT count(*) FROM chunks")
        print(f"chunks indexed: {total}\n")
        for label, question, target in CASES:
            and_n = await pool.fetchval(COUNT_AND, question)
            or_n = await pool.fetchval(COUNT_OR, question)
            print(f"===== {label} =====")
            print(f"  {question}")
            print(f"  chunks matched by AND (plainto_tsquery unmodified) : {and_n}")
            print(f"  chunks matched by OR  (what hybrid.py runs)        : {or_n}")
            _show(
                f"top-5 as shipped (max_per_document={retriever.max_per_document}):",
                await retriever.ranked(question, k=5),
                target,
            )
            _show(
                "top-10 with the per-document cap removed:",
                apply_role_boost(
                    await _fused(retriever, question), k=10, max_per_document=0
                ),
                target,
            )
            print()
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
