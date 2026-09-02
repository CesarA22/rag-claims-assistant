# Technical decisions

Tradeoffs, what was cut, and why. Appended as sessions land.

## S1 — `model` and `provider` in success meta, never in errors

T-10 forbids the model name in error payloads. Every successful answer
envelope still carries `meta.model` and `meta.provider`.

That is deliberate. An error payload is the artefact that travels — it gets
forwarded, pasted into a ticket and screenshotted — and provider error strings
are the place where prompt fragments and version detail leak. The ban is on
that uncontrolled channel.

The success envelope is telemetry for an authenticated internal tool. An
analyst disputing an answer needs to know which model and which provider
produced it. The identifier itself is not the leak; putting it on a
problem+json that leaves the building is.

## S2 — ingest decisions taken against the real PDFs

### Caption regex requires the dash

`^Tabela\s+\d+` matches 15 lines in this corpus. Two of them are prose
line-wraps (`Tabela 1. Confirmada a cobertura…`, `Tabela 1 para Danos
Elétricos.`). The detection ladder treats "caption present, no table" as a
failed extraction, so the loose regex fails the build on a correct `find_tables()`
result and the error points at PyMuPDF.

`^Tabela\s+\d+\s*[-–—]\s+\S` matches 13, which is exactly what `lines_strict`
returns. The ladder never fires. It stays in the code anyway: four lines, and
the difference between a silent miss and a named failure if a later document
arrives without ruling lines.

### Exact vector search, no ANN index

Decided now so the S3 migration is not written twice. At ~200 chunks an HNSW
index is slower than a sequential scan. The migration will ship a GIN index on
`tsv` and no index on `embedding`, with a comment naming the row count at which
HNSW starts to pay. Adding it later changes no interface.

`--no-embed` will store NULL, never a zero vector. Cosine on a zero-norm vector
is undefined; pgvector returns NaN, and a NaN in RRF corrupts ranking silently.

### Column blanking by header name

Step 5 of the ingest rule names CPF, phone and e-mail. In `ATA-COM-2025-04`
that leaves `Marta Ferreira Bittencourt` next to `SIN-2025-004512` in the
index — identifying, and matching the claims database row for row. Table
chunks therefore also blank any column whose header matches
`Segurado|Nome|CPF|Telefone|E-?mail`. All thirteen table headers were checked:
exactly one matches (`['Sinistro','Segurado','CPF','Telefone','Status']`).
The rule cannot quietly eat a `Cobertura` column.
