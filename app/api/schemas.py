from datetime import date

from pydantic import BaseModel, Field

from app.domain.models import TurnStatus


class MessageRequest(BaseModel):
    content: str
    client_message_id: str


class CitationOut(BaseModel):
    evidence_id: str
    document_code: str
    document_title: str
    section: str
    version: str
    effective_date: date
    snippet: str
    # Null on a citation read back from storage: the citations table has no page
    # columns and does not gain any this session. The panel omits the line.
    page_from: int | None = None
    page_to: int | None = None


class UsageOut(BaseModel):
    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    completion_tokens: int = 0


class MetaOut(BaseModel):
    trace_id: str
    provider: str
    model: str
    latency_ms: int
    cost_usd: float
    usage: UsageOut
    degraded: bool = False
    reason: str | None = None


class HistoryMessageOut(BaseModel):
    """A turn as history renders it — all five statuses, including failed."""

    message_id: str
    # The idempotency key, returned because Retry has to reuse it. Without it a
    # failed turn replayed from history has no key to re-post, and the client is
    # forced to mint one — which creates a second row and a second paid provider
    # call, the exact duplication D-02 exists to prevent.
    client_message_id: str
    question: str
    outcome: TurnStatus
    answer: str | None = None
    error_code: str | None = None
    # The safe message for error_code, derived at render time from the same map
    # problem+json uses. The client never owns a copy of the taxonomy.
    detail: str | None = None
    citations: list[CitationOut] = Field(default_factory=list)
    degraded: bool = False
    reason: str | None = None


class HistoryOut(BaseModel):
    conversation_id: str
    messages: list[HistoryMessageOut] = Field(default_factory=list)


class DatabaseOut(BaseModel):
    configured: bool
    reachable: bool | None = None
    detail: str | None = None


class BreakerOut(BaseModel):
    state: str
    consecutive_failures: int
    opened_at: float | None = None
    reset_in_s: float = 0.0


class HealthOut(BaseModel):
    status: str
    breaker: BreakerOut
    provider: str
    storage: str
    database: DatabaseOut
    degraded_since: float | None = None


class AnswerEnvelope(BaseModel):
    conversation_id: str
    message_id: str
    outcome: TurnStatus
    answer: str | None
    citations: list[CitationOut] = Field(default_factory=list)
    # Populated only on needs_clarification: the products the evidence spanned.
    clarification_options: list[str] = Field(default_factory=list)
    meta: MetaOut
