from __future__ import annotations

import asyncio
import re
from collections import deque
from typing import Any

from app.llm.base import Completion, Message, Usage

_EVIDENCE_ID = re.compile(r"evidence_id=(\S+)")

_CANNED_ANSWER = "A vigência padrão é de 12 meses."
_CANNED_EVIDENCE_ID = "cg-auto-2024#2.1"


class FakeProvider:
    """Scriptable double. Queue holds completions or exceptions; empty → canned answer."""

    name = "fake"

    def __init__(self) -> None:
        self.queue: deque[Completion | BaseException] = deque()
        self.calls = 0
        self.timeouts: list[float | None] = []
        self.max_output_tokens_seen: list[int | None] = []
        self.hold: asyncio.Event | None = None
        self.entered: asyncio.Event | None = None

    def enqueue(self, item: Completion | BaseException) -> None:
        self.queue.append(item)

    @staticmethod
    def answered(
        text: str,
        evidence_ids: list[str],
        *,
        model: str = "fake-1",
    ) -> Completion:
        return Completion(
            text=text,
            parsed={
                "outcome": "answered",
                "answer": text,
                "citations": [{"evidence_id": eid} for eid in evidence_ids],
            },
            usage=Usage(),
            model=model,
        )

    @staticmethod
    def needs_clarification(text: str, *, model: str = "fake-1") -> Completion:
        return Completion(
            text=text,
            parsed={
                "outcome": "needs_clarification",
                "answer": text,
                "citations": [],
            },
            usage=Usage(),
            model=model,
        )

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Completion:
        self.calls += 1
        self.timeouts.append(timeout_s)
        self.max_output_tokens_seen.append(max_output_tokens)
        if self.entered is not None:
            self.entered.set()
        if self.hold is not None:
            await self.hold.wait()
        if self.queue:
            item = self.queue.popleft()
            if isinstance(item, BaseException):
                raise item
            return item
        return self._canned(messages)

    def _canned(self, messages: list[Message]) -> Completion:
        ids = _EVIDENCE_ID.findall("\n".join(m.content for m in messages))
        evidence_id = _CANNED_EVIDENCE_ID if _CANNED_EVIDENCE_ID in ids else (ids[0] if ids else "")
        if not evidence_id:
            return Completion(
                text="Não encontrei evidência para responder.",
                parsed={
                    "outcome": "refused",
                    "answer": "Não encontrei evidência para responder.",
                    "citations": [],
                },
                model="fake-1",
            )
        return self.answered(_CANNED_ANSWER, [evidence_id])
