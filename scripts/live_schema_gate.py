"""F4's acceptance gate: N consecutive live calls, all conformant to DRAFT_SCHEMA.

The defect this gate exists for is probabilistic. With `strict: False` the live
model honoured the draft schema on roughly one call in three and otherwise
returned the JSON *Schema* itself — top-level keys `type`/`properties`/`required`
— which the pipeline surfaced to the analyst as a citation-validation refusal.
Three calls can pass that by luck; ten cannot, which is why the default is ten.

Nothing offline can stand in for this. `FakeProvider` discards the schema
argument entirely and `jsonschema` is not a dependency, so the whole suite
passes identically with `strict` set either way. The offline tests
(T-48/T-49/T-50) pin the flag and the schema shape; this measures the wire.

    python -m scripts.live_schema_gate [--calls 10] [--question "..."]

Costs real money — about US$0.0013 per call on gpt-5.4-mini. Requires
OPENAI_API_KEY (read from .env) and, for real retrieval, a corpus-indexed
Postgres; without one it falls back to a fixed five-chunk prompt and says so.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import date

from dotenv import load_dotenv

from app.domain.models import Evidence
from app.services.ask import DRAFT_SCHEMA, Draft, build_messages
from app.services.budget import Pricing

EXPECTED_KEYS = {"outcome", "answer", "citations"}
DEFAULT_QUESTION = "Qual é o prazo de vigência padrão de uma apólice de Seguro Auto?"


async def _evidence(question: str) -> tuple[list[Evidence], str]:
    """Five chunks from the indexed corpus, or a fixed fallback set."""
    try:
        from app.retrieval.hybrid import HybridRetriever
        from app.storage.db import create_pool

        pool = await create_pool()
        try:
            found = await HybridRetriever(pool, None, arm="lexical").search(question, k=5)
        finally:
            await pool.close()
        if found:
            return found, "postgres/lexical"
    except Exception as exc:  # noqa: BLE001 — any failure means "no index here"
        print(f"note: retrieval unavailable ({type(exc).__name__}), using the fallback set")

    from app.retrieval.memory import FIXED_CHUNKS

    padding = [
        Evidence(
            id=f"pad-{i}",
            document_code="CG-AUTO-2024",
            document_title="Condições Gerais do Seguro Auto",
            section=f"{i} Cláusula",
            version="3.2",
            effective_date=date(2024, 1, 1),
            product="Auto",
            text=(
                "As coberturas contratadas constam da apólice e observam os limites "
                "e franquias das tabelas anexas. " * 6
            ),
        )
        for i in range(3)
    ]
    return [*FIXED_CHUNKS, *padding], "fallback fixture"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, default=10)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    args = parser.parse_args()

    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set (checked the environment and .env).")
        return 2

    from app.llm.openai_provider import OpenAIProvider

    evidence, source = await _evidence(args.question)
    messages = build_messages([], evidence, args.question)
    provider = OpenAIProvider()
    pricing = Pricing.from_env()

    print(f"model={provider.model}  evidence={len(evidence)} chunks from {source}")
    # Deliberately not printed as "strict=True": the flag lives in the adapter,
    # and a banner that asserted it would have gone on saying so while a mutation
    # run measured 3/6 conformant. The conformance count is the claim.
    print(f"calls={args.calls}  schema=DRAFT_SCHEMA")
    print()

    conformant = 0
    latencies: list[float] = []
    total_usd = 0.0
    for n in range(1, args.calls + 1):
        started = time.perf_counter()
        completion = await provider.complete(messages, schema=DRAFT_SCHEMA)
        elapsed = time.perf_counter() - started
        latencies.append(elapsed)

        usage = completion.usage
        cost = pricing.cost_usd(usage)
        total_usd += cost

        keys = set(completion.parsed or {})
        ok = keys == EXPECTED_KEYS
        if ok:
            try:
                Draft.model_validate(completion.parsed)
            except Exception as exc:  # noqa: BLE001
                ok = False
                keys = {f"<{type(exc).__name__}>"}
        conformant += ok
        print(
            f"{n:3d}  {'OK ' if ok else 'BAD'}  {elapsed:5.2f}s  "
            f"US${cost:.6f}  in={usage.prompt_tokens} "
            f"cached={usage.cached_prompt_tokens} out={usage.completion_tokens}  "
            f"keys={sorted(keys)}"
        )

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1))))]
    print(
        f"\n{conformant}/{args.calls} conformant   "
        f"p50={p50:.2f}s p95={p95:.2f}s   "
        f"mean=US${total_usd / args.calls:.6f} total=US${total_usd:.6f}"
    )
    if conformant != args.calls:
        print("GATE FAILED — structured output is not deterministic.")
        return 1
    print("GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
