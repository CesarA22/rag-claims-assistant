# AI_USAGE.md

Declared use of AI assistants on this challenge, as required by §5 of the brief.

**Tools used:** Cursor (Claude Sonnet / GPT-5.4) for implementation; Claude
(Cowork) for corpus and database analysis and for planning; Claude Code
(Opus 5) for S8 — the front end, the API changes it forced, and driving the
browser for the chaos run.

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
| 4 |2026-09-02| S4 — claims tool: six named queries over `claims.db`, PII excluded at the query layer, gs-007 from the database alone | `QUERIES` registry of six static SQL strings, named parameters, hard LIMITs, `sqlite3.connect(..., uri=True)` read-only; no query joins `policyholders`; `claim_amount` labelled ("valor reivindicado pelo segurado, antes da regulação"); paid-ness is `EXISTS` on `payments` plus `payment_type: Integral \| Parcial`, never a Pago status; unpaid `get_claim_payment` is a LEFT JOIN that carries `claims.status`; `effective_date` is `MAX(occurrence_date, payment_date)` = 2026-02-11, computed once, not file mtime; T-04 and T-20..T-26; R-10 plus D-01..D-05 in the register; status vocabulary `done \| partial \| pending \| cut` re-derived from the tree | File mtime as `effective_date` — a clone, `COPY`, or `touch` would cite checkout day, not data freshness. A `count_claims` that rejected `'Pago'` but never asserted 713 — the trap closed, the fix untested. Restricting `status` to Aberto / Em regulação / Negado without `payment_type` made "pagos parcialmente?" unanswerable; the mapping onto Integral/Parcial is 1:1 (4597 / 772), so the capability comes back as a payments fact. Bare "nenhum pagamento" on a claim in Em regulação reads as a data gap. The gs-007 script describing the Vidros contradiction in prose instead of calling `claims_summary(product="Auto", claim_type="Vidros")`. Docstring `never status='Pago'` — the sentence documenting the invariant was the one hit `rg` returned. Translating `active`→`done` on R-03; S2 gave it code, the output scrubber and T-18 are still missing, so the honest value is `partial` | Queried the DB before any SQL was written: payments 5369/5369 1:1; max claims/policy 9; `status×payment_type` has exactly two pairs; unpaid statuses are only Aberto/Em regulação/Negado; coverage UNION is 2026-02-11 (max occurrence is 2025-12-17). T-23: `count_claims(claim_type="Incêndio", paid=True) == 713` not 614. T-24: `payment_type="Parcial" == 772`. T-25: SIN-2025-004512 claimed R$ 120.000,00 labelled, payment R$ 100.000,00; unpaid SIN-2023-005055 renders Em regulação. T-04: holder CPF `111.111.111-11` read from `policyholders` is absent from the tool output. T-26: section is the citation call string, `effective_date == date(2026, 2, 11)`. `python -m scripts.gs007_from_db` — Auto Vidros max R$ 85.869,12 against CG-AUTO R$ 5.000,00. `rg -n "status.*=.*'Pago'" app/` returns nothing. `pytest --disable-socket -q` — 33 passed |
| 5 |2026-09-03| S5 — resilient decorator, RFC 9457 errors, chaos provider | `ResilientProvider` holding the request budget, selective retry, single-flight half-open breaker, `max_output_tokens=800` next to the other ceilings; `QuestionBudget` on a ContextVar, mutated in place, estimate at chars/3; `ask.py` keys excerpts on evidence, not on which failure (`provider_degraded` and `circuit_open` both 200 with citations when retrieval returned something); `GET /healthz`; OpenAI adapter with `max_retries=0` and `reasoning.effort=none` sent explicitly; `ChaosProvider` one uniform draw so the two rates compose | First plan made rung 3 a 503. The discriminator is whether we hold citable evidence, not whether we paid — 503 with retrieved passages throws away the demo. Same pass: chars/4 on the cost gate (a central estimate on a ceiling is wrong in the only direction that matters); `remaining > 0` as the retry check (0.4 s left still starts a ~2 s call and spends the latency budget to arrive at the same degraded answer); no `max_output_tokens` (uncapped 128k is US$0.58 from one call, eleven times the question budget, and the ledger only gates retries); no `/healthz`; half-open without single-flight (every waiter probes at `reset_s`); output price invented at 3.00 before the pricing page (it is 4.50, and reasoning tokens bill there). Kept `max_retries=0` — the SDK’s default of 2 would make “max 2 retries” six calls | `pytest --disable-socket -q` — 50 passed (T-05, T-06, T-07, T-08, T-09, T-10, T-28, T-29, T-30, T-31, T-32, T-33, T-34). T-07: `InvalidRequest`, one inner call, breaker still closed. T-31: twenty concurrent `complete()` at the reset window, inner saw exactly one. T-34: captured request body has `max_output_tokens=800` and `reasoning.effort=none`. Chaos recording `CHAOS_500_RATE=1` `LLM_BREAKER_FAILURES=2`: POST 1 → 200 `reason=provider_degraded` citations=2; `/healthz` `state=open`; POST 2 → 200 `reason=circuit_open`, inner not called, still cited |
| 6 |2026-09-03| S6 — T-01..T-18 audited; T-16/T-17/T-18 written red, measured, then pinned | Fourteen of T-01..T-18 already existed and passed, so the session audited them (41/42 test docstrings carry `T-nn / R-nn`; the exception is the register meta-test, which has no requirement) rather than rewriting them; T-12 recorded as absorbed by T-22 instead of written as a duplicate; the one permitted `respx` test is T-28 and no second one was added; `tests/test_grounding.py` holds T-16/T-17/T-18 plus two T-17 discriminators; `xfail(strict=True)` over a red `master`; **S6b** created to own the five pieces of work the markers owe, because S7 is persistence and the history surface | Scripting the fake to *refuse* and asserting `outcome == "refused"` — that measures only that the fake returns what it was handed, and would retire R-02 without closing it. All three script the fake to return the confident, fluent, correctly-cited **wrong** answer and assert the pipeline overrides it. Same pass: `assert "5" not in answer` in T-16 (the first legitimate refusal saying "consultei 5 trechos" fails a test that should pass, and the next person deletes the assertion rather than the digit — assert the leak, `\d+([.,]\d+)?\s*%`, not the digit); a lone "names the product" discriminator for T-17 (the cheapest gate counts `len({e.product}) > 1` and trips on a mixed `All` + `Auto` set, so an all-`product="All"` case was added); pointing the three markers at S7, which has no room for them; and committing the suite red, which would have disarmed the tier-0 retrieval gate running beside it | Baseline first: `a1c75ee`, `pytest --disable-socket -q` — 50 passed, 1 deselected. Ran all three unmarked against unmodified `app/` — 3 failed, 2 passed (the discriminators, as predicted). Measured every assertion independently, since a test stops at the first failure: T-16 `answered` not `refused`, `%` matched `5%`; T-17 `answered` not `needs_clarification`; T-18 CPF present in **both** `answer` and `citations[0].snippet`, 2 forbidden names in the serialised result, `answered` not `refused`. Then pinned. Final: **52 passed, 1 deselected, 3 xfailed**, exit 0 — and the 50 originals unchanged. Verified `strict` is armed rather than assuming it: a throwaway `xfail(strict=True)` test that passes reports `[XPASS(strict)]` and fails the run. `python -m scripts.traceability` — 15 requirements, register ok. `git diff --stat app/` — empty |
| 7 |2026-09-03| S7a + S6b + S7b/c — Alembic schema, the three grounding gates, SQLAlchemy repository, history endpoint | Ran the planned order S7a → S6b → S7b/c rather than S7 straight through: S7a creates tables and persists nothing, so landing S6b between them is what stops `messages.answer_text` and `citations.snippet` accumulating unscrubbed corpus PII. Alembic owns the whole schema (001_chunks.sql subsumed and deleted); `grounding.py` holds the PII, ambiguity and sufficiency gates as deterministic code; `prompt_fingerprint()` replaces prompt text in logs with a hash plus evidence ids; `SqlConversationRepository` moves idempotency to `messages_client_idem` with ON CONFLICT DO NOTHING RETURNING; `GET /conversations/{id}/messages` renders all five statuses; `/healthz` gains a database block. `ask.py` got exactly one edit — `PROMPT_VERSION` | `autoincrement=True` on `messages.seq` — SQLAlchemy ignores it on a non-primary-key column, so the column would have been NOT NULL with no default and every insert in S7b would have failed; caught by compiling the DDL rather than trusting the model, fixed with an explicit `Identity()`. An FK from `citations.chunk_id` to `chunks.id`: it would block `TRUNCATE chunks`, which is how a re-chunk after a parser change has to be done, and it contradicts the point-in-time semantics that justified denormalising document_code and version in the first place. The S7d consolidation commit and the "fewer than 25 tests" gate — a runbook proxy for "do not pad", retired in favour of the enforced T-nn / R-nn mapping that traceability.py already checks. Scheduling the documents/chunks column duplication for removal in S8, which owes a graded deliverable; recorded as a kept tradeoff instead. An all-or-nothing sufficiency rule — T-01's own answer fails it | Baseline `a1c75ee`: 50 passed, 1 deselected. After: **74 passed, 8 deselected**, and the three xfail markers came off because `strict=True` would have failed CI once the gates landed. `alembic upgrade head --sql` renders the full DDL with no connection — `tsv` keeps GENERATED ALWAYS AS ... STORED, `embedding` is VECTOR(1536), `chunk_id TEXT` has no FK. T-40 asserts the migration matches models.py column-for-column. `ingest --dry-run` unchanged at 13 documents / 220 chunks. `git diff --stat app/services/ask.py` is the PROMPT_VERSION edit only. Then against a live Postgres: `alembic upgrade head` initially FAILED with `relation "chunks" already exists` — the volume still held the table S3 provisioned via 001_chunks.sql with 220 paid-for embeddings, which offline rendering could never have caught. Fixed by making revision 0001 adoptive (inspect first; create on a fresh clone, add only `document_id` on an existing one) rather than dropping and re-embedding. After: 220 chunks / 220 embeddings intact, `pytest -m db` 8 passed (7 contract tests on the SQL repo, plus T-19 over the adopted table), duplicate POST returns the same message_id with `provider calls=1` and `count(*)=1` per client_message_id, GET history renders answered/refused/failed in order, the NI-014 v1.0 citation query returns the row in one SELECT, and `/healthz` reports `storage=sql database.reachable=true` |
| 8 |2026-09-04| S8 — the React client: seven message states, two component tests, and the six holes in the API that building it exposed | Five components plus `state.ts`, which is where the only real logic lives — the API speaks three shapes and they disagree with each other (`degraded` sits under `meta` on POST and at the top level in history; history has no trace id at all), so the reconciliation is a plain module with no React in it. Every card carries a text badge naming its own state; the gate is settled by screenshots and screenshots get read in greyscale, so colour is the second signal and never the only one. `Sem base nas fontes` instead of `Recusado` — "rejected" is the exact misreading the amber rule exists to prevent. On the server: a `failed` turn re-opens in `begin_turn` rather than replaying; `client_message_id` and a derived `detail` on `HistoryMessageOut`; `RETRIEVER=memory\|hybrid` with the asyncpg pool opened in a startup hook, since `create_pool` is async and `create_app` is not; `clarification_options` built from `spanned_products`; `page_from`/`page_to` carried through to `CitationOut`. Vite `/api` proxy instead of CORS middleware. T-41 and T-42 | The plan came back with gate 3 missing outright — the 30-second outage recording. Not in the gates table, not in the runbook, and not in the out-of-scope list either, which is the part that made it a defect rather than a note: the plan's own "anything not on the file table is out of scope" rule would have pushed it out if anyone hit it mid-session. Same review: `HistoryMessageOut` never returned `client_message_id`. `_turn()` reads it off the row and the API boundary throws it away, so the whole re-open fix survives exactly as long as the tab stays open — reload the page and the client has to mint a fresh key, which is a second row and a second paid provider call on the most ordinary action there is. Three lines. Also cut the compose `app`/`web` services, which had been booked as a single row in the file table: there is no Dockerfile in this repo and nothing serves static files, so "complete application in compose" is two Dockerfiles plus a service, on the session that owes the graded interface — and D-03 is a differential, which is the category that goes first. And T-41/T-42 were filed under R-01, which is "every answer cites its sources"; they belong to R-02 and D-02. `traceability.py` only checks the register's shape, so a wrong mapping lands silently. Earlier, during planning: a `CHAOS_INNER` env var so chaos could wrap the real provider, on the theory that `complete` was unreachable behind a fake model. Measured instead — `"Qual é a vigência padrão da apólice de seguro auto?"` retrieves CG-AUTO §2.1 and the fake's canned sentence scores 1.00 on the sufficiency gate against that chunk — so the knob died before the plan was written. A `trace_id` column went the same way: one column, one migration, and the trace id matters at the moment of failure, which the live path already carries | `pytest --disable-socket -q` — **76 passed, 10 deselected**, up from 74/8; the two new cases are both on T-35 (a failed turn re-opens and keeps its row while a completed one still replays; a persisted citation reads back with null pages instead of raising, which is what makes the nullable type load-bearing rather than cosmetic — `sql.py` rebuilds `Citation` field by field and a required `int` would 500 every history read). `pytest -m db` — 10 passed. `npm test` — 2 passed. Checked T-42 has teeth by mutation rather than trusting it: point `Chat`'s retry at `crypto.randomUUID()` and it fails with the two uuids side by side; revert and it is green. Then the whole thing against the live stack — `STORAGE=sql RETRIEVER=hybrid RETRIEVER_ARM=lexical LLM_PROVIDER=chaos`, 220 chunks, client on the proxy. gs-009: `outcome=needs_clarification`, `clarification_options=['Auto', 'Residencial']`, no unqualified number. Retry: `b-2` failed with `provider_degraded` trace `b91febe9`, retried while still down and came back `circuit_open` trace `d4c589ce` — different code, different trace, so it is a real attempt and not a replay; after recovery the same turn resolved on the same `message_id`, and `count(*) GROUP BY client_message_id` has no id above 1 for the whole conversation. Seven screenshots plus the recording in `docs/screenshots/`. Four things only the browser found: the clarification prose named all three products next to chips offering two (`_CLARIFY` is now `_clarify(products)` off the same `spanned_products` call); nothing loaded history on mount, so `fromHistory`/`mergeHistory` were dead code and returning `client_message_id` bought nothing; the `/healthz` poll paused whenever the tab lost focus, so the banner went stale exactly when you walk away and come back; and T-42's own GET stub returned `{}` instead of a `HistoryOut`, which the mount effect exposed. One plan claim the clock corrected — a client abort does **not** orphan the turn. Abort the socket 1 s into a 20 s call and it reads `pending` at 1.5 s, `refused` at 5.5 s: Starlette never cancelled the handler, it just finished. So the reaper is only needed for a worker that dies mid-turn, and `S8.md` is annotated in place rather than quietly rewritten |
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

### 1. A Retry button that works until you refresh the page

**Suggested:** the S8 plan opened by finding that Retry was inert — `begin_turn`
replayed any turn that was not `pending`, so retrying a failure returned the
recorded failure at 200 and never called the provider — and fixed it by making a
`failed` turn re-openable. Good finding, right fix, and the plan then moved on.

**Rejected because:** it only holds while the tab stays open. `HistoryMessageOut`
does not return `client_message_id`, so after a reload the client has no key to
re-post with and has to mint one. That is a second row and a second paid provider
call, which is the exact duplication D-02 exists to prevent — and it happens on a
page refresh, the most ordinary thing anyone does. Nasty because the first Retry,
in the session that produced the failure, works perfectly. You would never catch
it by hand.

**How I caught it:** read the plan's own `state.ts` sketch and noticed
`clientMessageId: string` was non-optional while `fromHistory(msg)` had no such
field to read from — so TypeScript would force an invention there. Then checked
the API: `Turn.client_message_id` exists (`app/domain/models.py:53`), `sql.py`'s
`_turn()` populates it, and `grep client_message_id app/api/routes.py` returns
one hit, in the POST handler. Loaded from the row, dropped at the boundary.

### 2. Scope creep booked as a one-line file-table entry

**Suggested:** the same plan listed the `app` and `web` services for
`docker-compose.yml` as a single row in its Files table, and decision 3 leaned on
it — the argument for skipping CORS was partly "in compose the built client is
served same-origin".

**Rejected because:** nothing in this repository serves static files and there is
no Dockerfile anywhere, so that row is really two Dockerfiles, a service, and a
seventh backend concern — on the session that owes the mandatory interface
requirement. It also made the CORS argument lean on a deployment S8 never
creates. D-03 is a differential, and the brief is explicit that differentials do
not compensate for missing mandatory requirements, so it is first out. Cut it,
kept the Vite proxy, and struck the sentence rather than quietly rewording it.

**How I caught it:** `find . -iname "Dockerfile*"` and
`grep -rn "StaticFiles\|app.mount" app/` both come back empty.

### 3. A gate that fell through the plan's own safety net

**Suggested:** a plan that omitted the 30-second outage recording entirely.

**Rejected because:** missing it from the gates table is an oversight; missing it
from "out of scope — stated so it is not mistaken for an omission" is a defect,
because that section is the plan's designated home for deferrals and its closing
rule ("anything not on the file table is out of scope") would have actively
pushed the recording out if anyone hit the question mid-session. The plan had
already written the shot list without noticing — its Pass C is
`CHAOS_500_RATE=1.0` → Retry → `CHAOS_500_RATE=0` → same turn resolves, which it
called "the strongest single frame of the session". The argument for doing it in
S8 rather than S9 is that chaos, the proxy, Postgres and an ingested corpus are
all standing at that moment and at no other.

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
