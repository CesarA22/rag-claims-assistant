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
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |

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
