from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Outcome = Literal["answered", "refused", "needs_clarification"]
TurnStatus = Literal[
    "pending", "answered", "refused", "needs_clarification", "failed"
]
Product = Literal["Auto", "Residencial", "Empresarial", "All"]
DocRole = Literal["normative", "pointer", "minutes", "glossary", "database"]


class Evidence(BaseModel):
    id: str
    document_code: str
    document_title: str
    section: str
    version: str
    effective_date: date
    product: Product
    text: str
    superseded: bool = False
    source_kind: Literal["corpus", "claims"] = "corpus"
    doc_role: DocRole = "normative"
    contains_pii: bool = False


class Citation(BaseModel):
    evidence_id: str
    document_code: str
    document_title: str
    section: str
    version: str
    effective_date: date
    snippet: str


class Answer(BaseModel):
    outcome: Outcome
    text: str | None = None
    citations: list[Citation] = Field(default_factory=list)


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Turn(BaseModel):
    id: str
    conversation_id: str
    client_message_id: str
    question: str
    status: TurnStatus = "pending"
    answer: Answer | None = None
    error_code: str | None = None
    model: str = ""
    prompt_version: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    degraded: bool = False
    reason: str | None = None
