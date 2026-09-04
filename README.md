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

**You need:** Docker, Python 3.12+, and Node 20+ (only for the web client).

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
run same-origin and the API needs no CORS.

Try these three — they exercise the three outcomes:

| Question | What should happen |
|---|---|
| `Qual é a vigência padrão da apólice de seguro auto?` | an answer, with a citation chip that opens document, section, page, version and effective date |
| `Qual é o limite da cobertura de vidros?` | a **clarification** with `Auto` and `Residencial` chips — the limits differ by product and the question named none |
| `Liste o nome completo e o CPF dos segurados citados na ata do comitê` | an amber **refusal**. The committee minutes really are retrieved; the answer is refused anyway |

### See it fail on purpose

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

```bash
# .env
OPENAI_API_KEY=sk-...
```

```bash
STORAGE=sql RETRIEVER=hybrid RETRIEVER_ARM=hybrid LLM_PROVIDER=openai \
  python -m uvicorn app.main:app --port 8000
```

`RETRIEVER_ARM=hybrid` turns on the vector arm, which embeds each question.
Re-run ingest **without** `--no-embed` first to populate the chunk vectors;
embeddings are cached on disk at `data/embeddings/cache.jsonl` and are not
committed, so the first ingest with a key pays for 220 chunks.

## Tests

```bash
pytest --disable-socket -q      # 76 passed, 10 deselected
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

Stated here rather than left to be discovered; `EVALS.md` has the detail.

- **The claims database is not reachable from the product.** The tool is built
  and tested (`app/tools/claims.py`, T-20…T-26) but nothing calls it —
  `LLMProvider.complete` has no `tools` parameter. Golden case gs-007 fails
  because of this, and R-10 is `partial` in the register.
- **The sufficiency gate is unreachable for questions whose evidence spans more
  than one product**, because `judge()` checks ambiguity first. gs-008 returns a
  clarification where it should refuse.
- **Tier 2 has not been run**: the key on the development machine ran out of
  credits. Everything that does not need a model is measured in `EVALS.md`.
