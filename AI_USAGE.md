# AI_USAGE.md

Declared use of AI assistants on this challenge, as required by §5 of the brief.

**Tools used:** Cursor (Claude Sonnet / GPT-5.4) for implementation; Claude
(Cowork) for corpus and database analysis and for planning.

**How to read this:** the log is kept live, entry by entry, as the work happens.
The last column — *How verified* — is the one that matters. Using an assistant is
not the claim being made here; not trusting its output is.

---

## The rule I worked to

**Never accept a diff I could not defend in review.** If I could not explain why a
line was there, I either understood it or deleted it. The parts where a wrong
decision is invisible until production — the resilience policy, the data model,
the tool boundary — were specified by me and reviewed line by line. Scaffolding,
adapters, test bodies from cases I specified, and front-end state rendering were
generated and reviewed.

---

## Log

| # | Day | Task | Accepted | Rejected, and why | How verified |
|---|-----|------|----------|-------------------|--------------|
| 1 |2026-09-01| S1: POST /conversations/{id}/messages through ask.py, four Protocols, fakes only | The slice as shipped: four Protocols; no tools on complete until a Tool exists; product on Retriever.search; cached_prompt_tokens on meta; in-memory retriever with real CG-AUTO-2024 §2.1 / NI-014 v2.0 strings; citation validation in code not in the prompt; LLM_PROVIDER=fake as the default path | First plan left in-flight idempotency unspecified (replay-if-completed only). Retrofit on a unique constraint is painful; in-flight duplicate returns outcome: pending, not 409. Same pass: failed was missing from the turn enum (POST is problem+json, GET still has to render a failed turn); requires-python was pinned <3.13 (evaluator on 3.14 hits a wall) — now >=3.12 with no upper bound. Also deferred redact.py out of S1 when offered | pytest --disable-socket -q — 7 passed (T-01, T-02, T-03, T-10, T-11, T-13, T-14). Live POST of gs-001 returned answered citing cg-auto-2024#2.1 / CG-AUTO-2024 v3.2. Read ask.py top to bottom against the pipeline in the plan |
| 2 |2026-09-02| S2 — ingest: steps 1–5 of 15-retrieval.mdc over the 13 PDFs, --dry-run only | ingest.py in the six-step order; dash-anchored caption `^Tabela\s+\d+\s*[-–—]\s+\S`; find_tables() before prose; one Markdown chunk per table with caption, headers, cell-matched footnotes; digit-normalised boilerplate counted on distinct pages (so `Página 3 de 15` strips and `R$ 300.000,00` does not); shared redact.py (CPF check-digits, phone, e-mail) plus Segurado-column blanking by header; NI-014 supersession from v2 Histórico `Superada`, not from v1 calling itself Vigente; R-08 active, R-03 still planned | First plan gated on 14 tables with `^Tabela\s+\d+`. That regex matches two prose wraps (MAN-SIN p11, NI-022 p2), so the caption-without-table ladder would have failed a correct extraction and blamed PyMuPDF. Gate is 13; lines_strict hits 13; ladder never fires. Same pass: `--no-embed` writing zero vectors (cosine of a zero-norm vector is NaN; pgvector would poison RRF silently — store NULL, skip the vector arm). Scope was S2 plus half of S3; split at the IO boundary. HNSW dropped — at ~200 chunks exact search is cheaper and the interface does not move. Also missed gs-004 / gs-009 as ingest assertions (both RCF rows in one CG-AUTO chunk; Vidros 5.000 and 3.000 independently retrievable). | I counted captions and find_tables() on the real PDFs before any ingest code existed: loose regex 15, dash-anchored 13, find_tables() 13. After: `python -m app.retrieval.ingest data/corpus --dry-run` — 13 docs, 13 tables, 1 footnote (NI-022 note 3 only), 1 PII chunk (ATA), NI-014 v1 YES / v2 no, ladder=never. Read the NI-022 table chunk: `Danos Elétricos (3)` and `R$ 250,00` on the same row, note (3) attached. ATA indexed text has no names, CPFs or phones. CG-AUTO table has 100.000 and 150.000; CG-RES has 3.000. pytest --disable-socket -q — 14 passed (T-15 × 7). |
| 3 |2026-09-02| S3 — hybrid retrieval: pgvector cosine + Portuguese tsvector, RRF, filters, three-arm tier-0 eval | HybridRetriever as one gated SQL statement (lexical / vector / hybrid); OR'd tsquery `replace(plainto_tsquery(...)::text, '&', '|')`; `ts_rank_cd(..., 32)`; `rrf_k=15`; doc_role boost in Python (normative > minutes > pointer > glossary); `exclude_pii=False` by default (gs-010 needs the ATA back); `arm` as constructor config so the Retriever protocol stays clean; disk cache keyed `sha256(model + "\\n" + text)`, cache miss with no key raises instead of embedding zeros; NULL not a zero vector; no HNSW; BM25 baseline left runnable at `handoff/evals/retrieval_baseline.py`; T-19 `@pytest.mark.db` with NI-014 v1.0 excluded unless `include_superseded=True` | First plan used `plainto_tsquery` as if it were BM25. It ANDs every lexeme. I measured it against the 220 chunks before the retriever existed: 7/10 golden questions return zero rows (gs-001 AND=0, gs-002 AND=0, …). OR lands gs-001 on CG-AUTO 2.1 Vigência and gs-002 on MAN-SIN 6.2. Shipping AND would have read lexical recall at ~30%, I would have concluded vectors are load-bearing, and that inverted finding would have gone into EVALS.md. Same pass: skip-on-unreachable-DATABASE_URL under `--disable-socket` (asyncpg raises `SocketBlockedError`, not OSError; Docker up + sockets blocked fails the test rather than skips — marker `-m not db` instead); `rrf_k=60` (paper default; at 50 candidates from 220 chunks ranks 1 and 50 differ by <2×, so the 0.70–1.00 role boost would dominate fusion — k=15); version trap left as a probe to eyeball rather than a T-19 assertion | AND vs OR counts on the live index: 7/10 golden questions AND=0, OR recovers them, tsv never null. `python evals/retrieval_baseline.py --k 5 --arm all` — recall@5 lexical 8/9, vector 8/9, hybrid 8/9, shared miss gs-010 (POL-LGPD not in top 5; ATA appears on vector/hybrid only). `--probes`: lexical never reaches NI-014 in top 4; vector puts NI-014 v2.0 §3 at rank 1; hybrid has it at rank 4. T-19: `include_superseded=False` returns zero NI-014 v1.0 chunks, `True` returns them. `pytest --disable-socket -q` — 20 passed, 1 deselected (`@pytest.mark.db`). |

<!--
Add a row when something actually happens. Keep it in a split pane.

Guidance for filling this in honestly:

- "Accepted" is what you kept, specifically. Not "helped with retrieval" —
  "the RRF fusion function and the tsvector query builder".

- "Rejected, and why" needs at least one real entry, per the brief. It must be a
  genuine judgment call, not a typo you caught. The reason is the substance.

- "How verified" is the column nobody else will have. Be concrete:
  "ran it against 3 known tables and diffed the chunk boundaries by hand",
  "T-07 asserts zero retries on 400", "queried the DB directly and compared",
  "read every SELECT projection against data_dictionary.md".

- Include at least one entry where the assistant was right and you were wrong.
  It makes the rest of the document credible.

- Do not write this on day five. It reads like it was written on day five.
-->

---

## Rejected suggestions

> The brief asks for at least one example of a suggestion I rejected or
> corrected, with the reason. Recorded here as they happen.

### 1.
**Suggested:**
**Rejected because:**
**How I caught it:**

<!--
Candidates you are likely to actually hit on this build — use the real one that
happens to you, not these:

- A free-form SQL tool, or a "just this once" dynamic query builder.
  Reason: injection surface, unbounded scans against an 8 s latency budget, and a
  generated query is not a citable source.

- A blanket retry-on-Exception wrapper.
  Reason: retrying a 4xx spends latency and budget on a guaranteed second
  failure. Retry is only correct for 429 / 5xx / timeout / connection errors.

- A generic repository/service base-class layer, or a provider registry.
  Reason: the brief penalises abstraction disproportionate to the problem as
  hard as a monolith. Four protocols, each absorbing a named change.

- A fixed-size character splitter for chunking.
  Reason: every limit in this corpus lives in a table; a character splitter cuts
  tables in half and produces confidently wrong numbers.

- Refusal implemented as "retrieval returned nothing".
  Reason: for the real refusal case retrieval returns five confident, topically
  adjacent chunks. Refusal has to be a sufficiency judgment.

- Interpolating the SDK exception into the error response body.
  Reason: those strings routinely carry the model name and sometimes the prompt.
-->

---

## Where AI was *not* used

Recorded because the boundary is part of the answer:

- The resilience policy — retry classification, budget arithmetic, breaker
  thresholds, the degradation ladder — was specified by me before any code was
  generated. The numbers come from the stated 8 s p95 and US$ 0.05 budget, not
  from convention.
- The data model and the tool boundary, for the same reason.
- The corpus and database findings recorded in `DECISIONS.md` were surfaced with
  assistance but **independently confirmed against the source files** before
  being written down or acted on. Every figure quoted in that document was
  reproduced by a query or a `grep` I ran myself.
