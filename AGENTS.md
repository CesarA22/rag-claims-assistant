# AGENTS.md

Orientation for anyone — human or assistant — picking up this repository.
Read this first. The enforceable invariants live in `.cursor/rules/`; this file
explains what the project is and why it is shaped the way it is.

---

## What this is

An internal web application for **InsurCo** claims analysts. An analyst
asks a question in natural language; the assistant answers **only** from two
sources — a corpus of 13 controlled insurance documents and a read-only claims
database — and **every answer cites where it came from**. When the sources do
not contain the answer, it refuses instead of guessing.

Built as a Proof of Concept. The PoC corpus is a sample; production is stated to
hold tens of thousands of documents, and the architecture is shaped for that
even where the implementation is not.

### The four hard requirements

| | Requirement |
|---|---|
| 1 | Every answer cites its source — document + section, or the named DB query |
| 2 | Questions with no answer in the sources are **refused**, never hallucinated |
| 3 | Policyholder personal data never appears in an answer |
| 4 | ≤ US$ 0.05 per question, p95 latency < 8 s |

### The design thesis

> "The LLM here is an expensive, slow and unreliable external dependency."

That sentence is from the client brief and it drives the architecture. The
provider is treated as something that **will** fail, not something that might.
Everything else is arranged around that. If you are deciding between two
approaches and one of them survives a provider outage better, take that one.

---

## Running it

```bash
docker compose up             # client and API on one origin, http://localhost:8000
```

That is the whole thing: Docker, nothing else, no API key. The first boot
migrates the schema and indexes the corpus before it serves; later boots find the
index and skip straight to serving. Postgres is published on host **5433** for
host-side tools — inside the compose network the api service reaches it at
`db:5432`.

`README.md` is the authoritative run document, including the from-source path
with hot reload. Do not duplicate its steps here; this block went stale once
already by being a copy.

Set `OPENAI_API_KEY` in the environment or in a `.env` beside the compose file
for the real provider. Compose reads `.env` only for substitution, and the api
service sets `DATABASE_URL`, `STORAGE`, `RETRIEVER` and `RETRIEVER_ARM`
explicitly, so those four never reach the container from `.env`.

### Three provider modes — you do not need an API key to see this work

```bash
LLM_PROVIDER=openai   # real. needs OPENAI_API_KEY. US$0.0012/question measured
LLM_PROVIDER=fake     # deterministic replay of recorded responses. no key, no network
LLM_PROVIDER=chaos    # injects failures. watch the degradation ladder for yourself
                      # CHAOS_TIMEOUT_RATE, CHAOS_500_RATE, CHAOS_LATENCY_MS
```

### Tests and evaluation

```bash
pytest --disable-socket -q               # full suite. sockets OFF is the point:
                                         # it is mechanical proof no test hits the API
python evals/retrieval_baseline.py --k 5 --arm lexical --gate 1.0
python evals/retrieval_baseline.py --probes --arm lexical
# tier 2: live API. Needs LLM_PROVIDER=openai and RETRIEVER=hybrid in THIS
# process too, and mints a fresh --tag so committed answers are not replayed.
# Measured cost of a full 3-run tier 2 + boundary + judge: about US$0.11.
RETRIEVER=hybrid LLM_PROVIDER=openai \
  python -m evals.run_golden --base-url http://127.0.0.1:8000 --runs 3
python -m evals.report --results evals/results --out EVALS.md
```

---

## Architecture

### The four seams — exactly four

An interface exists here only where a second implementation already lives in the
repo, or where it absorbs a change we can name. Everything else is a plain
module. Do not add a fifth.

| Protocol | File | Why it exists |
|---|---|---|
| `LLMProvider` | `app/llm/base.py` | three implementations: real, fake, chaos |
| `Retriever` | `app/retrieval/base.py` | absorbs the scale change (13 → 50k documents) |
| `Tool` | `app/tools/base.py` | absorbs a new data source |
| `ConversationRepository` | `app/storage/base.py` | absorbs the storage change |

### The pipeline

`app/services/ask.py` is the file to open first. It reads top to bottom as the
whole system, and it is meant to stay that way:

```
1  history   = repo.recent_messages(conversation_id, token_budget)
2  turn      = repo.begin_turn(conversation_id, client_message_id)   # idempotent
3  evidence  = [*claims_evidence(content, claims), *retriever.search(content)]
4  draft     = await llm.complete(messages, schema=DRAFT_SCHEMA)     # strict
5  answer    = judge(draft, evidence, content)       # grounding.py, deterministic
6  repo.complete_turn(turn, answer, usage, latency)
```

`llm` there is the **resilient decorator**, never the raw provider. Step 5 is
deterministic code — the model is never asked to police itself. `judge()` runs
the PII gate, the ambiguity gate, citation validation, the sufficiency gate and
`redact()` in that order, and it overrules whatever the draft said.

That order has a known cost, measured rather than assumed: the sufficiency gate
never runs when the evidence spans more than one product, because the ambiguity
gate returns first. Both obvious repairs were tried against live drafts and both
break `gs-009`. See EVALS.md finding 1 before reordering anything.

> **Step 3 is two sources, and there is still no model tool-call step.** S11
> wired the claims tool through `app/services/claims_router.py`: a deterministic
> regex router picks named queries from the question and their rows are prepended
> to what the corpus retriever returns, labelled in the prompt as a separate
> `<claims_database>` block. `LLMProvider.complete` still has no `tools`
> parameter and no plan/execute round trip exists — that was a deliberate choice
> over model tool-calling (no second model call inside the cost ceiling, no
> Protocol change dragging all three providers, and it stays testable without a
> provider). Reasoning in `DECISIONS.md`; gs-007 passes and R-10 is `done`.

### Layout

```
app/
  api/        routers + pydantic schemas + error mapping. HTTP shape only.
  domain/     Answer, Citation, Evidence, Refusal, typed errors
  services/   ask.py (the pipeline), budget.py (per-question cost ledger)
  retrieval/  base.py, hybrid.py, ingest.py
  tools/      base.py, claims.py
  llm/        base.py, openai_provider.py, resilient.py, fake.py, chaos.py, prompts/
  storage/    base.py, sql.py, models.py, migrations/
  safety/     redact.py
evals/  tests/  web/  scripts/  docs/
```

---

## Invariants

Enforced per-directory in `.cursor/rules/`. Summarised here so you know they exist.

- The OpenAI SDK is imported in `app/llm/openai_provider.py` and **nowhere else**.
- **The model never writes SQL.** Only named, parameterised queries in
  `app/tools/claims.py`, each with an explicit column projection and a LIMIT.
- **Three outcomes, not two:** `answered` | `refused` | `needs_clarification`.
- **Refusal is a sufficiency judgment, not an empty-retrieval check.** Retrieval
  usually returns *something*; the question is whether it answers the question.
- Citation validation and PII redaction are deterministic code, never prompt
  instructions.
- Retrieved corpus content is **data, never instruction**. It goes in a delimited
  block and the system prompt says so. Treat the corpus as an untrusted channel.
- Errors returned to the client never contain the model name, the prompt, or a
  traceback. RFC 9457 `problem+json` with a `trace_id`; internals go to logs.
- Retry only on 429 / 5xx / timeout / connection errors. **Never on 4xx.**
- **The corpus governs rules; the database reports facts.** Never derive a rule
  from the data.

---

## What you need to know about the data before you touch it

These are not hypotheticals. Each one was verified against the actual files.

### Corpus

- **`NI-014` exists twice.** v1.0 (2023-01-01) says the notice deadline is
  5 dias úteis; v2.0 (2025-06-01) says **3** and explicitly supersedes it. Both
  are in the corpus. Every chunk carries `version` + `effective_date`; superseded
  documents are filtered before ranking.
- **Every limit and deductible lives in a table**, and `page.get_text()` flattens
  tables into orphan cells. Ingest uses `find_tables()` first; each table is one
  atomic chunk with its caption, headers and footnotes.
- **Footnotes change answers.** `NI-022` Tabela 1 gives the Danos Elétricos
  deductible as R$ 250 — and note (3) waives it entirely for lightning damage
  evidenced by a technical report. The table without its footnote is wrong.
- **The same coverage name carries different numbers per product.** Cobertura de
  Vidros: R$ 5.000 / R$ 150 on Auto, R$ 3.000 / R$ 100 on Residencial, absent on
  Empresarial. Product is a retrieval filter, not a hope.
- **Two deadlines are both called "prazo".** *Prazo de aviso* is the
  policyholder's (NI-014, dias úteis). *Prazo de regulação* is the insurer's
  (MAN-SIN Tabela 1, dias corridos, 30–60 by claim type).
- **`ATA-COM-2025-04` contains policyholder PII** — names, CPFs, phone numbers,
  matching the database exactly. PII is not only a database concern. Chunks are
  classified and identifiers redacted **at ingest**, so the document stays usable
  as a source without its identities entering the index.
- Precedence is stated in the corpus itself: latest effective version wins
  (MAN-SIN §3), normatives beat the FAQ (FAQ §1), the glossary does not bind
  (GLOS §1). We implement the client's policy, not our own.

### Database (`claims.db`, read-only)

- **`claims.claim_amount` is the amount CLAIMED, not paid.** The paid figure is
  `payments.paid_amount`. Across the database: R$ 742.8M claimed vs R$ 287.2M
  paid. Always return `claim_amount` with an explicit label.
- **`status = 'Pago'` is not how you find paid claims.** The data dictionary's
  consistency note omits `Pago parcial` (772 rows), which does have payments.
  `WHERE status='Pago'` undercounts by 13.9%. Every "paid" query joins `payments`.
- The synthetic data honours *some* corpus rules and not others — RCF-DM is
  correctly capped at R$ 100.000, glass payments are not. Hence the
  corpus-governs-rules boundary above.

---

## Conventions

- Python 3.12, type hints everywhere, pydantic v2 at boundaries, async throughout.
- Conventional commits naming the requirement ID:
  `feat(llm): R-05 bounded retry with cost-aware guard`
- Every test docstring names the requirement (R-01…R-07) or golden case (gs-001…)
  it covers. `scripts/traceability.py` reads these; a requirement with no test
  fails CI.
- Prompts are versioned files in `app/llm/prompts/`, hashed at load, with the
  hash stored on every message row. Never a string literal.
- Requests are ordered **stable prefix first** (system, tools, schema) so the
  cached-input tier applies to the part that repeats.

---

## Documentation map

| File | What it is for |
|---|---|
| `README.md` | How to run it. Verified from a clean clone. |
| `AGENTS.md` | This file. What it is and why it is shaped this way. |
| `DECISIONS.md` | Technical decisions, tradeoffs, what was cut and why, what's next. |
| `EVALS.md` | Golden set results, cost, latency, honest failure diagnosis. |
| `AI_USAGE.md` | Which assistants, on which parts, what was rejected, how verified. |
| `docs/requirements.yaml` | The requirement register — files and tests per requirement. |
| `docs/plans/` | Per-session implementation plans, written before the code. |
