from collections.abc import Callable
from pathlib import Path

import pytest

from app.retrieval.ingest import DocumentIngest, _norm, ingest_corpus

CORPUS = Path(__file__).resolve().parents[1] / "data" / "corpus"

ATA_NAMES = (
    "Marta Ferreira Bittencourt",
    "Rogério Alcântara Nunes",
    "Helena Vasconcelos Prado",
    "Tadeu Marinho Quintela",
    "Isadora Beltrão Camargo",
)


@pytest.fixture(scope="module")
def docs() -> list[DocumentIngest]:
    return ingest_corpus(CORPUS)


def _by_code(docs: list[DocumentIngest], code: str, version: str) -> DocumentIngest:
    match = [doc for doc in docs if doc.code == code and doc.version == version]
    assert match, f"missing {code} v{version}"
    return match[0]


def _table(doc: DocumentIngest) -> str:
    tables = [chunk for chunk in doc.chunks if chunk.chunk_kind == "table"]
    assert tables, f"no table in {doc.code}"
    return tables[0].text


def assert_thirteen_tables_no_ladder(docs: list[DocumentIngest]) -> None:
    assert len(docs) == 13
    assert sum(doc.table_count for doc in docs) == 13
    assert all(not doc.ladder_pages for doc in docs)


def assert_ni022_note_three_only(docs: list[DocumentIngest]) -> None:
    ni = _by_code(docs, "NI-022", "1.3")
    table = next(chunk for chunk in ni.chunks if chunk.chunk_kind == "table")
    assert len(table.footnotes) == 1
    assert table.footnotes[0].startswith("(3)")
    assert "laudo técnico" in table.footnotes[0]
    assert not any(note.startswith("(1)") or note.startswith("(2)") for note in table.footnotes)


def assert_ata_pii_redacted(docs: list[DocumentIngest]) -> None:
    ata = _by_code(docs, "ATA-COM-2025-04", "final")
    table = next(chunk for chunk in ata.chunks if chunk.chunk_kind == "table")
    assert table.contains_pii
    assert "cpf" in table.pii_kinds
    assert "phone" in table.pii_kinds
    joined = "\n".join(chunk.text for chunk in ata.chunks)
    for name in ATA_NAMES:
        assert name not in joined
    assert "111.111.111-11" not in table.text
    assert "90000-0001" not in table.text


def assert_ni014_supersession(docs: list[DocumentIngest]) -> None:
    v1 = _by_code(docs, "NI-014", "1.0")
    v2 = _by_code(docs, "NI-014", "2.0")
    assert v1.superseded
    assert v1.superseded_reason is not None and "v2.0" in v1.superseded_reason
    assert all(chunk.superseded for chunk in v1.chunks)
    assert not v2.superseded
    assert not any(chunk.superseded for chunk in v2.chunks)


def assert_boilerplate_stripped(docs: list[DocumentIngest]) -> None:
    for doc in docs:
        for chunk in doc.chunks:
            for line in chunk.text.splitlines():
                stripped = line.strip()
                if stripped:
                    assert _norm(stripped) not in doc.boilerplate, (
                        f"{doc.code} {chunk.id} kept boilerplate {stripped!r}"
                    )


def assert_gs004_rcf_rows_together(docs: list[DocumentIngest]) -> None:
    text = _table(_by_code(docs, "CG-AUTO-2024", "3.2"))
    assert "100.000" in text
    assert "150.000" in text


def assert_gs009_vidros_both_products(docs: list[DocumentIngest]) -> None:
    auto = _table(_by_code(docs, "CG-AUTO-2024", "3.2"))
    res = _table(_by_code(docs, "CG-RES-2024", "2.1"))
    assert "5.000" in auto
    assert "3.000" in res


CASES: dict[str, Callable[[list[DocumentIngest]], None]] = {
    "thirteen_tables_no_ladder": assert_thirteen_tables_no_ladder,
    "ni022_note_three_only": assert_ni022_note_three_only,
    "ata_pii_redacted": assert_ata_pii_redacted,
    "ni014_supersession": assert_ni014_supersession,
    "boilerplate_stripped": assert_boilerplate_stripped,
    "gs004_rcf_rows_together": assert_gs004_rcf_rows_together,
    "gs009_vidros_both_products": assert_gs009_vidros_both_products,
}


@pytest.mark.parametrize("case", list(CASES))
def test_ingest_acceptance(docs: list[DocumentIngest], case: str) -> None:
    """T-15 / R-08 (R-03 for corpus PII): table-aware ingest of the 13 PDFs."""
    CASES[case](docs)
