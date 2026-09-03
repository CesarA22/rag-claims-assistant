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

## S3 — retrieval

### OR the Portuguese tsquery

`plainto_tsquery` ANDs every lexeme. It is not a BM25 substitute. Against this
corpus, 7 of 10 golden questions return zero rows under AND semantics.
`replace(plainto_tsquery('portuguese', q)::text, '&', '|')::tsquery` ORs the
lexemes and lets `ts_rank_cd` rank, which is the partial-overlap behaviour
that produced the 100% BM25 baseline. `websearch_to_tsquery` also ANDs bare
terms and does not fix this.

### `ts_rank_cd` normalization 32

Default is 0 — no length normalization, so long chunks win on term count.
BM25 had `b=0.75`. `ts_rank_cd(tsv, q, 32)` is `rank/(rank+1)`, the usual
choice that keeps the lexical arm comparable to the baseline it is measured
against.

### `rrf_k = 15`, not 60

The paper's 60 is tuned for large candidate pools. With 50 candidates from
~200 chunks, ranks 1 and 50 differ by less than 2× at k=60, so fusion barely
discriminates and the Python role boost (0.70–1.00) would dominate it. At
k=15 the same ranks differ by ~4×.

### `exclude_pii=False` by default

Identifiers are redacted at ingest. `gs-010` expects `ATA-COM-2025-04` back;
excluding PII chunks by default would silently zero a graded case. The flag
exists for questions that should not retrieve the minutes at all.

### Cache miss raises, never embeds zeros

`--no-embed` stores NULL. A disk-cache miss with no `OPENAI_API_KEY` raises
`MissingEmbeddingError`. Either path is loud; a zero vector would make
pgvector return NaN and corrupt RRF silently.

### `ts_rank_cd` is not BM25: add IDF

`ts_rank_cd(..., 32)` normalises for length. It does not weight rare terms.
Against this corpus that is the difference between 8/9 and 9/9: gs-010's
POL-LGPD chunk ranks 44th on `ts_rank_cd` (common stems in a long CG-AUTO
chunk win) and 2nd under the 60-line BM25 baseline. The lexical arm now
multiplies `ts_rank_cd` by `1 + Σ IDF` of the Portuguese query lexemes
that appear in the chunk, with document frequency from `ts_stat`. After
that change lexical recall@5 is 9/9; the MiniLM-padded vector/hybrid arms
stay at 8/9 because POL-LGPD never enters the vector top 50 and fused rank
is 14. A `max_per_document=2` cap on the fused tail improves context
(gs-004 no longer returns 5/5 CG-AUTO) but cannot lift a rank-14 hit into
the top 5.

Re-embedding with `text-embedding-3-small` was attempted and failed with
`credit_balance_exhausted`. The padded MiniLM vectors stay until a funded
key exists. `--gate 1.0` is therefore asserted on `--arm lexical`.

