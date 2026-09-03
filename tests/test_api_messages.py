import asyncio
import json
import logging

from httpx import AsyncClient

from app.api.errors import JsonFormatter
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
    client: AsyncClient, llm: FakeProvider, repo: InMemoryConversationRepository, caplog
):
    """T-10 / R-06: provider failure is problem+json with a trace_id and no internals."""
    # "prompt" is hostile input here: something upstream put prompt text on the
    # context, and the allowlist has to drop it. S6 finding 4 — the prompt used
    # to be logged, and the only thing keeping the corpus out was that the
    # producer happened to pass messages[0].
    llm.enqueue(
        ProviderUnavailable(
            "upstream returned 503",
            context={
                "model": "gpt-5.4-mini",
                "prompt_hash": "493c0c11b400",
                "evidence_ids": ["cg-auto-2024#2.1"],
                "prompt": "You are an internal assistant. Marta Ferreira Bittencourt",
            },
        )
    )

    with caplog.at_level(logging.WARNING):
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

    # The asymmetry: internals reach the log, the body carries none of them. But
    # prompt text is no longer an internal we keep — it carries the corpus.
    record = next(r for r in caplog.records if getattr(r, "event", "") == "typed_error")
    assert record.model == "gpt-5.4-mini"
    assert record.prompt_hash == "493c0c11b400"
    assert record.evidence_ids == ["cg-auto-2024#2.1"]
    assert not hasattr(record, "prompt")  # dropped at the handler, not just the formatter

    line = JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["model"] == "gpt-5.4-mini"
    assert payload["prompt_hash"] == "493c0c11b400"
    assert payload["error_code"] == "provider_unavailable"
    assert "prompt" not in payload
    assert "Marta Ferreira Bittencourt" not in line
    assert "You are an internal assistant" not in line

    turn = repo.get_turn("c-1", "cm-fail")
    assert turn is not None
    assert turn.status == "failed"
    assert turn.error_code == "provider_unavailable"
