"""T-40 / R-01: the Alembic revision and models.py describe the same schema.

S7a hand-writes revision 0001 rather than autogenerating it, because `chunks.tsv`
is a GENERATED column and `chunks.embedding` is vector(1536) — neither survives
autogenerate cleanly, and a revision that drops the generated clause takes the
lexical arm of retrieval with it.

The cost of hand-writing is two descriptions of one schema that can drift. The
usual guard is `alembic revision --autogenerate`, which needs a live database and
so cannot run in the default socket-disabled suite. This does the same job
offline: it renders the migration with `upgrade --sql` (no connection opened) and
compares the column names it creates against `models.metadata`.

Names only, deliberately — a renamed or forgotten column is the realistic drift,
and comparing rendered type text would fail on spelling differences that do not
matter (TEXT vs Text, DEFAULT '0' vs DEFAULT 0).
"""

from __future__ import annotations

import io
import re
from contextlib import redirect_stdout
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.storage.models import metadata

ROOT = Path(__file__).resolve().parents[1]

_CREATE_TABLE = re.compile(
    r"CREATE TABLE (\w+) \((.*?)\n\);", re.DOTALL | re.IGNORECASE
)


def _render_migration_sql() -> str:
    config = Config(str(ROOT / "alembic.ini"))
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        command.upgrade(config, "head", sql=True)
    return buffer.getvalue()


def _columns_from_sql(body: str) -> set[str]:
    columns: set[str] = set()
    for raw in body.splitlines():
        line = raw.strip().rstrip(",")
        if not line:
            continue
        head = line.split()[0].upper()
        if head in {"PRIMARY", "FOREIGN", "CONSTRAINT", "UNIQUE", "CHECK"}:
            continue
        columns.add(line.split()[0])
    return columns


def test_migration_and_models_agree_on_every_column():
    """T-40 / R-01: revision 0001 creates exactly the columns models.py declares."""
    sql = _render_migration_sql()
    rendered = {
        name: _columns_from_sql(body) for name, body in _CREATE_TABLE.findall(sql)
    }
    rendered.pop("alembic_version", None)

    assert set(rendered) == set(metadata.tables), (
        f"tables differ: migration={sorted(rendered)} models={sorted(metadata.tables)}"
    )
    for name, table in metadata.tables.items():
        assert rendered[name] == {c.name for c in table.columns}, f"columns differ on {name}"


def test_migration_preserves_the_two_columns_autogenerate_would_break():
    """T-40 / R-09: chunks.tsv keeps its GENERATED clause and embedding stays vector(1536)."""
    sql = _render_migration_sql()

    assert "GENERATED ALWAYS AS (to_tsvector('portuguese'" in sql
    assert "STORED" in sql
    assert "VECTOR(1536)" in sql.upper()
    assert "USING gin (tsv)" in sql


def test_citations_has_no_foreign_key_to_chunks():
    """T-40 / R-01: chunk_id is a plain column — an FK would block TRUNCATE chunks on re-chunk."""
    sql = _render_migration_sql()
    citations = dict(_CREATE_TABLE.findall(sql))["citations"]

    assert "chunk_id TEXT" in citations
    assert "REFERENCES chunks" not in citations
