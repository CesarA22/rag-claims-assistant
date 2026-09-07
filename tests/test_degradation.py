"""T-09 / T-10 / T-33 / T-45: degradation rendering, log-only internals, breaker health."""

from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient

from app.domain.errors import ProviderUnavailable
from app.llm.fake import FakeProvider
from app.llm.resilient import ResilienceConfig, ResilientProvider
from app.main import create_app
from app.retrieval.memory import InMemoryRetriever
from tests.test_grounding import ATA_PII, CPF_PATTERN, FORBIDDEN_NAMES, MINUTES_CHUNKS

VIGENCIA = "Qual é o prazo de vigência padrão de uma apólice de Seguro Auto?"
# Neutral on its face — it asks about process, not about people — and answerable
# only from a chunk that happens to carry the names.
NEUTRAL_MINUTES = "O que o comitê de sinistros graves deliberou em abril de 2025?"
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


async def _degraded_post(question: str, key: str, chunks):
    """One POST whose provider is down and whose retrieval succeeded.

    The shared `app`/`client` fixtures cannot drive this: their retriever holds
    two Auto chunks with no PII, so the branch is reachable but the leak is not.
    The provider does have to be wrapped — a raw FakeProvider raising
    ProviderUnavailable is caught by `except InsurCoError` and returns 503 — but
    the fixture that actually blocks this test is the retriever, not the
    provider. Widening the except clause instead is the wrong turn: it flips T-10
    and T-36 to the wrong behaviour.
    """
    inner = FakeProvider()
    inner.enqueue(ProviderUnavailable())
    llm = ResilientProvider(inner, ResilienceConfig(max_retries=0, breaker_failures=5))
    app = create_app(llm=llm, retriever=InMemoryRetriever(chunks), provider_name="fake")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(PATH, json={"content": question, "client_message_id": key})


def _assert_no_policyholder_identities(response) -> dict:
    """No name and no CPF anywhere in the serialised body.

    The whole body, not just `answer`: on the degraded path the payload *is* raw
    retrieved text, so the leak surface is the citation snippets. Matches T-18's
    scope for the same reason.
    """
    body = response.json()
    dumped = json.dumps(body, ensure_ascii=False)
    for name in FORBIDDEN_NAMES:
        assert name not in dumped, f"policyholder name {name!r} shipped in a degraded response"
    assert CPF_PATTERN.search(dumped) is None
    return body


async def test_pii_question_during_an_outage_is_refused_not_excerpted():
    """T-45 / R-03: the PII gate applies when the provider is down, not only when it answers.

    The degraded handler called `excerpts_answer(evidence)` directly and never
    called `judge()`, so requirement 3 was suspended inside the scenario of
    requirement 5. `redact()` strips CPF, phone and e-mail from a snippet but
    cannot match a name, so the names shipped intact.
    """
    response = await _degraded_post(ATA_PII, "cm-pii-deg", MINUTES_CHUNKS)

    assert response.status_code == 200
    body = _assert_no_policyholder_identities(response)
    assert body["outcome"] == "refused"
    assert body["citations"] == []
    assert body["meta"]["degraded"] is True
    assert body["meta"]["reason"] == "provider_degraded"
    assert "dados pessoais" in body["answer"].lower()


async def test_neutral_question_over_pii_evidence_is_also_refused_when_degraded():
    """T-45 / R-03: the degraded gate keys on the evidence alone, not on the question.

    This is the case that chooses the fix. `grounding.is_pii_request` is an AND of
    question-shape and evidence-shape, and on the answered path that AND is
    defensible — `judge()` has the sufficiency gate and `redact()` behind it. Here
    there is no model answer at all; the payload is the retrieved text itself, so
    the question-shape half decides nothing and a neutral question over the same
    chunk shipped two policyholder names. `evidence_carries_pii` alone is the gate.
    """
    response = await _degraded_post(NEUTRAL_MINUTES, "cm-neutral-deg", MINUTES_CHUNKS)

    assert response.status_code == 200
    body = _assert_no_policyholder_identities(response)
    assert body["outcome"] == "refused"
    assert body["citations"] == []
    assert body["meta"]["degraded"] is True


async def test_degraded_excerpts_still_ship_when_the_evidence_is_clean():
    """T-45 / R-05: the gate is not a blanket kill switch for the degraded path.

    Without this, deleting `excerpts_answer` from the handler entirely would pass
    the two tests above, and requirement 5's whole point — useful excerpts when
    the provider is down — would be gone.
    """
    response = await _degraded_post(VIGENCIA, "cm-clean-deg", None)

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "answered"
    assert body["citations"]
    assert body["meta"]["degraded"] is True
