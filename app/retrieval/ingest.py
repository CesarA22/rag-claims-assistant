"""Ingest the InsurCo corpus. Steps 1–5 of `.cursor/rules/15-retrieval.mdc`.

Step 6 (embed + to_tsvector) lands in S3. This module's only IO is reading PDFs.
"""

from __future__ import annotations

import argparse
import re
import sys
import warnings
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Literal

import pymupdf
from pydantic import BaseModel, Field

from app.domain.models import Product
from app.safety.redact import redact, redact_table_columns, scan

warnings.filterwarnings("ignore", message="Consider using the pymupdf_layout package")

DOC_META = re.compile(
    r"Código do documento:\s*(?P<code>[A-Z0-9\-]+)\s+"
    r"Versão:\s*(?P<version>\S+)\s+"
    r"Vigência a partir de:\s*(?P<effective>\d{4}-\d{2}-\d{2})"
)
CAPTION = re.compile(r"^Tabela\s+\d+\s*[-–—]\s+\S")
HEADING = re.compile(r"^\d+(\.\d+)*\s+[A-ZÀ-Ú]")
FOOTNOTE_LINE = re.compile(r"^\((\d+)\)\s+[A-ZÀ-Ú].+")
CELL_MARKER = re.compile(r"\((\d+)\)")
PRODUCT_BY_PREFIX = {
    "CG-AUTO": "Auto",
    "CG-RES": "Residencial",
    "CG-EMP": "Empresarial",
}

DocRole = Literal["normative", "pointer", "minutes", "glossary"]
ChunkKind = Literal["table", "prose"]

CAPTION_ABOVE_PT = 40.0
NOTE_CONTINUATION_GAP_PT = 20.0
PROSE_CAP = 1200
PROSE_OVERLAP = 150


class TableDetectionError(Exception):
    """A dash-anchored caption was found but find_tables() returned nothing."""


class Chunk(BaseModel):
    id: str
    document_code: str
    document_title: str
    section: str
    version: str
    effective_date: date
    product: Product
    doc_role: DocRole
    text: str
    superseded: bool = False
    chunk_kind: ChunkKind
    page_from: int
    page_to: int
    caption: str | None = None
    footnotes: list[str] = Field(default_factory=list)
    contains_pii: bool = False
    pii_kinds: list[str] = Field(default_factory=list)


class DocumentIngest(BaseModel):
    code: str
    version: str
    title: str
    effective_date: date
    product: Product
    doc_role: DocRole
    page_count: int
    superseded: bool = False
    superseded_reason: str | None = None
    boilerplate: list[str] = Field(default_factory=list)
    chunks: list[Chunk] = Field(default_factory=list)
    table_count: int = 0
    footnotes_attached: int = 0
    pii_chunks: int = 0
    ladder_pages: list[int] = Field(default_factory=list)
    history_rows: list[tuple[str, str]] = Field(default_factory=list)


class _Line(BaseModel):
    page: int
    text: str
    bbox: tuple[float, float, float, float]


def product_for(code: str) -> Product:
    return next(
        (product for prefix, product in PRODUCT_BY_PREFIX.items() if code.startswith(prefix)),
        "All",
    )


def doc_role_for(code: str) -> DocRole:
    if code.startswith("FAQ"):
        return "pointer"
    if code.startswith("ATA"):
        return "minutes"
    if code.startswith("GLOS"):
        return "glossary"
    return "normative"


def parse_metadata(text: str) -> tuple[str, str, date]:
    match = DOC_META.search(text)
    if match is None:
        raise ValueError("DOC_META header not found")
    return match["code"], match["version"], date.fromisoformat(match["effective"])


def _norm(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip())


def boilerplate_keys(lines: list[_Line], page_count: int) -> set[str]:
    """A digit-normalised line appearing on every page is header/footer, not content."""
    if page_count <= 1:
        return set()
    pages_for: dict[str, set[int]] = {}
    for line in lines:
        if line.text.strip():
            pages_for.setdefault(_norm(line.text), set()).add(line.page)
    return {key for key, pages in pages_for.items() if len(pages) >= page_count}


def _page_lines(page: pymupdf.Page, page_number: int) -> list[_Line]:
    lines: list[_Line] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                lines.append(_Line(page=page_number, text=text, bbox=tuple(line["bbox"])))
    return lines


def _overlaps(line: _Line, bbox: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = line.bbox
    tx0, ty0, tx1, ty1 = bbox
    return not (x1 < tx0 or x0 > tx1 or y1 < ty0 or y0 > ty1)


def _caption_for(table_bbox: tuple[float, float, float, float], lines: list[_Line]) -> str | None:
    candidates: list[tuple[float, str]] = []
    top = table_bbox[1]
    for line in lines:
        if not CAPTION.match(line.text):
            continue
        gap = top - line.bbox[3]
        if -2.0 <= gap <= CAPTION_ABOVE_PT:
            candidates.append((gap, line.text))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _footnotes_for(
    table_bbox: tuple[float, float, float, float],
    lines: list[_Line],
    cell_markers: set[str],
    skip: set[str],
) -> tuple[list[str], set[str]]:
    """Attach notes whose (n) marker appears in a cell. Fold continuation lines."""
    below = sorted(
        (line for line in lines if line.bbox[1] >= table_bbox[3] - 2),
        key=lambda line: line.bbox[1],
    )
    collected: dict[str, str] = {}
    consumed: set[str] = set()
    current: str | None = None
    last_y = 0.0
    for line in below:
        if _norm(line.text) in skip:
            current = None
            continue
        match = FOOTNOTE_LINE.match(line.text)
        if match:
            current = match.group(1)
            collected[current] = line.text
            consumed.add(line.text)
            last_y = line.bbox[3]
            continue
        if (
            current is not None
            and line.bbox[1] - last_y <= NOTE_CONTINUATION_GAP_PT
            and not HEADING.match(line.text)
            and not CAPTION.match(line.text)
        ):
            collected[current] = f"{collected[current]} {line.text}"
            consumed.add(line.text)
            last_y = line.bbox[3]
        else:
            current = None
    attached = [collected[n] for n in sorted(collected) if n in cell_markers]
    attached_consumed = {text for text in consumed if any(text in note for note in attached)}
    return attached, attached_consumed


def _markdown_table(headers: list[str], rows: list[list[str | None]]) -> str:
    def cells(row: list[str | None]) -> str:
        cleaned = [
            (cell or "").replace("\n", " ").replace("|", "\\|").strip() for cell in row
        ]
        while len(cleaned) < len(headers):
            cleaned.append("")
        return "| " + " | ".join(cleaned[: len(headers)]) + " |"

    header_row = cells(headers)
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join(cells(row) for row in rows)
    return f"{header_row}\n{sep}\n{body}" if body else f"{header_row}\n{sep}"


def _split_header_rows(table: object) -> tuple[list[str], list[list[str | None]]]:
    extracted = table.extract()
    names = [name or "" for name in table.header.names] if table.header else []
    if (
        table.header
        and not table.header.external
        and extracted
        and [cell or "" for cell in extracted[0]] != names
    ):
        extracted = [names, *extracted]
    if not extracted:
        return names or [""], []
    headers = [cell or "" for cell in extracted[0]]
    data = [list(row) for row in extracted[1:]]
    return headers, data


def _history_rows(headers: list[str], rows: list[list[str | None]]) -> list[tuple[str, str]]:
    lower = [h.strip().lower() for h in headers]
    try:
        version_i = next(i for i, h in enumerate(lower) if "vers" in h)
        status_i = next(i for i, h in enumerate(lower) if "situa" in h)
    except StopIteration:
        return []
    out: list[tuple[str, str]] = []
    for row in rows:
        version = (row[version_i] or "").strip() if version_i < len(row) else ""
        status = (row[status_i] or "").strip() if status_i < len(row) else ""
        if version and status:
            out.append((version, status))
    return out


def extract_tables(
    page: pymupdf.Page,
    page_number: int,
    skip: set[str],
    source: str,
) -> tuple[list[dict], set[str], bool]:
    """find_tables() BEFORE prose. Returns (table dicts, consumed line texts, ladder_fired)."""
    lines = _page_lines(page, page_number)
    captions = [line.text for line in lines if CAPTION.match(line.text)]
    finder = page.find_tables()
    tables = list(finder.tables) if finder else []
    ladder = False
    if captions and not tables:
        ladder = True
        finder = page.find_tables(strategy="text")
        tables = list(finder.tables) if finder else []
        if captions and not tables:
            raise TableDetectionError(
                f"{source} p{page_number}: caption(s) {captions!r} but find_tables() returned none"
            )

    consumed: set[str] = set()
    out: list[dict] = []
    for table in tables:
        bbox = tuple(table.bbox)
        caption = _caption_for(bbox, lines)
        if caption is None and table.header and table.header.external:
            candidate = (table.header.names[0] or "").strip()
            if CAPTION.match(candidate):
                caption = candidate
        headers, rows = _split_header_rows(table)
        cell_text = " ".join(
            cell for row in [headers, *rows] for cell in row if cell
        )
        markers = set(CELL_MARKER.findall(cell_text))
        footnotes, note_lines = _footnotes_for(bbox, lines, markers, skip)
        if caption:
            consumed.add(caption)
        consumed.update(note_lines)
        out.append(
            {
                "bbox": bbox,
                "caption": caption,
                "headers": headers,
                "rows": rows,
                "footnotes": footnotes,
                "history": _history_rows(headers, rows),
            }
        )
    return out, consumed, ladder


def _apply_pii_to_table(headers: list[str], rows: list[list[str | None]]) -> tuple[str, set[str], bool]:
    raw = _markdown_table(headers, rows)
    kinds = scan(raw)
    _, blanked_rows, indexes = redact_table_columns(headers, rows)
    rendered = redact(_markdown_table(headers, blanked_rows))
    return rendered, kinds, bool(indexes) or bool(kinds)


def split_prose(
    lines: list[_Line],
    *,
    cap: int = PROSE_CAP,
    overlap: int = PROSE_OVERLAP,
) -> list[tuple[str, str, int, int]]:
    """Split on numbered headings. Overlap stays inside a heading."""
    heading = "preâmbulo"
    buf: list[_Line] = []
    sections: list[tuple[str, list[_Line]]] = []

    def flush() -> None:
        nonlocal buf, heading
        if buf:
            sections.append((heading, buf))
        buf = []

    for line in lines:
        if HEADING.match(line.text):
            flush()
            heading = line.text
            continue
        buf.append(line)
    flush()

    chunks: list[tuple[str, str, int, int]] = []
    for heading, body_lines in sections:
        if not body_lines:
            continue
        body = " ".join(line.text for line in body_lines)
        page_from = body_lines[0].page
        page_to = body_lines[-1].page
        text = f"{heading}\n\n{body}"
        if len(text) <= cap:
            chunks.append((heading, text, page_from, page_to))
            continue
        pieces = _window(text, cap, overlap)
        for piece in pieces:
            chunks.append((heading, piece, page_from, page_to))
    return chunks


def _window(text: str, cap: int, overlap: int) -> list[str]:
    pieces: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + cap, length)
        if end < length:
            window = text[start:end]
            cut = max(window.rfind(". "), window.rfind(" "))
            if cut >= cap // 3:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return pieces


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "chunk"


def _finish_chunk(chunk: Chunk) -> Chunk:
    kinds = scan(chunk.text)
    chunk.text = redact(chunk.text)
    if kinds:
        chunk.contains_pii = True
        chunk.pii_kinds = sorted(kinds)
    return chunk


def build_document(path: Path) -> DocumentIngest:
    doc = pymupdf.open(path)
    try:
        raw = "".join(page.get_text() for page in doc)
        code, version, effective = parse_metadata(raw)
        product = product_for(code)
        role = doc_role_for(code)

        all_lines = [
            line
            for i, page in enumerate(doc, 1)
            for line in _page_lines(page, i)
        ]
        skip = boilerplate_keys(all_lines, doc.page_count)
        stripped = [line.text for line in all_lines if _norm(line.text) not in skip]
        title = _title_from(stripped)

        table_records: list[dict] = []
        consumed: set[str] = set()
        ladder_pages: list[int] = []
        history: list[tuple[str, str]] = []
        bboxes_by_page: dict[int, list[tuple[float, float, float, float]]] = {}

        for page_number, page in enumerate(doc, 1):
            tables, used, fired = extract_tables(page, page_number, skip, path.name)
            if fired:
                ladder_pages.append(page_number)
            consumed.update(used)
            for table in tables:
                table["page"] = page_number
                table_records.append(table)
                history.extend(table["history"])
                bboxes_by_page.setdefault(page_number, []).append(table["bbox"])

        prose_lines: list[_Line] = []
        for line in all_lines:
            if _norm(line.text) in skip:
                continue
            if line.text in consumed:
                continue
            if DOC_META.search(line.text) or line.text == title:
                continue
            if any(_overlaps(line, bbox) for bbox in bboxes_by_page.get(line.page, [])):
                continue
            prose_lines.append(line)

        chunks: list[Chunk] = []
        for i, table in enumerate(table_records, 1):
            body, kinds, flagged = _apply_pii_to_table(table["headers"], table["rows"])
            caption = table["caption"] or f"Tabela {i}"
            parts = [caption, "", body]
            if table["footnotes"]:
                parts.append("")
                parts.extend(f"> {note}" for note in table["footnotes"])
            chunk = Chunk(
                id=f"{code.lower()}#v{version}#tabela-{i}",
                document_code=code,
                document_title=title,
                section=caption,
                version=version,
                effective_date=effective,
                product=product,
                doc_role=role,
                text="\n".join(parts),
                chunk_kind="table",
                page_from=table["page"],
                page_to=table["page"],
                caption=caption,
                footnotes=table["footnotes"],
                contains_pii=flagged,
                pii_kinds=sorted(kinds),
            )
            chunk.text = redact(chunk.text)
            chunks.append(chunk)

        seen: Counter[str] = Counter()
        for heading, text, page_from, page_to in split_prose(prose_lines):
            seen[heading] += 1
            suffix = "" if seen[heading] == 1 else f"-{seen[heading]}"
            chunk = Chunk(
                id=f"{code.lower()}#v{version}#{_slug(heading)}{suffix}",
                document_code=code,
                document_title=title,
                section=heading,
                version=version,
                effective_date=effective,
                product=product,
                doc_role=role,
                text=text,
                chunk_kind="prose",
                page_from=page_from,
                page_to=page_to,
            )
            chunks.append(_finish_chunk(chunk))

        pii_chunks = sum(1 for chunk in chunks if chunk.contains_pii)
        notes = sum(len(chunk.footnotes) for chunk in chunks)
        return DocumentIngest(
            code=code,
            version=version,
            title=title,
            effective_date=effective,
            product=product,
            doc_role=role,
            page_count=doc.page_count,
            boilerplate=sorted(skip),
            chunks=chunks,
            table_count=len(table_records),
            footnotes_attached=notes,
            pii_chunks=pii_chunks,
            ladder_pages=ladder_pages,
            history_rows=history,
        )
    finally:
        doc.close()


def _title_from(stripped: list[str]) -> str:
    for i, line in enumerate(stripped):
        if DOC_META.search(line) and i > 0:
            return stripped[i - 1]
    raise ValueError("document title not found above DOC_META")


def resolve_superseded(docs: list[DocumentIngest]) -> None:
    """Histórico 'Superada' is primary; latest-effective-date-wins is the fallback."""
    declared: dict[tuple[str, str], str] = {}
    for doc in docs:
        for version, status in doc.history_rows:
            if re.search(r"superada", status, re.IGNORECASE):
                declared[(doc.code, version)] = (
                    f"superada @ {doc.code} v{doc.version} Histórico"
                )

    by_code: dict[str, list[DocumentIngest]] = {}
    for doc in docs:
        by_code.setdefault(doc.code, []).append(doc)

    for doc in docs:
        key = (doc.code, doc.version)
        if key in declared:
            doc.superseded = True
            doc.superseded_reason = declared[key]
        else:
            later = [other for other in by_code[doc.code] if other.effective_date > doc.effective_date]
            if later:
                newest = max(later, key=lambda item: item.effective_date)
                doc.superseded = True
                doc.superseded_reason = (
                    f"later effective {newest.version} {newest.effective_date.isoformat()}"
                )
        if doc.superseded:
            for chunk in doc.chunks:
                chunk.superseded = True


def ingest_corpus(corpus_dir: Path | str) -> list[DocumentIngest]:
    paths = sorted(Path(corpus_dir).glob("*.pdf"))
    if not paths:
        raise FileNotFoundError(f"no PDFs in {corpus_dir}")
    docs = [build_document(path) for path in paths]
    resolve_superseded(docs)
    return docs


def report(docs: list[DocumentIngest]) -> str:
    headers = (
        f"{'CODE':<18} {'VER':<6} {'EFFECTIVE':<12} {'PRODUCT':<13} {'ROLE':<10} "
        f"{'SUPERSEDED':<11} {'PAGES':>5} {'CHUNKS':>6} {'TABLES':>6} {'NOTES':>5} {'PII':>3}"
    )
    rows = [headers]
    for doc in docs:
        flag = "YES" if doc.superseded else "no"
        line = (
            f"{doc.code:<18} {doc.version:<6} {doc.effective_date.isoformat():<12} "
            f"{doc.product:<13} {doc.doc_role:<10} {flag:<11} {doc.page_count:>5} "
            f"{len(doc.chunks):>6} {doc.table_count:>6} {doc.footnotes_attached:>5} "
            f"{doc.pii_chunks:>3}"
        )
        rows.append(line)
        if doc.superseded_reason:
            rows.append(f"  {doc.superseded_reason}")
        rows.append(
            f"  title={doc.title}  boilerplate={len(doc.boilerplate)} lines  "
            f"ladder={doc.ladder_pages or 'never'}"
        )
    totals = (
        f"totals: {len(docs)} documents · {sum(len(d.chunks) for d in docs)} chunks · "
        f"{sum(d.table_count for d in docs)} tables · "
        f"{sum(d.footnotes_attached for d in docs)} footnotes attached · "
        f"{sum(d.pii_chunks for d in docs)} PII chunks"
    )
    rows.append(totals)
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest data/corpus/*.pdf (steps 1–5).")
    parser.add_argument("corpus", nargs="?", default="data/corpus", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the report and write nothing. The only mode in S2.",
    )
    args = parser.parse_args(argv)
    docs = ingest_corpus(args.corpus)
    print(report(docs))
    if not args.dry_run:
        print(
            "index write is S3; rerun with --dry-run. Nothing was stored.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
