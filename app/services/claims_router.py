"""Decide, deterministically, which named claims queries a question needs.

**Why a regex router and not model tool-calling.** The obvious alternative is a
plan/execute round trip: hand the model the tool schema, let it choose a query
and arguments, execute, then ask again. It was rejected for four reasons, in
descending order of weight:

1. It costs a second model call inside a US$0.05-per-question ceiling, on every
   question, to decide something six regexes decide.
2. It changes `LLMProvider.complete`, which means changing the Protocol and all
   three providers, so the fake and chaos doubles would have to learn tool
   calling to keep the keyless path working.
3. It is untestable without the model. Every gate in this system is deterministic
   code that runs *after* the model precisely so it can be tested without one,
   and putting the data-source decision behind the provider would be the first
   place that stopped being true.
4. There are six named queries, not an open action space. The thing model tool
   calling buys — generalising over actions nobody enumerated — is not needed
   here, and the thing it costs is the property the architecture is built on.

The cost of the choice, stated: a question phrased in a way no keyword matches
gets no database evidence and is answered from the corpus alone. That is a
recall problem with a visible symptom (a refusal, or a corpus-only answer) rather
than a correctness problem, and widening it is editing a list in this file.

**Two queries per claim, not one.** `get_claim_payment` alone is not enough for
gs-007 — see `route()`.
"""

from __future__ import annotations

import re

from app.services.grounding import normalize

# The identifier formats app/tools/claims.py already validates. Matched here in
# their written form, because that is how an analyst types them.
CLAIM_NUMBER = re.compile(r"\bSIN-\d{4}-\d{6}\b", re.IGNORECASE)
POLICY_NUMBER = re.compile(r"\bAP-(?:AUTO|RES|EMP)-\d{6}\b", re.IGNORECASE)

# Intent keywords, normalised (lowercase, unaccented) exactly as the question is.
_CLAIM_WORDS = (
    "sinistro", "sinistros", "pago", "paga", "pagamento", "indeniza",
    "reembols", "aviso", "regulacao", "ocorrencia",
)

Plan = tuple[str, dict[str, object]]


def route(question: str) -> list[Plan]:
    """Named queries this question needs, in the order their evidence should read.

    An empty list means "corpus only", which is the common case: the router is a
    narrow addition to retrieval, not a replacement for it.

    **A claim number always routes BOTH `get_claim` and `get_claim_payment`, and
    that is measured rather than tidy.** gs-007 asks whether the amount paid on
    SIN-2025-004512 respected the RCF-DM limit. Routing only the payment query
    leaves the answer's grounded ratio at 0.769 — below SUFFICIENCY_MIN 0.80 — so
    the turn returns the unsupported-answer refusal while its `must_cite` set is
    satisfied: a failure that looks like a success. The 0.077 that closes the gap
    is the Portuguese label `_claimed()` renders on the get_claim row. The two
    rows are also the pair the question is actually about: `claims.claim_amount`
    is the amount CLAIMED and `payments.paid_amount` the amount PAID, and
    answering "was it within the limit" from either alone is the mistake AGENTS.md
    warns about.
    """
    plans: list[Plan] = []
    haystack = normalize(question)

    claims = _unique(m.group(0).upper() for m in CLAIM_NUMBER.finditer(question))
    for claim_number in claims:
        plans.append(("get_claim", {"claim_number": claim_number}))
        plans.append(("get_claim_payment", {"claim_number": claim_number}))

    policies = _unique(m.group(0).upper() for m in POLICY_NUMBER.finditer(question))
    for policy_number in policies:
        plans.append(("get_policy", {"policy_number": policy_number}))
        # The claims list is only added when the question is about claims. It is
        # up to twenty rows, and a question about the policy's own terms —
        # coverage, vigência, product — is answered from the corpus with the
        # policy row for context, not from a list of its incidents.
        if claims or _mentions(haystack, _CLAIM_WORDS):
            plans.append(("list_claims_by_policy", {"policy_number": policy_number}))

    return plans


def _unique(values) -> list[str]:
    """Preserve order, drop repeats — the same id twice is one query."""
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen)


def _mentions(haystack: str, words: tuple[str, ...]) -> bool:
    return any(word in haystack for word in words)
