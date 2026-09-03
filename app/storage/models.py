"""SQLAlchemy Core schema. The source of truth Alembic diffs against.

Core, not the ORM: no declarative models, no relationships, no lazy loading.
`sql.py` wants parameter binding and Alembic wants a schema to compare — that is
the whole requirement, and an ORM here would obscure `hybrid.py`'s hand-written
retrieval SQL without replacing it.
"""

from __future__ import annotations

from typing import get_args

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR

from app.domain.models import TurnStatus

metadata = MetaData()

# Generated from the Literal, never retyped. Two spellings of this vocabulary
# would drift the day someone adds a sixth value; T-38 asserts they match.
MESSAGE_STATUS = Enum(
    *get_args(TurnStatus),
    name="message_status",
    metadata=metadata,
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", Text, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

messages = Table(
    "messages",
    metadata,
    Column("id", Text, primary_key=True),
    Column(
        "conversation_id",
        Text,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("client_message_id", Text, nullable=False),
    # Ordering is this column, never created_at: two turns inside one millisecond
    # order nondeterministically, and the gate is "in order".
    Column("seq", BigInteger, Identity(always=False), nullable=False),
    Column("question", Text, nullable=False),
    Column("status", MESSAGE_STATUS, nullable=False, server_default="pending"),
    Column("answer_text", Text),
    Column("error_code", Text),
    Column("reason", Text),
    Column("model", Text, nullable=False, server_default=""),
    Column("prompt_version", Text, nullable=False, server_default=""),
    Column("latency_ms", Integer, nullable=False, server_default="0"),
    # numeric, not float: this column exists to answer "what did we spend".
    Column("cost_usd", Numeric(10, 6), nullable=False, server_default="0"),
    Column("prompt_tokens", Integer, nullable=False, server_default="0"),
    Column("completion_tokens", Integer, nullable=False, server_default="0"),
    Column("cached_tokens", Integer, nullable=False, server_default="0"),
    Column("degraded", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("completed_at", DateTime(timezone=True)),
    # Idempotency lives here, not in a process's memory.
    UniqueConstraint("conversation_id", "client_message_id", name="messages_client_idem"),
    Index("messages_conversation_seq", "conversation_id", "seq"),
)

documents = Table(
    "documents",
    metadata,
    Column("id", Text, primary_key=True),  # document_code, e.g. CG-AUTO-2024
    Column("title", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("effective_date", Date, nullable=False),
    Column("product", Text, nullable=False),
    Column("doc_role", Text, nullable=False),
    Column("superseded", Boolean, nullable=False, server_default="false"),
    Column("source_path", Text),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

chunks = Table(
    "chunks",
    metadata,
    Column("id", Text, primary_key=True),
    Column("document_id", Text, ForeignKey("documents.id")),
    # The per-document columns below duplicate `documents` on purpose. hybrid.py
    # selects them by name inside a query whose ranking constants were measured
    # against the real index; rewriting it to save a join across 13 documents is
    # not worth the risk at this scale. Kept tradeoff, not deferred cleanup.
    Column("document_code", Text, nullable=False),
    Column("document_title", Text, nullable=False),
    Column("section", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("effective_date", Date, nullable=False),
    Column("product", Text, nullable=False),
    Column("doc_role", Text, nullable=False),
    Column("chunk_kind", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("superseded", Boolean, nullable=False, server_default="false"),
    Column("contains_pii", Boolean, nullable=False, server_default="false"),
    Column("pii_kinds", ARRAY(Text), nullable=False, server_default="{}"),
    Column("caption", Text),
    Column("footnotes", ARRAY(Text), nullable=False, server_default="{}"),
    Column("page_from", Integer, nullable=False),
    Column("page_to", Integer, nullable=False),
    Column("embedding", Vector(1536)),
    Column(
        "tsv",
        TSVECTOR,
        Computed(
            "to_tsvector('portuguese', coalesce(section, '') || ' ' || text)",
            persisted=True,
        ),
    ),
    Index("chunks_tsv_gin", "tsv", postgresql_using="gin"),
    Index("chunks_filters", "superseded", "product"),
    # No index on embedding: ~200 rows, exact scan beats HNSW. Revisit near 50k.
)

citations = Table(
    "citations",
    metadata,
    Column("id", BigInteger, Identity(always=False), primary_key=True),
    Column("message_id", Text, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("evidence_id", Text, nullable=False),
    # Deliberately NOT a foreign key to chunks. A citation is a point-in-time
    # record decoupled from the current index — the same reason document_code and
    # version are denormalised below. An FK would also block `TRUNCATE chunks`,
    # which is how a re-chunk after a parser change has to be done.
    Column("chunk_id", Text),
    # Captured as cited, not as the document reads now. Without this, the day
    # NI-014 v3.0 lands every historical answer claims to have cited it.
    Column("document_code", Text, nullable=False),
    Column("document_title", Text, nullable=False),
    Column("section", Text, nullable=False),
    Column("version", Text, nullable=False),
    Column("effective_date", Date, nullable=False),
    Column("snippet", Text, nullable=False),
    UniqueConstraint("message_id", "ordinal", name="citations_message_ordinal"),
    Index("citations_document", "document_code", "version"),
)

__all__ = [
    "MESSAGE_STATUS",
    "chunks",
    "citations",
    "conversations",
    "documents",
    "messages",
    "metadata",
]
