from __future__ import annotations

import uuid

from app.domain.models import Answer, HistoryMessage, Turn
from app.llm.base import Usage
from app.storage.base import BeginTurnResult


class InMemoryConversationRepository:
    """Dict-backed store. Replaced by sql.py. Three-branch begin_turn."""

    def __init__(self) -> None:
        self._by_client: dict[tuple[str, str], Turn] = {}
        self._by_id: dict[str, Turn] = {}
        self._order: dict[str, list[str]] = {}

    def get_turn(self, conversation_id: str, client_message_id: str) -> Turn | None:
        return self._by_client.get((conversation_id, client_message_id))

    async def history(self, conversation_id: str) -> list[Turn]:
        return [self._by_id[tid] for tid in self._order.get(conversation_id, [])]

    async def recent_messages(
        self,
        conversation_id: str,
        token_budget: int = 2000,
    ) -> list[HistoryMessage]:
        assembled: list[HistoryMessage] = []
        for turn_id in self._order.get(conversation_id, []):
            turn = self._by_id[turn_id]
            if turn.status in ("pending", "failed") or turn.answer is None:
                continue
            assembled.append(HistoryMessage(role="user", content=turn.question))
            assembled.append(
                HistoryMessage(role="assistant", content=turn.answer.text or "")
            )
        kept: list[HistoryMessage] = []
        remaining = token_budget
        for msg in reversed(assembled):
            cost = max(1, len(msg.content) // 4)
            if kept and cost > remaining:
                break
            kept.append(msg)
            remaining -= cost
        kept.reverse()
        return kept

    async def begin_turn(
        self,
        conversation_id: str,
        client_message_id: str,
        question: str,
    ) -> BeginTurnResult:
        key = (conversation_id, client_message_id)
        existing = self._by_client.get(key)
        if existing is not None:
            if existing.status == "pending":
                return BeginTurnResult(turn=existing, kind="in_flight")
            if existing.status == "failed":
                # A failed turn is re-openable. The idempotency key protects
                # against duplicate ANSWERS, not against retrying a turn that
                # produced none — and 40-frontend.mdc requires Retry to reuse
                # the same client_message_id, which would otherwise replay the
                # recorded failure and never call the provider. Same row, same
                # id, so D-02 still holds.
                reopened = existing.model_copy(
                    update={"status": "pending", "error_code": None, "answer": None}
                )
                self._store(reopened)
                return BeginTurnResult(turn=reopened, kind="new")
            return BeginTurnResult(turn=existing, kind="replay")
        turn = Turn(
            id=f"m-{uuid.uuid4().hex[:12]}",
            conversation_id=conversation_id,
            client_message_id=client_message_id,
            question=question,
            status="pending",
        )
        self._by_client[key] = turn
        self._by_id[turn.id] = turn
        self._order.setdefault(conversation_id, []).append(turn.id)
        return BeginTurnResult(turn=turn, kind="new")

    async def complete_turn(
        self,
        turn: Turn,
        answer: Answer,
        usage: Usage,
        latency_ms: int,
        model: str = "",
        *,
        cost_usd: float = 0.0,
        prompt_version: str = "",
        degraded: bool = False,
        reason: str | None = None,
    ) -> Turn:
        updated = turn.model_copy(
            update={
                "status": answer.outcome,
                "answer": answer,
                "model": model,
                "prompt_version": prompt_version,
                "latency_ms": latency_ms,
                "prompt_tokens": usage.prompt_tokens,
                "cached_prompt_tokens": usage.cached_prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cost_usd": cost_usd,
                "degraded": degraded,
                "reason": reason,
            }
        )
        self._store(updated)
        return updated

    async def fail_turn(self, turn: Turn, error_code: str) -> Turn:
        updated = turn.model_copy(
            update={"status": "failed", "error_code": error_code, "answer": None}
        )
        self._store(updated)
        return updated

    def _store(self, turn: Turn) -> None:
        self._by_client[(turn.conversation_id, turn.client_message_id)] = turn
        self._by_id[turn.id] = turn
