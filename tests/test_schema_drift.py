"""T-40 / T-54 / R-01: the Alembic revision and models.py describe the same schema.

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

The rendered SQL is the WHOLE chain, not revision 0001, so later revisions have
to be applied to the picture the CREATE TABLE statements build. S11 added
revision 0002, which is `ALTER TABLE citations ADD COLUMN` — invisible to a
CREATE-only reader, so this test reported as missing the very columns the
migration had just added.
"""

from __future__ import annotations

import io
import re
from contextlib import redirect_stdout
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.domain.models import TurnStatus
from app.storage.models import MESSAGE_STATUS, metadata

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


_ALTER_COLUMN = re.compile(
    r"ALTER TABLE (\w+) (ADD|DROP) COLUMN (\w+)\b", re.IGNORECASE
)


def _apply_alters(sql: str, rendered: dict[str, set[str]]) -> None:
    """Replay ALTER TABLE ADD/DROP COLUMN onto the CREATE TABLE picture.

    In the order they appear, so an add followed by a later drop leaves the
    column absent — which is what running the chain actually does.
    """
    for table, action, column in _ALTER_COLUMN.findall(sql):
        if table not in rendered:
            continue
        if action.upper() == "ADD":
            rendered[table].add(column)
        else:
            rendered[table].discard(column)


def test_migration_and_models_agree_on_every_column():
    """T-40 / R-01: the whole revision chain creates exactly the columns models.py declares."""
    sql = _render_migration_sql()
    rendered = {
        name: _columns_from_sql(body) for name, body in _CREATE_TABLE.findall(sql)
    }
    _apply_alters(sql, rendered)
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


def test_the_drift_check_sees_alter_table_add_column():
    """T-40 / R-01: the reader itself is checked, because a blind one passes everything.

    A CREATE-TABLE-only reader does not FAIL on a revision that adds a column —
    it silently stops seeing that column, and goes on passing while models.py and
    the database diverge. Asserting the ALTER is picked up is the difference
    between a guard and a formality.
    """
    sql = _render_migration_sql()
    assert "ALTER TABLE citations ADD COLUMN source_kind" in sql

    rendered = {
        name: _columns_from_sql(body) for name, body in _CREATE_TABLE.findall(sql)
    }
    assert "source_kind" not in rendered["citations"]      # not in any CREATE TABLE
    _apply_alters(sql, rendered)
    assert "source_kind" in rendered["citations"]          # added by revision 0002
    assert "superseded" in rendered["citations"]


def test_the_status_enum_is_generated_from_the_literal():
    """T-54 / R-01: MESSAGE_STATUS and TurnStatus carry the same five values.

    `app/storage/models.py` claimed for two sessions that a test asserted this,
    naming an id nobody had written. The claim was reasonable — the enum IS built
    with `get_args(TurnStatus)`, so it cannot drift by being retyped — but a
    source comment naming a test that does not exist is the same dishonesty the
    register check now catches, in a file no register rule looks at.

    The dead id is deliberately not spelled out here. `scripts/traceability.py`
    harvests every `T-nn` it finds in a test file, so writing one in prose would
    make that id look real to the very check that exists to say it is not.

    Written rather than deleted because the property is worth pinning: the
    generation could be replaced with a literal tuple in one careless edit, and
    the failure mode is a status the database rejects at write time, on the
    failure path, in production.
    """
    from typing import get_args

    assert tuple(MESSAGE_STATUS.enums) == get_args(TurnStatus)
    assert MESSAGE_STATUS.name == "message_status"
    assert "failed" in MESSAGE_STATUS.enums   # the one only the error path uses
