from app.llm.fake import FakeProvider
from app.retrieval.memory import InMemoryRetriever
from app.services.ask import ask
from app.storage.memory import InMemoryConversationRepository

VIGENCIA = "Qual é o prazo de vigência padrão de uma apólice de Seguro Auto?"


async def _ask(llm: FakeProvider, **kwargs):
    defaults = {
        "conversation_id": "c-1",
        "content": VIGENCIA,
        "client_message_id": "cm-1",
        "llm": llm,
        "retriever": InMemoryRetriever(),
        "repo": InMemoryConversationRepository(),
        "trace_id": "t-1",
        "provider_name": "fake",
    }
    defaults.update(kwargs)
    return await ask(**defaults)


async def test_happy_path_cites_retrieved_evidence():
    """T-01 / R-01: a grounded answer cites evidence that was actually retrieved."""
    llm = FakeProvider()
    retriever = InMemoryRetriever()
    result = await _ask(llm, retriever=retriever)

    assert result.outcome == "answered"
    assert result.answer is not None
    assert "12 meses" in result.answer
    assert result.citations
    retrieved_ids = {chunk.id for chunk in await retriever.search(VIGENCIA)}
    assert all(citation.evidence_id in retrieved_ids for citation in result.citations)
    assert result.citations[0].document_code == "CG-AUTO-2024"
    assert result.citations[0].version == "3.2"


async def test_needs_clarification_round_trips():
    """T-13 / R-01: a scripted needs_clarification outcome is live in the envelope."""
    llm = FakeProvider()
    llm.enqueue(
        FakeProvider.needs_clarification(
            "Qual produto você quer consultar: Auto, Residencial ou Empresarial?"
        )
    )
    result = await _ask(llm)

    assert result.outcome == "needs_clarification"
    assert result.answer is not None
    assert "Auto" in result.answer
    assert result.citations == []


async def test_invented_citation_forces_refusal():
    """T-03 / R-02: a citation to an evidence_id that was not retrieved forces a refusal."""
    llm = FakeProvider()
    llm.enqueue(FakeProvider.answered("42%", ["not-in-the-index"]))
    result = await _ask(llm)

    assert result.outcome == "refused"
    assert result.citations == []
    assert "42%" not in (result.answer or "")
