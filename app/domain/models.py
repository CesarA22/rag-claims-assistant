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
    page_from: int | None = None
    page_to: int | None = None


class Citation(BaseModel):
    evidence_id: str
    document_code: str
    document_title: str
    section: str
    version: str
    effective_date: date
    snippet: str
    # D-05 provenance. `source_kind` says whether this came from a controlled
    # document or from a named database query; `superseded` says whether the
    # version cited has since been replaced. Both are persisted (revision 0002),
    # so a citation replayed from history keeps its provenance — unlike the page
    # numbers below. `source_kind` is nullable because rows written before that
    # revision never recorded it, and null means "not recorded", which is true.
    source_kind: Literal["corpus", "claims"] | None = None
    superseded: bool = False
    # Nullable, and that is load-bearing rather than lazy. sql.py rebuilds a
    # Citation field-by-field from the citations table, which has no page
    # columns and is not gaining any — a required int would raise on every
    # history read. A replayed citation shows no page; the panel omits the line
    # rather than inventing one.
    page_from: int | None = None
    page_to: int | None = None


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
