"""OpenAI SDK lives here and nowhere else.

Thin adapter: SDK exceptions → typed domain errors, SDK usage → Usage.
No retry, breaker, or budget policy. max_retries=0 on the client.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from app.domain.errors import (
    InvalidRequest,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
)
from app.llm.base import Completion, Message, Usage

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536

logger = logging.getLogger(__name__)


async def embed(texts: list[str], *, model: str = EMBED_MODEL) -> list[list[float]]:
    if not texts:
        return []
    client = AsyncOpenAI(max_retries=0)
    response = await client.embeddings.create(model=model, input=texts)
    by_index = {item.index: item.embedding for item in response.data}
    return [by_index[i] for i in range(len(texts))]


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        model: str | None = None,
    ) -> None:
        self.model = model or os.getenv("LLM_MODEL", "gpt-5.4-mini")
        self.reasoning_effort = os.getenv("LLM_REASONING_EFFORT", "none")
        self._default_max_output = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "800"))
        self._client = client or AsyncOpenAI(max_retries=0)

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Completion:
        cap = self._default_max_output if max_output_tokens is None else max_output_tokens
        instructions, items = _split_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": items,
            "temperature": 0,
            "max_output_tokens": cap,
            "reasoning": {"effort": self.reasoning_effort},
        }
        if instructions is not None:
            kwargs["instructions"] = instructions
        if schema is not None:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "answer",
                    "schema": schema,
                    "strict": False,
                }
            }
        if timeout_s is not None:
            kwargs["timeout"] = timeout_s

        log_context = {"model": self.model, "prompt": instructions or (messages[0].content if messages else "")}
        try:
            response = await self._client.responses.create(**kwargs)
        except APITimeoutError as exc:
            raise ProviderTimeout(context=log_context) from exc
        except RateLimitError as exc:
            raise RateLimited(context={**log_context, "upstream_status": 429}) from exc
        except APIConnectionError as exc:
            raise ProviderUnavailable(context=log_context) from exc
        except APIStatusError as exc:
            status = exc.status_code
            ctx = {**log_context, "upstream_status": status}
            if status >= 500:
                raise ProviderUnavailable(context=ctx) from exc
            raise InvalidRequest(context=ctx) from exc

        if response.status == "incomplete":
            logger.warning("finish_reason=length model=%s", self.model)

        text = response.output_text
        parsed: dict[str, Any] | None = None
        try:
            loaded = json.loads(text) if text else None
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            parsed = loaded

        usage = _usage_from(response.usage)
        return Completion(text=text, parsed=parsed, usage=usage, model=response.model or self.model)


def _split_messages(messages: list[Message]) -> tuple[str | None, list[dict[str, str]]]:
    instructions: str | None = None
    items: list[dict[str, str]] = []
    for message in messages:
        if message.role == "system" and instructions is None:
            instructions = message.content
        else:
            items.append({"role": message.role, "content": message.content})
    if not items:
        items = [{"role": "user", "content": ""}]
    return instructions, items


def _usage_from(usage: Any) -> Usage:
    if usage is None:
        return Usage()
    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) if details is not None else 0
    out_details = getattr(usage, "output_tokens_details", None)
    reasoning = getattr(out_details, "reasoning_tokens", 0) if out_details is not None else 0
    return Usage(
        prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
        cached_prompt_tokens=cached or 0,
        completion_tokens=getattr(usage, "output_tokens", 0) or 0,
        reasoning_tokens=reasoning or 0,
    )
