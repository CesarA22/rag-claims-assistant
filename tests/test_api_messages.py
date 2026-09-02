import asyncio
import json

from httpx import AsyncClient

from app.domain.errors import ProviderUnavailable
from app.llm.fake import FakeProvider
from app.retrieval.memory import InMemoryRetriever
from app.services.ask import ask
from app.storage.memory import InMemoryConversationRepository

VIGENCIA = "Qual é o prazo de vigência padrão de uma apólice de Seguro Auto?"
PATH = "/conversations/c-1/messages"


async def test_envelope_shape_and_completed_replay(
    client: AsyncClient, llm: FakeProvider
):
    """T-02 / R-01: envelope shape; duplicate client_message_id on a completed turn replays."""
    payload = {"content": VIGENCIA, "client_message_id": "cm-1"}
    first = await client.post(PATH, json=payload)
    assert first.status_code == 200
    body = first.json()

    assert body["outcome"] == "answered"
    assert body["conversation_id"] == "c-1"
    assert body["message_id"]
    assert "12 meses" in body["answer"]
    assert body["citations"]
    assert body["citations"][0]["document_code"] == "CG-AUTO-2024"
    assert body["citations"][0]["version"] == "3.2"
    assert body["citations"][0]["effective_date"] == "2024-01-01"
    meta = body["meta"]
    assert meta["provider"] == "fake"
    assert meta["model"] == "fake-1"
    assert meta["trace_id"]
    assert meta["degraded"] is False
    assert meta["cost_usd"] == 0.0
    assert set(meta["usage"]) == {
        "prompt_tokens",
        "cached_prompt_tokens",
        "completion_tokens",
    }
    assert llm.calls == 1

    second = await client.post(PATH, json=payload)
    assert second.status_code == 200
    replay = second.json()
    assert replay["message_id"] == body["message_id"]
    assert replay["answer"] == body["answer"]
    assert llm.calls == 1


async def test_in_flight_duplicate_returns_pending(llm: FakeProvider):
    """T-14 / R-01: duplicate client_message_id while in flight returns outcome=pending."""
    llm.hold = asyncio.Event()
    llm.entered = asyncio.Event()
    retriever = InMemoryRetriever()
    repo = InMemoryConversationRepository()
    kwargs = {
        "conversation_id": "c-1",
        "content": VIGENCIA,
        "client_message_id": "cm-inflight",
        "llm": llm,
        "retriever": retriever,
        "repo": repo,
        "trace_id": "t-1",
        "provider_name": "fake",
    }
    first_task = asyncio.create_task(ask(**kwargs))
    await llm.entered.wait()
    pending = await ask(**kwargs)
    assert pending.outcome == "pending"
    assert pending.answer is None
    assert pending.citations == []
    assert llm.calls == 1

    llm.hold.set()
    first = await first_task
    assert first.outcome == "answered"
    assert first.message_id == pending.message_id
    assert llm.calls == 1


async def test_provider_failure_is_problem_json_and_persists_failed(
    client: AsyncClient, llm: FakeProvider, repo: InMemoryConversationRepository
):
    """T-10 / R-06: provider failure is problem+json with a trace_id and no internals."""
    leak = (
        "openai gpt-5.4-mini failed; prompt=You are an internal assistant; "
        "traceback: File app/llm/openai_provider.py line 1"
    )
    llm.enqueue(ProviderUnavailable(leak))

    response = await client.post(
        PATH,
        json={"content": VIGENCIA, "client_message_id": "cm-fail"},
    )
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["trace_id"]
    assert body["status"] == 503
    assert "title" in body
    assert "detail" in body

    dumped = json.dumps(body).lower()
    assert "gpt-5.4-mini" not in dumped
    assert "openai" not in dumped
    assert "prompt" not in dumped
    assert "traceback" not in dumped
    assert "you are an internal assistant" not in dumped

    turn = repo.get_turn("c-1", "cm-fail")
    assert turn is not None
    assert turn.status == "failed"
    assert turn.error_code == "provider_unavailable"
