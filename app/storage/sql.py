"""Postgres-backed ConversationRepository.

The seam S1 wrote `ConversationRepository` for. `ask.py` does not change to use
this — if it had to, the Protocol was decoration.

The reason this exists is not scale, it is correctness: the in-memory store keeps
`client_message_id` in a per-process dict, so two uvicorn workers each hold their
own and the same id posted twice creates two turns and two provider calls. D-02
was true at one worker. `messages_client_idem` makes it true.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.models import Answer, Citation, HistoryMessage, Turn
from app.llm.base import Usage
from app.storage.base import BeginTurnResult
from app.storage.models import citations, conversations, messages

_CONTEXT_EXCLUDED = ("pending", "failed")


class SqlConversationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def begin_turn(
        self,
        conversation_id: str,
        client_message_id: str,
        question: str,
    ) -> BeginTurnResult:
        """Three branches, decided by the database rather than by a dict.

        `ON CONFLICT DO NOTHING RETURNING` gives back no row when the pair is
        already taken, which is the whole signal. Race-safe across workers, which
        is what the dict could never be.
        """
        async with self._sessions() as session, session.begin():
            await session.execute(
                insert(conversations)
                .values(id=conversation_id)
                .on_conflict_do_nothing(index_elements=["id"])
            )
            turn_id = f"m-{uuid.uuid4().hex[:12]}"
            inserted = (
                await session.execute(
                    insert(messages)
                    .values(
                        id=turn_id,
                        conversation_id=conversation_id,
                        client_message_id=client_message_id,
                        question=question,
                        status="pending",
                    )
                    .on_conflict_do_nothing(
                        index_elements=["conversation_id", "client_message_id"]
                    )
                    .returning(messages)
                )
            ).mappings().first()

            if inserted is not None:
                return BeginTurnResult(turn=_turn(inserted, []), kind="new")

            # A failed turn is re-openable, because the idempotency key
            # protects against duplicate ANSWERS and a failure is not one.
            # 40-frontend.mdc requires Retry to reuse the same
            # client_message_id; without this the retry replays the recorded
            # failure at 200 and never reaches the provider.
            #
            # `AND status = 'failed'` is what makes it race-safe: of two
            # simultaneous retries one wins the UPDATE and gets `new`, the other
            # reads `pending` and gets `in_flight`. Same three branches, same
            # row, same message_id — D-02 is unaffected.
            reopened = (
                await session.execute(
                    messages.update()
                    .where(
                        messages.c.conversation_id == conversation_id,
                        messages.c.client_message_id == client_message_id,
                        messages.c.status == "failed",
                    )
                    .values(
                        status="pending",
                        error_code=None,
                        answer_text=None,
                        completed_at=None,
                    )
                    .returning(messages)
                )
            ).mappings().first()
            if reopened is not None:
                await session.execute(
                    citations.delete().where(citations.c.message_id == reopened["id"])
                )
                return BeginTurnResult(turn=_turn(reopened, []), kind="new")

            existing = (
                await session.execute(
                    select(messages).where(
                        messages.c.conversation_id == conversation_id,
                        messages.c.client_message_id == client_message_id,
                    )
                )
            ).mappings().one()
            rows = await self._citation_rows(session, existing["id"])
            kind = "in_flight" if existing["status"] == "pending" else "replay"
            return BeginTurnResult(turn=_turn(existing, rows), kind=kind)

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
        async with self._sessions() as session, session.begin():
            await session.execute(
                messages.update()
                .where(messages.c.id == turn.id)
                .values(
                    status=answer.outcome,
                    answer_text=answer.text,
                    reason=reason,
                    model=model,
                    prompt_version=prompt_version,
                    latency_ms=latency_ms,
                    # Usage.cached_prompt_tokens is the wire name and does not
                    # move — T-02 asserts the usage keys exactly. The column is
                    # cached_tokens; the mapping lives here and only here.
                    cached_tokens=usage.cached_prompt_tokens,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    cost_usd=Decimal(str(cost_usd)),
                    degraded=degraded,
                    completed_at=_now(),
                )
            )
            await session.execute(
                citations.delete().where(citations.c.message_id == turn.id)
            )
            if answer.citations:
                await session.execute(
                    citations.insert(),
                    [
                        {
                            "message_id": turn.id,
                            "ordinal": i,
                            "evidence_id": c.evidence_id,
                            "chunk_id": c.evidence_id,
                            "document_code": c.document_code,
                            "document_title": c.document_title,
                            "section": c.section,
                            "version": c.version,
                            "effective_date": c.effective_date,
                            "snippet": c.snippet,
                        }
                        for i, c in enumerate(answer.citations)
                    ],
                )
        return turn.model_copy(
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

    async def fail_turn(self, turn: Turn, error_code: str) -> Turn:
        async with self._sessions() as session, session.begin():
            await session.execute(
                messages.update()
                .where(messages.c.id == turn.id)
                .values(
                    status="failed",
                    error_code=error_code,
                    answer_text=None,
                    completed_at=_now(),
                )
            )
        return turn.model_copy(
            update={"status": "failed", "error_code": error_code, "answer": None}
        )

    async def history(self, conversation_id: str) -> list[Turn]:
        """All five statuses, oldest first. Ordered by seq, never created_at."""
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(messages)
                    .where(messages.c.conversation_id == conversation_id)
                    .order_by(messages.c.seq)
                )
            ).mappings().all()
            if not rows:
                return []
            cited = await self._citation_rows_for(session, [r["id"] for r in rows])
        return [_turn(row, cited.get(row["id"], [])) for row in rows]

    async def recent_messages(
        self,
        conversation_id: str,
        token_budget: int = 2000,
    ) -> list[HistoryMessage]:
        """Context for the model: answered turns only, newest kept under budget."""
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(messages)
                    .where(
                        messages.c.conversation_id == conversation_id,
                        messages.c.status.notin_(_CONTEXT_EXCLUDED),
                    )
                    .order_by(messages.c.seq)
                )
            ).mappings().all()

        assembled: list[HistoryMessage] = []
        for row in rows:
            if row["answer_text"] is None:
                continue
            assembled.append(HistoryMessage(role="user", content=row["question"]))
            assembled.append(HistoryMessage(role="assistant", content=row["answer_text"]))
        return _fit(assembled, token_budget)

    async def _citation_rows(self, session: AsyncSession, message_id: str) -> list[Citation]:
        found = await self._citation_rows_for(session, [message_id])
        return found.get(message_id, [])

    async def _citation_rows_for(
        self, session: AsyncSession, message_ids: list[str]
    ) -> dict[str, list[Citation]]:
        rows = (
            await session.execute(
                select(citations)
                .where(citations.c.message_id.in_(message_ids))
                .order_by(citations.c.message_id, citations.c.ordinal)
            )
        ).mappings().all()
        grouped: dict[str, list[Citation]] = {}
        for row in rows:
            grouped.setdefault(row["message_id"], []).append(
                Citation(
                    evidence_id=row["evidence_id"],
                    document_code=row["document_code"],
                    document_title=row["document_title"],
                    section=row["section"],
                    version=row["version"],
                    effective_date=row["effective_date"],
                    snippet=row["snippet"],
                )
            )
        return grouped


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _fit(assembled: list[HistoryMessage], token_budget: int) -> list[HistoryMessage]:
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


def _turn(row, cited: list[Citation]) -> Turn:
    answer = None
    if row["answer_text"] is not None and row["status"] != "failed":
        answer = Answer(
            outcome=row["status"] if row["status"] != "pending" else "answered",
            text=row["answer_text"],
            citations=cited,
        )
    return Turn(
        id=row["id"],
        conversation_id=row["conversation_id"],
        client_message_id=row["client_message_id"],
        question=row["question"],
        status=row["status"],
        answer=answer,
        error_code=row["error_code"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        latency_ms=row["latency_ms"],
        prompt_tokens=row["prompt_tokens"],
        cached_prompt_tokens=row["cached_tokens"],
        completion_tokens=row["completion_tokens"],
        cost_usd=float(row["cost_usd"]),
        degraded=row["degraded"],
        reason=row["reason"],
    )
