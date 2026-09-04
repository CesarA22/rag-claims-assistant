from fastapi import APIRouter, Request
from sqlalchemy import text

from app.api.schemas import (
    AnswerEnvelope,
    BreakerOut,
    CitationOut,
    DatabaseOut,
    HealthOut,
    HistoryMessageOut,
    HistoryOut,
    MessageRequest,
    MetaOut,
    UsageOut,
)
from app.api.errors import safe_detail
from app.services.ask import AskResult, ask

router = APIRouter()


@router.get("/healthz", response_model=HealthOut)
async def healthz(request: Request) -> HealthOut:
    llm = request.app.state.llm
    breaker = getattr(llm, "breaker", None)
    if breaker is None:
        snapshot = {
            "state": "closed",
            "consecutive_failures": 0,
            "opened_at": None,
            "reset_in_s": 0.0,
        }
    else:
        snapshot = breaker.snapshot()
    database = await _database_health(request)
    breaker_ok = snapshot["state"] == "closed"
    # A database outage is not a degraded answer, it is no answer: an answer that
    # cannot be recorded is not given, because the citation record is this
    # product's compliance artifact. So it downgrades status on its own.
    status = "ok" if breaker_ok and database.reachable is not False else "degraded"
    return HealthOut(
        status=status,
        breaker=BreakerOut(
            state=snapshot["state"],
            consecutive_failures=snapshot["consecutive_failures"],
            opened_at=snapshot["opened_at"],
            reset_in_s=snapshot["reset_in_s"],
        ),
        provider=request.app.state.provider_name,
        storage=getattr(request.app.state, "storage", "memory"),
        database=database,
        degraded_since=snapshot["opened_at"],
    )


async def _database_health(request: Request) -> DatabaseOut:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return DatabaseOut(configured=False)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any driver error means "not reachable"
        # Type name only. The DSN carries a password and never reaches a body.
        return DatabaseOut(configured=True, reachable=False, detail=type(exc).__name__)
    return DatabaseOut(configured=True, reachable=True)


@router.get("/conversations/{conversation_id}/messages", response_model=HistoryOut)
async def get_messages(conversation_id: str, request: Request) -> HistoryOut:
    """History for a human: all five statuses, oldest first.

    Deliberately not `recent_messages`, which feeds the model and drops failed
    and pending turns. Two reads of one table; see storage/base.py.
    """
    turns = await request.app.state.repo.history(conversation_id)
    return HistoryOut(
        conversation_id=conversation_id,
        messages=[
            HistoryMessageOut(
                message_id=turn.id,
                client_message_id=turn.client_message_id,
                question=turn.question,
                outcome=turn.status,
                answer=turn.answer.text if turn.answer else None,
                error_code=turn.error_code,
                detail=safe_detail(turn.error_code),
                citations=[_citation_out(c) for c in (turn.answer.citations if turn.answer else [])],
                degraded=turn.degraded,
                reason=turn.reason,
            )
            for turn in turns
        ],
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=AnswerEnvelope,
)
async def post_message(
    conversation_id: str,
    body: MessageRequest,
    request: Request,
) -> AnswerEnvelope:
    result = await ask(
        conversation_id=conversation_id,
        content=body.content,
        client_message_id=body.client_message_id,
        llm=request.app.state.llm,
        retriever=request.app.state.retriever,
        repo=request.app.state.repo,
        trace_id=request.state.trace_id,
        provider_name=request.app.state.provider_name,
    )
    return _to_envelope(result)


def _to_envelope(result: AskResult) -> AnswerEnvelope:
    return AnswerEnvelope(
        conversation_id=result.conversation_id,
        message_id=result.message_id,
        outcome=result.outcome,
        answer=result.answer,
        citations=[_citation_out(c) for c in result.citations],
        clarification_options=result.clarification_options,
        meta=MetaOut(
            trace_id=result.trace_id,
            provider=result.provider,
            model=result.model,
            latency_ms=result.latency_ms,
            cost_usd=result.cost_usd,
            usage=UsageOut(
                prompt_tokens=result.prompt_tokens,
                cached_prompt_tokens=result.cached_prompt_tokens,
                completion_tokens=result.completion_tokens,
            ),
            degraded=result.degraded,
            reason=result.reason,
        ),
    )


def _citation_out(citation) -> CitationOut:
    return CitationOut(
        evidence_id=citation.evidence_id,
        document_code=citation.document_code,
        document_title=citation.document_title,
        section=citation.section,
        version=citation.version,
        effective_date=citation.effective_date,
        snippet=citation.snippet,
        page_from=citation.page_from,
        page_to=citation.page_to,
    )
