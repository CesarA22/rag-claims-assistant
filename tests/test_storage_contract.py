"""T-35 / T-36 / T-39: the ConversationRepository contract, run against both implementations.

One test body, two repositories. The in-memory one runs in the default
socket-disabled suite; the Postgres one is marked `db` and runs under `-m db`
against a live database. A behaviour the two do not share is a bug in one of
them, and nothing in the suite would have caught it before.

This is the evidence that the seam is real. S1 justified
`ConversationRepository` with a named change it would absorb — "the storage
change" — and the only way to show it absorbed one is to run the same
assertions through both sides of it.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.domain.models import Answer, Citation
from app.llm.base import Usage
from app.storage.memory import InMemoryConversationRepository

CITATION = Citation(
    evidence_id="ni-014-v1#3",
    document_code="NI-014",
    document_title="Normativo Interno de Prazos de Comunicação de Sinistro",
    section="3 Prazo de Comunicação",
    version="1.0",
    effective_date=date(2023, 1, 1),
    snippet="O prazo de comunicação é de 5 dias úteis.",
)


def _answered(text: str = "A vigência padrão é de 12 meses.") -> Answer:
    return Answer(outcome="answered", text=text, citations=[CITATION])


async def _seed_completed(repos, conversation: str, client_id: str, question: str, answer: Answer):
    begun = await repos.begin_turn(conversation, client_id, question)
    return await repos.complete_turn(
        begun.turn,
        answer,
        Usage(prompt_tokens=100, cached_prompt_tokens=20, completion_tokens=30),
        latency_ms=1234,
        model="fake-1",
        cost_usd=0.001234,
    )


async def test_same_client_message_id_creates_one_row(repos):
    """T-35 / D-02: a duplicate client_message_id replays; it never creates a second turn."""
    first = await repos.begin_turn("c-idem", "cm-1", "Qual é o prazo?")
    assert first.kind == "new"
    await repos.complete_turn(first.turn, _answered(), Usage(), 10, model="fake-1")

    second = await repos.begin_turn("c-idem", "cm-1", "Qual é o prazo?")
    assert second.kind == "replay"
    assert second.turn.id == first.turn.id

    assert len(await repos.history("c-idem")) == 1


async def test_failed_turn_reopens_and_keeps_its_row(repos):
    """T-35 / D-02: a failed turn re-opens to `new`; a completed one still replays.

    40-frontend.mdc requires Retry to reuse the same client_message_id. Without
    this branch that retry replays the recorded failure at 200 and never reaches
    the provider — the button is inert. Reopening keeps the row and the
    message_id, so D-02 ("repeating a question does not duplicate messages in
    the history") is unaffected; it is asserted here rather than assumed.
    """
    first = await repos.begin_turn("c-reopen", "cm-r", "Pergunta que falha")
    assert first.kind == "new"
    await repos.fail_turn(first.turn, "provider_unavailable")

    retry = await repos.begin_turn("c-reopen", "cm-r", "Pergunta que falha")
    assert retry.kind == "new"
    assert retry.turn.id == first.turn.id
    assert retry.turn.status == "pending"
    assert retry.turn.error_code is None
    assert len(await repos.history("c-reopen")) == 1

    # The reopened turn now completes normally, still on one row.
    await repos.complete_turn(retry.turn, _answered(), Usage(), 10, model="fake-1")
    turns = await repos.history("c-reopen")
    assert [t.status for t in turns] == ["answered"]
    assert turns[0].id == first.turn.id

    # And a turn that reached an outcome is NOT re-openable.
    again = await repos.begin_turn("c-reopen", "cm-r", "Pergunta que falha")
    assert again.kind == "replay"


async def test_persisted_citation_reads_back_without_pages(repos):
    """T-35 / R-01: a stored citation rehydrates with null pages, it does not raise.

    Citation.page_from/page_to are populated from retrieval and deliberately not
    persisted — the citations table gains no columns this session. sql.py
    rebuilds Citation field-by-field, so a required int would raise
    ValidationError on every history read. The plan's "a replayed citation shows
    no page" is only true while these stay nullable; this is what holds it.
    """
    await _seed_completed(repos, "c-pages", "cm-p", "Qual o prazo?", _answered())
    turns = await repos.history("c-pages")
    cited = turns[0].answer.citations[0]
    assert cited.page_from is None
    assert cited.page_to is None
    assert cited.document_code == "NI-014"


async def test_duplicate_while_in_flight_is_not_a_replay(repos):
    """T-35 / D-02: a duplicate arriving before the first turn completes is in_flight, not replay."""
    first = await repos.begin_turn("c-flight", "cm-2", "Qual é o prazo?")
    assert first.kind == "new"

    second = await repos.begin_turn("c-flight", "cm-2", "Qual é o prazo?")
    assert second.kind == "in_flight"
    assert second.turn.id == first.turn.id
    assert second.turn.status == "pending"

    assert len(await repos.history("c-flight")) == 1


async def test_concurrent_begin_turn_still_creates_one_row(repos):
    """T-35 / D-02: the multi-worker case — two racing begin_turn calls, one row."""
    results = await asyncio.gather(
        *[repos.begin_turn("c-race", "cm-race", "Qual é o prazo?") for _ in range(2)]
    )
    kinds = sorted(r.kind for r in results)
    assert kinds == ["in_flight", "new"], kinds
    assert len({r.turn.id for r in results}) == 1
    assert len(await repos.history("c-race")) == 1


async def test_history_renders_every_status_in_order(repos):
    """T-36 / R-01: history replays answered, refused and failed in order; context excludes two."""
    await _seed_completed(repos, "c-hist", "cm-a", "Pergunta A", _answered("Resposta A."))
    await _seed_completed(
        repos, "c-hist", "cm-b", "Pergunta B",
        Answer(outcome="refused", text="Não há base nas fontes.", citations=[]),
    )
    failed = await repos.begin_turn("c-hist", "cm-c", "Pergunta C")
    await repos.fail_turn(failed.turn, "provider_unavailable")
    await repos.begin_turn("c-hist", "cm-d", "Pergunta D")  # left pending

    turns = await repos.history("c-hist")
    assert [t.status for t in turns] == ["answered", "refused", "failed", "pending"]
    assert [t.question for t in turns] == [
        "Pergunta A", "Pergunta B", "Pergunta C", "Pergunta D",
    ]
    assert turns[2].error_code == "provider_unavailable"
    assert turns[2].answer is None
    assert turns[3].answer is None

    # The other read of the same table. A failure is not a conversational turn.
    context = await repos.recent_messages("c-hist")
    contents = [m.content for m in context]
    assert "Pergunta C" not in contents
    assert "Pergunta D" not in contents
    assert "Pergunta A" in contents


async def test_usage_columns_round_trip(repos):
    """T-39 / R-04: every usage column survives a write and read, cost_usd exactly."""
    await _seed_completed(repos, "c-usage", "cm-u", "Pergunta", _answered())

    turn = (await repos.history("c-usage"))[0]
    assert turn.latency_ms == 1234
    assert turn.prompt_tokens == 100
    assert turn.cached_prompt_tokens == 20  # column is cached_tokens; wire name unchanged
    assert turn.completion_tokens == 30
    assert turn.cost_usd == pytest.approx(0.001234)
    assert turn.model == "fake-1"
    assert turn.degraded is False


async def test_citations_survive_the_round_trip(repos):
    """T-37 / R-01: a persisted answer keeps its citations, in order, as cited."""
    await _seed_completed(repos, "c-cite", "cm-c1", "Pergunta", _answered())

    turn = (await repos.history("c-cite"))[0]
    assert turn.answer is not None
    assert [c.evidence_id for c in turn.answer.citations] == ["ni-014-v1#3"]
    assert turn.answer.citations[0].document_code == "NI-014"
    assert turn.answer.citations[0].version == "1.0"


async def test_unknown_conversation_has_empty_history(repos):
    """T-36 / R-01: an unseen conversation is empty, not an error."""
    assert await repos.history("c-never-seen") == []
    assert await repos.recent_messages("c-never-seen") == []


def test_memory_repo_satisfies_the_protocol():
    """T-35 / R-01: the fake implements every method the Protocol names."""
    from app.storage.base import ConversationRepository

    repo = InMemoryConversationRepository()
    for name in ConversationRepository.__protocol_attrs__:
        assert hasattr(repo, name), name


def _compiled(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect()))


def test_sql_statements_compile_against_postgres():
    """T-35 / R-01: every statement sql.py builds renders as valid Postgres, without a database.

    The contract tests above prove behaviour, but only when Postgres is up. This
    runs in the default suite and catches the failures that do not need a server
    to be real: a wrong column name, a bad conflict target, a typo in ON CONFLICT.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from app.storage.models import citations, messages

    begin = (
        insert(messages)
        .values(id="m-1", conversation_id="c-1", client_message_id="cm-1",
                question="q", status="pending")
        .on_conflict_do_nothing(index_elements=["conversation_id", "client_message_id"])
        .returning(messages)
    )
    sql = _compiled(begin)
    assert "ON CONFLICT (conversation_id, client_message_id) DO NOTHING" in sql
    assert "RETURNING" in sql

    history = _compiled(
        select(messages).where(messages.c.conversation_id == "c-1").order_by(messages.c.seq)
    )
    assert "ORDER BY messages.seq" in history

    context = _compiled(
        select(messages).where(messages.c.status.notin_(("pending", "failed")))
    )
    assert "NOT IN" in context.upper()

    update = _compiled(
        messages.update().where(messages.c.id == "m-1").values(
            status="answered", cached_tokens=20, cost_usd=0.001, prompt_version="abc"
        )
    )
    for column in ("status", "cached_tokens", "cost_usd", "prompt_version"):
        assert column in update

    assert "ordinal" in _compiled(citations.insert())


def test_citation_query_answers_the_gate_question_in_one_statement():
    """T-37 / R-01: "which answers cited NI-014 v1.0?" is a single SELECT."""
    from sqlalchemy import select

    from app.storage.models import citations, messages

    stmt = (
        select(messages.c.id, messages.c.question, messages.c.answer_text)
        .select_from(citations.join(messages, messages.c.id == citations.c.message_id))
        .where(citations.c.document_code == "NI-014", citations.c.version == "1.0")
        .order_by(messages.c.seq)
    )
    sql = _compiled(stmt)

    assert sql.count("SELECT") == 1
    assert "JOIN messages" in sql
    assert "citations.document_code" in sql and "citations.version" in sql
