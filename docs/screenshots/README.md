# S8 evidence — the seven states, captured under chaos

Taken against the running stack, not mocked:
`STORAGE=sql RETRIEVER=hybrid RETRIEVER_ARM=lexical LLM_PROVIDER=chaos`,
Postgres with the 220-chunk corpus, client on the Vite `/api` proxy.

| File | State | How it was reached |
|---|---|---|
| `01-idle-after-cancel.png` | **idle** | Cancel during a 20 s `CHAOS_LATENCY_MS` window. The card says the server keeps working, and offers *Atualizar* — not Retry |
| `02-sending.jpg` | **sending** | `CHAOS_LATENCY_MS=4000`; skeleton, live counter at 1.1 s, Cancel |
| `03-complete-citation-panel.png` | **complete** | `Qual é a vigência padrão da apólice de seguro auto?` with the citation chip open — snippet, documento, seção, **página p. 2**, versão, vigente desde |
| `04-refused-unsupported.png` | **refused** | the sufficiency gate: evidence on topic that does not answer |
| `04b-refused-pii-gs010.png` | **refused** (gs-010) | the PII gate. No CPF, no names, cites POL-LGPD-2024 |
| `05-needs-clarification-gs009.png` | **needs_clarification** (gs-009) | two chips, `Auto` and `Residencial`, and no unqualified number |
| `06-degraded-and-failed.jpg` | **degraded** + **failed** | `CHAOS_500_RATE=1.0`. Degraded keeps its five citations; failed is the off-corpus question, which retrieves nothing |
| `07-failed.png` | **failed** | safe message verbatim, trace id, *Tentar novamente* |
| `08-connection-banner.png` | banner | breaker open, `/healthz` degraded — visible before anything is typed |
| `09-four-states-together.jpg` | four at once | the pairwise-confusion check, in one frame |
| `outage-and-recovery.gif` | the ladder | one continuous take, below |

## outage-and-recovery.gif

Healthy answer → provider fails (`CHAOS_500_RATE=1.0`) → **degraded** with excerpts →
**failed** on a question that retrieves nothing → banner appears on its own →
**Retry** while still down, which returns a *different* error and a *new* trace id
(proof it is a real attempt, not a replay of the recorded failure) →
provider recovers → **the same card resolves**.

Two things the recording is honest about:

- The final frame is **refused**, not a green answer. Under chaos, `failed`
  requires empty retrieval, so the recovered turn's correct outcome is a
  refusal — the question is genuinely outside the corpus. A green frame here
  would have needed a different failure path.
- One row survives the whole arc.
  `SELECT client_message_id, count(*) FROM messages GROUP BY 1` returns 1 for
  every id after two retries; D-02 holds through the retry path.
