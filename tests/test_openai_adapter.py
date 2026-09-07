"""T-28 / T-34 / T-48: adapter maps SDK errors; the output cap, reasoning pin and strict flag cannot be dropped."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from openai import AsyncOpenAI

from app.domain.errors import InvalidRequest, ProviderTimeout, ProviderUnavailable, RateLimited
from app.llm.base import Message
from app.llm.openai_provider import OpenAIProvider
from app.services.ask import DRAFT_SCHEMA

MSGS = [Message(role="system", content="You are an assistant."), Message(role="user", content="q")]
URL = "https://api.openai.com/v1/responses"

SUCCESS_BODY = {
    "id": "resp_1",
    "object": "response",
    "created_at": 0,
    "model": "gpt-5.4-mini",
    "output": [
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [
                {
                    "type": "output_text",
                    "text": '{"outcome":"answered","answer":"ok","citations":[]}',
                    "annotations": [],
                }
            ],
        }
    ],
    "parallel_tool_calls": False,
    "tool_choice": "auto",
    "tools": [],
    "usage": {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    },
    "status": "completed",
}


def _provider() -> OpenAIProvider:
    http_client = httpx.AsyncClient()
    client = AsyncOpenAI(api_key="sk-test", max_retries=0, http_client=http_client)
    return OpenAIProvider(client=client)


def test_client_retries_are_disabled():
    """T-28 / R-07: the SDK client is constructed with max_retries=0."""
    provider = OpenAIProvider(client=AsyncOpenAI(api_key="sk-test", max_retries=0))
    assert provider._client.max_retries == 0


@respx.mock
async def test_status_codes_map_to_typed_errors():
    """T-28 / R-07: 429 → RateLimited, 500 → ProviderUnavailable, 400 → InvalidRequest, timeout → ProviderTimeout."""
    error = {"error": {"message": "x", "type": "x"}}
    cases = [
        (429, RateLimited),
        (500, ProviderUnavailable),
        (400, InvalidRequest),
    ]
    for status, exc_type in cases:
        respx.post(URL).mock(return_value=httpx.Response(status, json=error))
        provider = _provider()
        with pytest.raises(exc_type):
            await provider.complete(MSGS)
        assert respx.calls.call_count == 1
        respx.calls.clear()

    respx.post(URL).mock(side_effect=httpx.TimeoutException("timed out"))
    provider = _provider()
    with pytest.raises(ProviderTimeout):
        await provider.complete(MSGS)
    assert respx.calls.call_count == 1


@respx.mock
async def test_adapter_always_sends_output_cap_and_reasoning_none():
    """T-34 / R-04: max_output_tokens and reasoning.effort are always explicit on the wire."""
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=SUCCESS_BODY))
    provider = _provider()
    result = await provider.complete(MSGS)

    assert result.parsed is not None
    assert result.parsed["outcome"] == "answered"
    assert result.usage.cached_prompt_tokens == 2
    assert result.usage.reasoning_tokens == 0
    assert route.call_count == 1
    body = json.loads(route.calls.last.request.content.decode())
    assert body["max_output_tokens"] == 800
    assert body["reasoning"]["effort"] == "none"
    assert body["temperature"] == 0


@respx.mock
async def test_a_schema_is_always_sent_strict():
    """T-48 / R-01: `strict` is True on the wire whenever a schema is passed.

    With strict False the live model honoured the schema on about one call in
    three and otherwise echoed the JSON Schema back, which the pipeline surfaced
    as a citation refusal. Nothing offline can reproduce that — FakeProvider
    throws `schema` away — so the flag itself is what gets pinned.

    Asserted on its own call, not bolted onto the cap/reasoning test above:
    that one calls complete() with no schema, so `body["text"]` does not exist.
    """
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=SUCCESS_BODY))
    provider = _provider()
    await provider.complete(MSGS, schema=DRAFT_SCHEMA)

    body = json.loads(route.calls.last.request.content.decode())
    fmt = body["text"]["format"]
    assert fmt["strict"] is True
    assert fmt["type"] == "json_schema"
    assert fmt["schema"] == DRAFT_SCHEMA


@respx.mock
async def test_no_schema_means_no_text_format_block():
    """T-48 / R-07: the schema-free call is unchanged — no `text` key at all."""
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=SUCCESS_BODY))
    provider = _provider()
    await provider.complete(MSGS)

    assert "text" not in json.loads(route.calls.last.request.content.decode())


@respx.mock
async def test_an_incomplete_response_is_reported_as_truncated():
    """T-48 / R-05: `status: incomplete` reaches the caller instead of only the log.

    The output cap is our own budget policy. _parse_draft has to tell it apart
    from the provider returning a body that is not the schema, and the provider's
    own status is the only honest signal for that.
    """
    body = {**SUCCESS_BODY, "status": "incomplete"}
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    provider = _provider()

    assert (await provider.complete(MSGS)).truncated is True

    respx.post(URL).mock(return_value=httpx.Response(200, json=SUCCESS_BODY))
    assert (await _provider().complete(MSGS)).truncated is False
