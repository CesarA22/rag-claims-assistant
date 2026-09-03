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


class BreakerOut(BaseModel):
    state: str
    consecutive_failures: int
    opened_at: float | None = None
    reset_in_s: float = 0.0


class HealthOut(BaseModel):
    status: str
    breaker: BreakerOut
    provider: str
    degraded_since: float | None = None


class AnswerEnvelope(BaseModel):
    conversation_id: str
    message_id: str
    outcome: TurnStatus
    answer: str | None
    citations: list[CitationOut] = Field(default_factory=list)
    meta: MetaOut
