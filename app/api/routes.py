from fastapi import APIRouter, Request

from app.api.schemas import (
    AnswerEnvelope,
    BreakerOut,
    CitationOut,
    HealthOut,
    MessageRequest,
    MetaOut,
    UsageOut,
)
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
    status = "ok" if snapshot["state"] == "closed" else "degraded"
    return HealthOut(
        status=status,
        breaker=BreakerOut(
            state=snapshot["state"],
            consecutive_failures=snapshot["consecutive_failures"],
            opened_at=snapshot["opened_at"],
            reset_in_s=snapshot["reset_in_s"],
        ),
        provider=request.app.state.provider_name,
        degraded_since=snapshot["opened_at"],
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
        citations=[
            CitationOut(
                evidence_id=c.evidence_id,
                document_code=c.document_code,
                document_title=c.document_title,
                section=c.section,
                version=c.version,
                effective_date=c.effective_date,
                snippet=c.snippet,
            )
            for c in result.citations
        ],
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
