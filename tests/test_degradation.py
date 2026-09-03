"""T-09 / T-10 / T-33: degradation rendering, log-only internals, breaker health."""

from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient

from app.domain.errors import ProviderUnavailable
from app.llm.fake import FakeProvider
from app.llm.resilient import ResilienceConfig, ResilientProvider
from app.main import create_app
from app.retrieval.memory import InMemoryRetriever

VIGENCIA = "Qual é o prazo de vigência padrão de uma apólice de Seguro Auto?"
PATH = "/conversations/c-1/messages"


async def test_degraded_excerpts_when_provider_fails_and_evidence_exists():
    """T-09 / R-05: provider down + retrieval up → 200, degraded excerpts, reason=provider_degraded."""
    inner = FakeProvider()
    inner.enqueue(ProviderUnavailable())
    llm = ResilientProvider(inner, ResilienceConfig(max_retries=0, breaker_failures=5))
    retriever = InMemoryRetriever()
    app = create_app(llm=llm, retriever=retriever, provider_name="fake")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            PATH, json={"content": VIGENCIA, "client_message_id": "cm-deg"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "answered"
    assert body["meta"]["degraded"] is True
    assert body["meta"]["reason"] == "provider_degraded"
    assert "resumo" in body["answer"].lower()
    assert body["citations"]
    retrieved_ids = {chunk.id for chunk in await retriever.search(VIGENCIA)}
    assert all(c["evidence_id"] in retrieved_ids for c in body["citations"])


async def test_open_circuit_serves_excerpts_with_circuit_open_reason():
    """T-09 / R-05: CircuitOpen with evidence → 200, same excerpts, meta.reason=circuit_open."""
    inner = FakeProvider()
    llm = ResilientProvider(
        inner, ResilienceConfig(max_retries=0, breaker_failures=1, breaker_reset_s=30.0)
    )
    retriever = InMemoryRetriever()
    app = create_app(llm=llm, retriever=retriever, provider_name="fake")

    inner.enqueue(ProviderUnavailable())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            PATH, json={"content": VIGENCIA, "client_message_id": "cm-trip"}
        )
        assert first.status_code == 200
        assert first.json()["meta"]["reason"] == "provider_degraded"
        calls_after_trip = inner.calls

        second = await client.post(
            PATH, json={"content": VIGENCIA, "client_message_id": "cm-open"}
        )

    assert second.status_code == 200
    body = second.json()
    assert body["meta"]["degraded"] is True
    assert body["meta"]["reason"] == "circuit_open"
    assert body["citations"]
    assert inner.calls == calls_after_trip


async def test_degraded_without_evidence_is_problem_json():
    """T-09 / R-05: no evidence in hand → 503 problem+json."""
    inner = FakeProvider()
    inner.enqueue(ProviderUnavailable())
    llm = ResilientProvider(inner, ResilienceConfig(max_retries=0, breaker_failures=5))
    app = create_app(
        llm=llm,
        retriever=InMemoryRetriever(chunks=[]),
        provider_name="fake",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            PATH, json={"content": VIGENCIA, "client_message_id": "cm-empty"}
        )

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 503
    dumped = json.dumps(body).lower()
    assert "gpt-5.4-mini" not in dumped
    assert "traceback" not in dumped


async def test_healthz_reports_breaker_state():
    """T-33 / R-05: GET /healthz reports closed, then open with reset_in_s after the breaker trips."""
    inner = FakeProvider()
    llm = ResilientProvider(
        inner, ResilienceConfig(max_retries=0, breaker_failures=1, breaker_reset_s=30.0)
    )
    app = create_app(llm=llm, retriever=InMemoryRetriever(), provider_name="fake")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        healthy = await client.get("/healthz")
        assert healthy.status_code == 200
        body = healthy.json()
        assert body["status"] == "ok"
        assert body["breaker"]["state"] == "closed"
        assert body["breaker"]["consecutive_failures"] == 0
        assert body["provider"] == "fake"

        inner.enqueue(ProviderUnavailable())
        await client.post(PATH, json={"content": VIGENCIA, "client_message_id": "cm-trip"})

        down = await client.get("/healthz")

    assert down.status_code == 200
    opened = down.json()
    assert opened["status"] == "degraded"
    assert opened["breaker"]["state"] == "open"
    assert opened["breaker"]["consecutive_failures"] >= 1
    assert opened["breaker"]["reset_in_s"] > 0
    assert opened["degraded_since"] is not None
