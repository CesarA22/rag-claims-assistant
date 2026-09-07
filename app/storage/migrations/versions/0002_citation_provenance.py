"""Citation provenance: source_kind and superseded.

D-05 asks each answer to indicate which source it came from and the freshness of
the queried data. `document_code`, `section`, `version` and `effective_date` were
already captured as cited, so the freshness line was one render away — but the
*kind* of source was not stored at all, and a citation replayed out of history
could not tell a controlled document from a database query without matching on
the string "claims.db".

The cheaper alternative was the `page_from`/`page_to` precedent: leave the fields
off the table and let them be null on replay. That was rejected here. Page numbers
are a display nicety and their absence costs a line in a panel; provenance is the
thing D-05 is *about*, and a history read that cannot say whether an answer rested
on a document or on a database query has lost the differential rather than
degraded it. So it costs a migration, and T-40 — which renders every revision
offline and compares it column-for-column against models.py — is why this file
lands in the same commit as the model change rather than after it.

Both columns are nullable with a server default rather than NOT NULL: rows written
before this revision are real citations whose provenance was simply never
recorded, and back-filling `source_kind='corpus'` onto them would be inventing a
fact about a historical answer. Null means "not recorded", which is true.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("citations", sa.Column("source_kind", sa.Text()))
    op.add_column(
        "citations",
        sa.Column("superseded", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("citations", "superseded")
    op.drop_column("citations", "source_kind")
