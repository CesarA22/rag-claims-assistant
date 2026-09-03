"""T-36 / T-33: the history surface, and healthz reporting the database.

GET renders for a human, so it carries all five statuses — including `failed`
with its error_code, which is why S1 put `failed` in TurnStatus on day one even
though no POST body ever returns it.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.domain.errors import ProviderUnavailable
from app.llm.fake import FakeProvider
from app.main import create_app
from app.retrieval.memory import InMemoryRetriever
from app.services.ask import PROMPT_VERSION
from app.storage.memory import InMemoryConversationRepository

VIGENCIA = "Qual é o prazo de vigência padrão de uma apólice de Seguro Auto?"
PATH = "/conversations/c-1/messages"


async def test_history_renders_answered_and_failed_in_order(
    client: AsyncClient, llm: FakeProvider
):
    """T-36 / R-01: reopening a conversation replays answered and failed turns, in order."""
    await client.post(PATH, json={"content": VIGENCIA, "client_message_id": "cm-1"})
    llm.enqueue(ProviderUnavailable("upstream 503"))
    await client.post(PATH, json={"content": "Pergunta que falha", "client_message_id": "cm-2"})

    response = await client.get(PATH)
    assert response.status_code == 200
    body = response.json()

    assert body["conversation_id"] == "c-1"
    assert [m["outcome"] for m in body["messages"]] == ["answered", "failed"]
    assert [m["question"] for m in body["messages"]] == [VIGENCIA, "Pergunta que falha"]

    answered, failed = body["messages"]
    assert "12 meses" in answered["answer"]
    assert answered["citations"][0]["document_code"] == "CG-AUTO-2024"
    assert failed["answer"] is None
    assert failed["error_code"] == "provider_unavailable"
    assert failed["citations"] == []


async def test_history_of_unknown_conversation_is_empty(client: AsyncClient):
    """T-36 / R-01: an unseen conversation is an empty list, not a 404."""
    response = await client.get("/conversations/c-never/messages")
    assert response.status_code == 200
    assert response.json() == {"conversation_id": "c-never", "messages": []}


async def test_history_never_renders_corpus_pii(llm: FakeProvider):
    """T-36 / R-03: the durable surface inherits the gates — a PII question stores no PII."""
    from tests.test_grounding import FORBIDDEN_NAMES, MINUTES_CHUNKS

    repo = InMemoryConversationRepository()
    app = create_app(
        llm=llm, retriever=InMemoryRetriever(MINUTES_CHUNKS), repo=repo, provider_name="fake"
    )
    llm.enqueue(
        FakeProvider.answered(
            "Marta Ferreira Bittencourt, CPF 111.111.111-11.", ["ata-com-2025-04#4"]
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            PATH,
            json={
                "content": "Liste o nome completo e o CPF dos segurados citados na ata.",
                "client_message_id": "cm-pii",
            },
        )
        history = (await c.get(PATH)).text

    assert "111.111.111-11" not in history
    for name in FORBIDDEN_NAMES:
        assert name not in history


async def test_healthz_reports_storage_and_database(client: AsyncClient):
    """T-33 / R-05: healthz carries the database block beside the breaker."""
    body = (await client.get("/healthz")).json()

    assert body["status"] == "ok"
    assert body["breaker"]["state"] == "closed"
    assert body["storage"] == "memory"
    # No engine configured with the in-memory repo — reported, not guessed at.
    assert body["database"] == {"configured": False, "reachable": None, "detail": None}


def test_prompt_version_is_a_stable_hash():
    """T-39 / R-04: prompt_version is a hash of the prompt and schema, not a hand-bumped number."""
    assert len(PROMPT_VERSION) == 12
    assert PROMPT_VERSION.isalnum()

    from app.services.ask import _prompt_version

    assert _prompt_version() == PROMPT_VERSION


async def test_prompt_version_is_persisted_on_the_turn(
    client: AsyncClient, repo: InMemoryConversationRepository
):
    """T-39 / R-04: the turn records which prompt produced it."""
    await client.post(PATH, json={"content": VIGENCIA, "client_message_id": "cm-pv"})

    turn = repo.get_turn("c-1", "cm-pv")
    assert turn is not None
    assert turn.prompt_version == PROMPT_VERSION
