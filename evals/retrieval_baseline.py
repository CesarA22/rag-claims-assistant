"""
Tier-0 retrieval evaluation. No LLM, no cost, no variance.

Runs against HybridRetriever (Postgres). The original BM25 baseline remains
runnable at handoff/evals/retrieval_baseline.py.

    python evals/retrieval_baseline.py --k 5 --arm lexical --gate 1.0
    python evals/retrieval_baseline.py --k 5 --arm all
    python evals/retrieval_baseline.py --k 5 --arm all --probes
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.llm.openai_provider import EMBED_DIM, EMBED_MODEL
from app.retrieval.embeddings import EmbeddingCache
from app.retrieval.hybrid import Arm, HybridRetriever
from app.storage.db import connect

GOLDEN = os.environ.get("GOLDEN_SET", "evals/golden_set_enriched.json")
ARMS: tuple[Arm, ...] = ("lexical", "vector", "hybrid")

PROBES = [
    ("version trap",  "Em quantos dias o segurado precisa avisar a seguradora sobre um sinistro?"),
    ("paraphrase",    "Se meu carro for levado por bandidos, quanto tempo a seguradora tem para resolver?"),
    ("paraphrase",    "Meu computador queimou por causa de uma tempestade com relâmpagos. Pago franquia?"),
    ("colloquial",    "quanto a seguradora banca se eu bater no carro de outra pessoa"),
    ("unseen-likely", "Qual o teto de indenização para incêndio em imóvel comercial?"),
]


def _pad(vec: list[float]) -> list[float]:
    values = [float(x) for x in vec]
    if len(values) < EMBED_DIM:
        values.extend([0.0] * (EMBED_DIM - len(values)))
    return values[:EMBED_DIM]


def _embed_query():
    """OpenAI disk cache by default. EMBED_QUERY=e5 uses local multilingual vectors."""
    if os.getenv("EMBED_QUERY") == "e5":
        from fastembed import TextEmbedding

        encoder = TextEmbedding(
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )

        async def embed_one(text: str) -> list[float]:
            return _pad(list(next(encoder.embed([text]))))

        return embed_one
    cache = EmbeddingCache(model=os.getenv("EMBED_MODEL", EMBED_MODEL))
    return cache.embed_one


def _arms(name: str) -> tuple[Arm, ...]:
    if name == "all":
        return ARMS
    return (name,)  # type: ignore[return-value]


async def _run_probes(retriever: HybridRetriever) -> None:
    for label, query in PROBES:
        print(f"[{label}] {query}")
        ranked = await retriever.ranked(query, k=4)
        for score, chunk in ranked:
            heading = chunk.section[:56]
            print(
                f"   {score:6.4f}  {chunk.document_code} v{chunk.version} "
                f"({chunk.effective_date})  {heading}"
            )
        print()


async def _run_golden(retriever: HybridRetriever, k: int) -> float:
    cases = json.loads(Path(GOLDEN).read_text(encoding="utf-8"))
    hits = scored = 0
    for case in cases:
        expected = case.get("docs", [])
        got = [c.document_code for c in await retriever.search(case["pergunta"], k=k)]
        if not expected:
            print(f'{case["id"]}  n/a   (refusal case)  top{k}={got}')
            continue
        scored += 1
        ok = all(e in got for e in expected)
        hits += ok
        print(f'{case["id"]}  {"OK  " if ok else "MISS"}  expected={expected}  top{k}={got}')
    recall = hits / scored if scored else 0.0
    print(f"\nrecall@{k} = {hits}/{scored} = {recall:.0%}")
    return recall


async def async_main(args: argparse.Namespace) -> int:
    load_dotenv()
    embed_query = None if args.arm == "lexical" else _embed_query()
    gate_recall: float | None = None
    async with connect() as pool:
        n_chunks, n_docs = await HybridRetriever(pool).corpus_stats()
        print(f"{n_chunks} chunks from {n_docs} documents\n")
        for arm in _arms(args.arm):
            if args.arm == "all":
                print(f"== arm: {arm} ==\n")
            query_fn = None if arm == "lexical" else embed_query
            retriever = HybridRetriever(pool, query_fn, arm=arm)
            if args.probes:
                await _run_probes(retriever)
            else:
                recall = await _run_golden(retriever, args.k)
                if arm == "hybrid" or args.arm != "all":
                    gate_recall = recall
            if args.arm == "all":
                print()
    if args.gate is not None and gate_recall is not None and gate_recall < args.gate:
        print(f"FAIL: below gate {args.gate:.0%}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--gate", type=float, default=None, help="fail if recall below this")
    parser.add_argument("--probes", action="store_true", help="run unseen-style probes instead")
    parser.add_argument(
        "--arm",
        choices=("lexical", "vector", "hybrid", "all"),
        default="hybrid",
    )
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
