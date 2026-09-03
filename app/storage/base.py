from typing import Literal, Protocol

from pydantic import BaseModel

from app.domain.models import Answer, HistoryMessage, Turn
from app.llm.base import Usage


class BeginTurnResult(BaseModel):
    turn: Turn
    kind: Literal["new", "replay", "in_flight"]


class ConversationRepository(Protocol):
    async def history(self, conversation_id: str) -> list[Turn]:
        """Every turn, oldest first, including failed and pending.

        Distinct from recent_messages on purpose. This renders for a human, so
        it shows a failure with its error_code and a turn still running. That is
        why `failed` has been in TurnStatus since S1.
        """
        ...

    async def recent_messages(
        self,
        conversation_id: str,
        token_budget: int = 2000,
    ) -> list[HistoryMessage]:
        """Context for the model. Excludes failed and pending turns — a failure
        is not a conversational turn, and replaying one invites the model to
        apologise for an error the analyst never saw.
        """
        ...

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
        prompt_version: str = "",
        degraded: bool = False,
        reason: str | None = None,
    ) -> Turn: ...

    async def fail_turn(self, turn: Turn, error_code: str) -> Turn: ...
