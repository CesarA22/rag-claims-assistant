"""The judge for the four `grade: judge` golden cases.

Contract, fixed before the run:

  SEES        the question, the answer text, the FULL TEXT of every cited chunk,
              and the case's `criterio_de_acerto`.
  NEVER SEES  `resposta_esperada`, `docs`, `must_contain`, `must_cite`, `traps`.

`criterio_de_acerto` is the rubric, not the answer key — it says what would make
an answer right without saying what the right answer is ("deve identificar a
ambiguidade ... ou pedir esclarecimento"). Withholding it would leave the judge
grading only groundedness, which is not what these four cases ask.

Full chunk text, not the 240-character snippet: the snippet length was chosen
for layout, and S6 found it capping a PII leak by accident. Judging groundedness
against a display truncation would measure the truncation. The snippet is what
the analyst sees; the chunk is what the answer was grounded in.

The judge gets its OWN provider. Its spend never enters a QuestionBudget, its
failures never trip the product's breaker, and "cost per question" therefore
means the product's cost and nothing else.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from app.llm.base import Message

# Same strict shape as DRAFT_SCHEMA, and for the same reason: `strict` is a
# property of the adapter, not of one caller's schema. A judge schema without
# `additionalProperties: false` is rejected with a 400 → InvalidRequest →
# judge_run.py exits 2 and the four `grade: judge` cases stay ungraded.
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "reason": {"type": "string"},
        "grounded": {"type": "boolean"},
    },
    "required": ["verdict", "reason", "grounded"],
    "additionalProperties": False,
}

SYSTEM = """You grade one answer produced by an insurance claims assistant.

You are given the analyst's question, the assistant's answer, the full text of
every source the answer cited, and the acceptance criterion for this question.

Judge two things:
  1. Does the answer satisfy the acceptance criterion?
  2. Is every substantive claim in the answer supported by the cited sources?

A refusal or a request for clarification IS a correct answer when the criterion
says so — read the criterion carefully before deciding. Do not reward an answer
for being confident, and do not penalise it for being short.

You do not have the expected answer and must not guess at one. Grade only
against the criterion and the cited sources.

Respond using the given JSON schema."""


def build_messages(
    question: str,
    answer: str,
    criterion: str,
    cited_chunks: list[dict[str, str]],
) -> list[Message]:
    lines = ["<cited_sources>"]
    if not cited_chunks:
        lines.append("(the answer cited no sources)")
    for chunk in cited_chunks:
        lines.append(
            f"### evidence_id={chunk['id']} document={chunk['document_code']} "
            f"section={chunk['section']} version={chunk['version']}"
        )
        lines.append(chunk["text"])
        lines.append("")
    lines.append("</cited_sources>")

    return [
        Message(role="system", content=SYSTEM),
        Message(role="user", content="\n".join(lines)),
        Message(
            role="user",
            content=(
                f"<question>\n{question}\n</question>\n\n"
                f"<answer>\n{answer}\n</answer>\n\n"
                f"<acceptance_criterion>\n{criterion}\n</acceptance_criterion>"
            ),
        ),
    ]


async def resolve_chunks(pool: asyncpg.Pool, evidence_ids: list[str]) -> list[dict[str, str]]:
    """Full chunk text for the cited ids, in the order they were cited."""
    if not evidence_ids:
        return []
    rows = await pool.fetch(
        "SELECT id, document_code, section, version, text FROM chunks WHERE id = ANY($1::text[])",
        evidence_ids,
    )
    by_id = {row["id"]: dict(row) for row in rows}
    return [by_id[eid] for eid in evidence_ids if eid in by_id]


async def judge_case(
    provider: Any,
    pool: asyncpg.Pool,
    case: dict[str, Any],
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Grade one judge case. Returns {verdict, reason, grounded, cited_resolved}."""
    evidence_ids = [c.get("evidence_id", "") for c in envelope.get("citations", [])]
    chunks = await resolve_chunks(pool, evidence_ids)

    messages = build_messages(
        question=case["pergunta"],
        answer=envelope.get("answer") or "",
        criterion=case["criterio_de_acerto"],
        cited_chunks=chunks,
    )
    completion = await provider.complete(messages, schema=VERDICT_SCHEMA)
    parsed = completion.parsed or {}
    return {
        "verdict": parsed.get("verdict", "fail"),
        "reason": parsed.get("reason", "judge returned no parseable verdict"),
        "grounded": bool(parsed.get("grounded", False)),
        "cited_resolved": len(chunks),
        "cited_requested": len(evidence_ids),
        "usage": {
            "prompt_tokens": completion.usage.prompt_tokens,
            "completion_tokens": completion.usage.completion_tokens,
        },
    }
