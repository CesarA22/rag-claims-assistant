"""Measure the sufficiency/ambiguity gate ordering against LIVE drafts.

The question F2 has to answer — swap the two gates, or discriminate on two sets —
cannot be decided offline. `FakeProvider` emits one canned sentence whose content
terms are `{vigencia, padrao, meses}`, which scores 0.000 against the glass and
premium evidence, so under *any* sufficiency-first ordering the fake-driven
harness refuses gs-009 and au-004 alike. That is a property of the double, not
evidence about the shipped system.

So this asks the real model for a real draft over the real 220-chunk index and
prints, per case:

    ratio(cited)      the answer's content terms found in the chunks it cited
    ratio(retrieved)  the same terms found anywhere in the retrieved set
    what each candidate ordering would ship

Run it against a corpus-indexed Postgres with a working key:

    python -m scripts.gate_order_probe
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

from app.services import grounding
from app.services.ask import DRAFT_SCHEMA, Draft, _cited_evidence, build_messages, validate_citations

# The three cases the F2 decision turns on, with the shape each is meant to have.
CASES = [
    ("gs-008", "Existe desconto para pagamento do prêmio à vista? De quanto?",
     "absent fact over multi-product evidence — must REFUSE"),
    ("gs-009", "Qual é o limite da cobertura de vidros?",
     "real fact, two products, question names none — may ASK"),
    ("au-004", "Qual é a franquia da cobertura de vidros na minha apólice?",
     "real fact for one product, question names none — must ASK, not refuse"),
]


async def main() -> int:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set (checked the environment and .env).")
        return 2

    from app.llm.openai_provider import OpenAIProvider
    from app.retrieval.hybrid import HybridRetriever
    from app.storage.db import create_pool

    provider = OpenAIProvider()
    pool = await create_pool()
    try:
        retriever = HybridRetriever(pool, None, arm="lexical")
        for case_id, question, shape in CASES:
            evidence = await retriever.search(question, k=5)
            completion = await provider.complete(
                build_messages([], evidence, question), schema=DRAFT_SCHEMA
            )
            draft = Draft.model_validate(completion.parsed)

            products = sorted(grounding.spanned_products(evidence))
            ambiguous = grounding.is_ambiguous(question, evidence)
            answer = validate_citations(draft, evidence)
            cited = _cited_evidence(answer, evidence)
            text = draft.answer

            r_cited = grounding.grounded_ratio(text, cited)
            r_corpus = grounding.grounded_ratio(text, evidence)
            unsupported_by_cited = r_cited < grounding.SUFFICIENCY_MIN
            unsupported_by_corpus = r_corpus < grounding.SUFFICIENCY_MIN

            print(f"\n=== {case_id} — {shape}")
            print(f"  question    : {question}")
            print(f"  draft says  : {draft.outcome}")
            print(f"  draft text  : {text[:160]}")
            print(f"  cited ids   : {[c.evidence_id for c in draft.citations]}")
            print(f"  products    : {products}  is_ambiguous={ambiguous}")
            print(f"  ratio(cited)     = {r_cited:.3f}")
            print(f"  ratio(retrieved) = {r_corpus:.3f}   (SUFFICIENCY_MIN={grounding.SUFFICIENCY_MIN})")

            if draft.outcome in ("refused", "needs_clarification"):
                verdict = f"passthrough -> {draft.outcome} (both gates bypassed)"
                print(f"  today       : {verdict}")
                print(f"  bare reorder: {verdict}")
                print(f"  discriminator: {verdict}")
                continue

            today = "needs_clarification" if ambiguous else (
                "refused (unsupported)" if unsupported_by_cited else "answered"
            )
            reorder = (
                "refused (unsupported)" if unsupported_by_cited
                else ("needs_clarification" if ambiguous else "answered")
            )
            if unsupported_by_corpus:
                discriminator = "refused (unsupported by the whole retrieved set)"
            elif ambiguous:
                discriminator = "needs_clarification"
            elif unsupported_by_cited:
                discriminator = "refused (unsupported)"
            else:
                discriminator = "answered"
            print(f"  today        : {today}")
            print(f"  bare reorder : {reorder}")
            print(f"  discriminator: {discriminator}")
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
