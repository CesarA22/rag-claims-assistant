from typing import Literal, Protocol

from pydantic import BaseModel

from app.domain.models import Answer, HistoryMessage, Turn
from app.llm.base import Usage


class BeginTurnResult(BaseModel):
    turn: Turn
    kind: Literal["new", "replay", "in_flight"]


class ConversationRepository(Protocol):
    async def recent_messages(
        self,
        conversation_id: str,
        token_budget: int = 2000,
    ) -> list[HistoryMessage]: ...

    async def begin_turn(
        self,
        conversation_id: str,
        client_message_id: str,
        question: str,
    ) -> BeginTurnResult: ...

    async def complete_turn(
        self,
        turn: Turn,
        answer: Answer,
        usage: Usage,
        latency_ms: int,
        model: str = "",
        *,
        cost_usd: float = 0.0,
        degraded: bool = False,
        reason: str | None = None,
    ) -> Turn: ...

    async def fail_turn(self, turn: Turn, error_code: str) -> Turn: ...
