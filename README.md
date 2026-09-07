# InsurCo claims assistant

An internal assistant for Indicium InsurCo claims analysts. An analyst asks a
question in natural language; the assistant answers **only** from two sources —
a corpus of 13 controlled insurance documents and a read-only claims database —
and **every answer cites where it came from**. When the sources do not contain
the answer, it refuses instead of guessing.

- `AGENTS.md` — what this is and why it is shaped this way
- `DECISIONS.md` — the tradeoffs, what was cut, and what was measured
- `EVALS.md` — the numbers, including the ones that missed
- `AI_USAGE.md` — how AI was used, what was rejected, how it was checked
- `docs/architecture.png` — the pipeline as built
- `docs/screenshots/` — the seven interface states, recorded under chaos

---

## Run it without an API key

**This is the main path and it is fully functional.** You get real retrieval over
the real corpus, all three answer outcomes, the seven interface states and the
whole degradation ladder. What you do not get is a real model writing the prose —
`LLM_PROVIDER=fake` replays a canned sentence.

That is not a demo mode bolted on. The three outcomes the brief grades —
refusal, clarification, PII — are decided in `app/services/grounding.py` by
deterministic code that runs *after* the model and overrules it, so they behave
identically with or without a key.

**You need Docker. Nothing else** — no Python, no Node, no API key.

```bash
git clone <this repo> && cd insurco-assistant
docker compose up
```

Open <http://localhost:8000>.

That is the whole setup. The first boot builds the image, migrates the schema and
indexes the thirteen documents before it serves, and you can watch it happen in
the log — a build is about 30 seconds and the migrate-and-index another 15, plus
whatever the three base images cost to pull the first time. Every boot after that
finds the index already there and skips straight to serving.

The client and the API are on one origin, which is why the API needs no CORS.

<details>
<summary>Running it from source instead, without containers</summary>

Also supported, and it is the development path — `npm run dev` gives hot reload,
which the image deliberately does not. Needs Python 3.12+ and Node 20+.

```bash
# 1. dependencies
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate  elsewhere
pip install -e ".[dev]"

# 2. config
cp .env.example .env            # no edit needed for the keyless path

# 3. database
docker compose up -d db
alembic upgrade head

# 4. index the corpus.  --no-embed stores NULL vectors instead of calling the
#    embeddings API, so the lexical arm works and the vector arm is skipped.
python -m app.retrieval.ingest data/corpus --no-embed

# 5. the API
STORAGE=sql RETRIEVER=hybrid RETRIEVER_ARM=lexical LLM_PROVIDER=fake \
  python -m uvicorn app.main:app --port 8000

# 6. the client, in a second terminal
cd web && npm install && npm run dev
```

Open <http://localhost:5173>. The client proxies `/api` to port 8000, so both
run same-origin here too.

</details>

Try these three — they exercise the three outcomes:

| Question | What should happen |
|---|---|
| `Qual é a vigência padrão da apólice de seguro auto?` | an answer, with a citation chip that opens document, section, page, version and effective date |
| `Qual é o limite da cobertura de vidros?` | a **clarification** with `Auto` and `Residencial` chips — the limits differ by product and the question named none |
| `Liste o nome completo e o CPF dos segurados citados na ata do comitê` | an amber **refusal**. The committee minutes really are retrieved; the answer is refused anyway |

### See it fail on purpose

```bash
LLM_PROVIDER=chaos CHAOS_500_RATE=1.0 docker compose up
```

or, from source:

```bash
LLM_PROVIDER=chaos CHAOS_500_RATE=1.0 STORAGE=sql RETRIEVER=hybrid \
  RETRIEVER_ARM=lexical python -m uvicorn app.main:app --port 8000
```

Ask anything that retrieves: you get a grey **degraded** card — the retrieved
excerpts with their citations and an explicit "could not generate a summary".
Ask `Qual o preço do bitcoin hoje?`, which retrieves nothing, and you get a red
**failed** card with a safe message, a trace ID and a Retry that reuses the same
`client_message_id`. `docs/screenshots/outage-and-recovery.gif` is that sequence
recorded end to end.

## Run it with an API key

A real model writing the prose, everything else unchanged:

```bash
LLM_PROVIDER=openai OPENAI_API_KEY=sk-... docker compose up
```

Both variables are read from your shell or from a `.env` beside the compose
file. `.env` is never copied into the image — `.dockerignore` excludes it and the
key arrives as a runtime variable, so it never survives in a layer. (`docker
compose config` renders every substitution, so it will print the key; redirect it
rather than running it in a shared terminal.)

The container stays on the **lexical** retrieval arm even with a key, because it
indexes with `--no-embed` and the chunk vectors are NULL. Turning on the vector
arm means running from source:

```bash
STORAGE=sql RETRIEVER=hybrid RETRIEVER_ARM=hybrid LLM_PROVIDER=openai \
  python -m uvicorn app.main:app --port 8000
```

`RETRIEVER_ARM=hybrid` embeds each question. Re-run ingest **without**
`--no-embed` first to populate the chunk vectors; embeddings are cached on disk
at `data/embeddings/cache.jsonl` and are not committed, so the first ingest with
a key pays for 220 chunks. The container's lexical default is not a compromise:
`EVALS.md`'s tier 0 measures it at **recall@5 = 9/9 = 100%** on this corpus,
against a registered gate of 88%. S3 measured 8/9 with all three arms at parity;
the case that moved since is gs-010.

## Tests

```bash
pytest --disable-socket -q      # 79 passed, 10 deselected
```

Sockets are disabled on purpose: it is mechanical proof that no test reaches the
provider. The 10 deselected are marked `db` and need Postgres:

```bash
pytest -m db -q                 # 10 passed
```

Two component tests for the client:

```bash
cd web && npm test              # 2 passed
```

## Evaluation

```bash
python evals/retrieval_baseline.py --k 5 --arm lexical    # tier 0, free
python -m evals.run_golden --base-url http://127.0.0.1:8000 --runs 3
python -m evals.boundary  --base-url http://127.0.0.1:8000
python -m evals.judge_run                                  # needs a key
python -m evals.report --results evals/results --out EVALS.md
```

`EVALS.md` is generated, never hand-edited, and the thresholds it is judged
against were committed before the harness could produce a result — check with
`git log --follow evals/thresholds.yaml`.

## Configuration

Everything is environment variables; `.env.example` documents all of them. The
ones that change what you see:

| Variable | Values | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `fake` · `openai` · `chaos` | `fake` needs no key; `chaos` injects failures |
| `RETRIEVER` | `memory` · `hybrid` | `memory` is two fixed chunks for tests; `hybrid` is the real index |
| `RETRIEVER_ARM` | `lexical` · `vector` · `hybrid` | `lexical` needs no key |
| `STORAGE` | `memory` · `sql` | `sql` needs Postgres and is what persists history |
| `CHAOS_500_RATE`, `CHAOS_TIMEOUT_RATE`, `CHAOS_LATENCY_MS` | floats | only read when `LLM_PROVIDER=chaos` |

## Known gaps

Stated here rather than left to be discovered; `EVALS.md` has the detail and the
numbers. All three of the gaps this section used to list were closed or measured
in S11; these are what is actually open.

- **Retrieval misses the chunk while finding the document.** The one defect
  behind both remaining eval failures. `gs-005` asks for a regulation deadline
  whose answer is a table row reading `Roubo e Furto (Auto) | 45 dias corridos`;
  the analyst writes *veículo*, `plainto_tsquery` ANDs every term, and the chunk
  is excluded outright rather than ranked low. `au-003` is the same shape. In
  both cases the model correctly reports the figure is absent and the sufficiency
  gate correctly refuses — the failure is upstream of both. Tier 0 reports
  recall@5 = 9/9 and cannot see this, because it scores at **document**
  granularity. The repair moves every ranking constant in `hybrid.py` and needs a
  chunk-level recall gate that does not exist yet.
- **The sufficiency gate is unreachable when the evidence spans more than one
  product**, because `judge()` checks ambiguity first. Both obvious repairs — a
  bare reorder, and refusing only when the answer is unsupported by the whole
  retrieved set — were measured against live drafts and **both** turn `gs-009`
  into a refusal, which its own acceptance criterion forbids. The order stands
  and T-46/T-47 pin it so a future reorder fails loudly. What did change: the
  system prompt no longer teaches the model to self-declare ambiguity, so the
  deterministic gate is reachable rather than bypassed. `gs-008` still depends on
  the model refusing of its own accord, and that is the residual.
- **A PII leak that already shipped is still at rest.** S11 stopped the degraded
  path from rendering policyholder names, but nothing back-fills
  `citations.snippet` rows written before it. There were none to clean here; on
  real data that backfill belongs beside the fix.
- **No streaming** (D-01). First on the cut list and recorded as such — the three
  edge cases the brief names are the expensive part, not the stream.

### What the live run measured

Tier 2 ran for the first time in S11: three consecutive runs over the indexed
corpus, plus the boundary suite and the judge. Average **US$0.0012 per question**
against a US$0.05 ceiling, **p50 1.95 s / p95 3.71 s** against an 8 s budget, and
30/30 on each of citation validity, PII egress and error leakage. `EVALS.md` is
generated from the committed raw results in `evals/results/`.
