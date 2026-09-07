"""T-53 / R-10: the claims tool is reachable from the product, and cited as a query.

The tool has been built and tested since S4 (T-20…T-26) and unreachable from the
pipeline ever since — `app/services/ask.py` carried no reference to `app.tools`,
so the brief's "document and section, **or database query**" had no second half
and gs-007 failed all three of its assertions. These tests cover the wiring: what
the router decides, what the evidence looks like, that the answer cites the query
by name, and that a broken database costs the database half of an answer rather
than the answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.llm.fake import FakeProvider
from app.retrieval.memory import InMemoryRetriever
from app.services import claims_router
from app.services.ask import _format_evidence, ask, claims_evidence
from app.storage.memory import InMemoryConversationRepository
from app.tools.claims import ClaimsTool

GS007 = (
    "No sinistro SIN-2025-004512, o valor pago respeitou o limite da cobertura "
    "de Danos Materiais a Terceiros do Seguro Auto?"
)
CLAIMS_DB = Path(__file__).resolve().parents[1] / "data" / "claims.db"

needs_db = pytest.mark.skipif(not CLAIMS_DB.exists(), reason="data/claims.db is not present")


async def _ask(llm, content: str, claims=None):
    return await ask(
        conversation_id="c-1",
        content=content,
        client_message_id="cm-1",
        llm=llm,
        retriever=InMemoryRetriever(),
        repo=InMemoryConversationRepository(),
        trace_id="t-1",
        provider_name="fake",
        claims=claims,
    )


def test_a_claim_number_routes_both_the_claim_and_its_payment():
    """T-53 / R-10: one identifier, two queries — measured, not tidy.

    Routing `get_claim_payment` alone leaves gs-007's grounded ratio below
    SUFFICIENCY_MIN, so the turn refuses while its must_cite set is satisfied —
    a failure that looks like a success. The two rows are also the pair the
    question is about: claim_amount is CLAIMED and paid_amount is PAID.
    """
    assert claims_router.route(GS007) == [
        ("get_claim", {"claim_number": "SIN-2025-004512"}),
        ("get_claim_payment", {"claim_number": "SIN-2025-004512"}),
    ]


def test_the_router_is_case_insensitive_and_deduplicates():
    """T-53 / R-10: an analyst types what they type, and twice is still one query."""
    plans = claims_router.route("sin-2025-004512 aparece duas vezes: SIN-2025-004512")

    assert [q for q, _ in plans] == ["get_claim", "get_claim_payment"]
    assert plans[0][1]["claim_number"] == "SIN-2025-004512"


def test_a_policy_question_about_terms_does_not_pull_twenty_claim_rows():
    """T-53 / R-10: `list_claims_by_policy` is added on claim intent, not on any policy id."""
    terms = claims_router.route("Qual o produto da apólice AP-RES-000045?")
    incidents = claims_router.route("Quais sinistros estão na apólice AP-AUTO-000123?")

    assert [q for q, _ in terms] == ["get_policy"]
    assert [q for q, _ in incidents] == ["get_policy", "list_claims_by_policy"]


def test_a_corpus_question_routes_nothing():
    """T-53 / R-10: the router adds to retrieval, it does not replace it."""
    assert claims_router.route("Qual é o prazo de vigência padrão de uma apólice?") == []


@needs_db
async def test_a_routed_query_becomes_evidence_citable_as_a_query():
    """T-53 / R-01: the citation names the query, which is the brief's second half."""
    evidence = await claims_evidence(GS007, ClaimsTool())

    assert [item.section for item in evidence] == [
        'claims.get_claim(claim_number="SIN-2025-004512")',
        'claims.get_claim_payment(claim_number="SIN-2025-004512")',
    ]
    assert all(item.source_kind == "claims" for item in evidence)
    assert all(item.doc_role == "database" for item in evidence)
    assert all(item.document_code == "claims.db" for item in evidence)
    # The freshness of the queried data — a snapshot date, not today's date.
    assert all(item.effective_date is not None for item in evidence)
    assert "R$ 100.000,00" in evidence[1].text


@needs_db
async def test_the_answer_cites_the_database_query_end_to_end():
    """T-53 / R-01 / gs-007: `claims.get_claim_payment` reaches the envelope.

    gs-007's must_cite is ['CG-AUTO-2024', 'claims.get_claim_payment'] and the
    second half was unsatisfiable while the tool had no caller. The draft is
    scripted rather than live so this runs without a key; what it pins is that a
    claims evidence_id resolves through validate_citations and renders as a
    citation whose section is the query call string.
    """
    tool = ClaimsTool()
    evidence = await claims_evidence(GS007, tool)

    # A database-only answer, in the rows' own vocabulary. The word "limite" is
    # deliberately absent: a coverage limit is a corpus fact, and the in-memory
    # retriever this test uses holds two chunks about vigência. Live, the corpus
    # half supplies it — that is the point of routing tool evidence alongside
    # retrieval rather than instead of it.
    llm = FakeProvider()
    llm.enqueue(
        FakeProvider.answered(
            "O pagamento foi integral e está concluido: valor pago de "
            "R$ 100.000,00 sobre o valor reivindicado pelo segurado.",
            [item.id for item in evidence],
        )
    )
    result = await _ask(llm, GS007, claims=tool)

    assert result.outcome == "answered"
    assert [c.section for c in result.citations] == [
        'claims.get_claim(claim_number="SIN-2025-004512")',
        'claims.get_claim_payment(claim_number="SIN-2025-004512")',
    ]
    assert all(c.document_code == "claims.db" for c in result.citations)
    assert all(c.version == "snapshot" for c in result.citations)
    assert "R$ 100.000,00" in (result.answer or "")


@needs_db
async def test_a_forged_claims_id_still_refuses_the_whole_answer():
    """T-53 / R-02: citation validation stays all-or-nothing over tool evidence.

    A claims evidence_id is `claims:<query>:<8 hex>` and the model transcribes it
    like any other. Dropping just the bad citation and shipping the rest would be
    kinder to a typo and would also let a partly forged answer through, which is
    the property T-03 exists to prevent. The stricter rule is kept deliberately,
    and pinned here so the choice is visible rather than incidental.
    """
    llm = FakeProvider()
    llm.enqueue(FakeProvider.answered("Foi pago R$ 100.000,00.", ["claims:get_claim_payment:deadbeef"]))
    result = await _ask(llm, GS007, claims=ClaimsTool())

    assert result.outcome == "refused"
    assert result.citations == []
    assert "100.000" not in (result.answer or "")


async def test_a_broken_claims_database_degrades_to_corpus_only():
    """T-53 / R-05: a tool failure costs the database half of an answer, not the answer.

    `ask()`'s `except InsurCoError` fails the whole turn, so an unreadable
    database would have taken every question with a claim number down with it.
    The corpus is the source of record for every rule; the database only adds
    facts, and losing it is a degradation rather than an outage.
    """
    broken = ClaimsTool(path="does/not/exist.db")

    assert await claims_evidence(GS007, broken) == []

    llm = FakeProvider()
    result = await _ask(llm, GS007, claims=broken)

    assert result.outcome in ("answered", "refused")   # a real turn, not a 5xx
    assert result.error_code is None


async def test_no_tool_configured_is_the_same_as_no_routed_queries():
    """T-53 / R-10: a keyless clone with no data/claims.db still answers from the corpus."""
    assert await claims_evidence(GS007, None) == []


@needs_db
async def test_database_rows_are_not_presented_to_the_model_as_corpus_text():
    """T-53 / R-01: two kinds of evidence, labelled as two kinds.

    Everything used to be wrapped in `<retrieved_corpus>` under the sentence "the
    following is retrieved corpus content", so a database row arrived labelled as
    the wrong kind of thing at the one place the model can see it — and "the
    corpus governs rules, the database reports facts" is a boundary the prompt has
    to state if the model is to respect it.
    """
    from app.retrieval.memory import FIXED_CHUNKS

    evidence = [*await claims_evidence(GS007, ClaimsTool()), *FIXED_CHUNKS]
    rendered = _format_evidence(evidence)

    assert "<claims_database>" in rendered
    assert "never establish a RULE" in rendered
    # The database block ends before the corpus block begins; no claims row is
    # inside the corpus wrapper.
    corpus_block = rendered.split("<retrieved_corpus>")[1]
    assert "claims:get_claim" not in corpus_block
    assert "cg-auto-2024#2.1" in corpus_block
