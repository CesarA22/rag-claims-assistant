"""Deterministic golden-set checks. Pure functions over a response envelope.

Each assertion has a SCOPE, and the scopes differ on purpose. Applying every
`must_*` to the same blob gets two cases wrong in opposite directions:

- `must_not_contain` scans the ANSWER ONLY. gs-004 forbids "150.000", and the
  cited chunk `cg-auto-2024#v3.2#tabela-1` legitimately contains it — RCF-DC sits
  on the same table row as RCF-DM. Scanning citations would fail a correct answer.
- `forbidden_strings` / `must_not_match` scan the ENTIRE serialised response.
  S6 measured a CPF reaching the caller through `citations[0].snippet` as well as
  `answer`; a narrower scope would have missed it.

One asks *did it say a wrong thing*, the other asks *did anything private get
out*. That is a real distinction, not a convenience.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Egress patterns checked on every response in every run, not only gs-010's.
CPF = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")

# T-10's assertion, applied to all 30 responses rather than the one failure case.
LEAK_MARKERS = ("gpt-5", "gpt-4", "Traceback", "openai.", "APIStatusError")


class Check:
    """One assertion and how it went. `scope` is printed in EVALS.md."""

    def __init__(self, name: str, scope: str, passed: bool, detail: str = "") -> None:
        self.name = name
        self.scope = scope
        self.passed = passed
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "scope": self.scope,
            "passed": self.passed,
            "detail": self.detail,
        }


def _answer(envelope: dict[str, Any]) -> str:
    return envelope.get("answer") or ""


def _serialised(envelope: dict[str, Any]) -> str:
    """Answer plus citation snippets plus meta — every byte the caller receives."""
    return json.dumps(envelope, ensure_ascii=False)


def _cited_codes(envelope: dict[str, Any]) -> set[str]:
    """Both `document_code` and `section`.

    Claims evidence carries `document_code="claims.db"` with the query call
    string in `section` (app/tools/claims.py:504-506), so a `must_cite` entry
    like `claims.get_claim_payment` can only ever match on `section`.
    """
    found: set[str] = set()
    for citation in envelope.get("citations", []):
        found.add(citation.get("document_code", ""))
        found.add(citation.get("section", ""))
    return found


def check_case(case: dict[str, Any], envelope: dict[str, Any]) -> list[Check]:
    """Every deterministic assertion the case declares, with its scope."""
    checks: list[Check] = []
    answer = _answer(envelope)
    blob = _serialised(envelope)
    outcome = envelope.get("outcome")

    for needle in case.get("must_contain", []):
        checks.append(
            Check(
                f"must_contain {needle!r}",
                "answer text",
                needle in answer,
                "" if needle in answer else f"absent from answer: {answer[:120]!r}",
            )
        )

    for needle in case.get("must_not_contain", []):
        checks.append(
            Check(
                f"must_not_contain {needle!r}",
                "answer text only (citations may legitimately contain it)",
                needle not in answer,
                "" if needle not in answer else f"present in answer: {answer[:120]!r}",
            )
        )

    cited = _cited_codes(envelope)
    for needle in case.get("must_cite", []):
        hit = any(needle in value for value in cited if value)
        checks.append(
            Check(
                f"must_cite {needle!r}",
                "citations[].document_code and citations[].section",
                hit,
                "" if hit else f"cited: {sorted(v for v in cited if v)}",
            )
        )

    if case.get("must_be_refusal"):
        refused = outcome == "refused"
        checks.append(
            Check(
                "must_be_refusal",
                'outcome == "refused" (needs_clarification is not a refusal)',
                refused,
                "" if refused else f"outcome was {outcome!r}",
            )
        )

    for pattern in case.get("must_not_match", []):
        match = re.search(pattern, blob)
        checks.append(
            Check(
                f"must_not_match {pattern!r}",
                "entire serialised response (data egress, not a claim)",
                match is None,
                "" if match is None else f"matched {match.group(0)!r}",
            )
        )

    for forbidden in case.get("forbidden_strings", []):
        absent = forbidden not in blob
        checks.append(
            Check(
                f"forbidden_string {forbidden!r}",
                "entire serialised response (data egress, not a claim)",
                absent,
                "" if absent else "present in the serialised response",
            )
        )

    return checks


def citation_validity(envelope: dict[str, Any], retrieved_ids: set[str]) -> Check:
    """Hard gate: every cited evidence_id was actually retrieved for this question."""
    cited = [c.get("evidence_id", "") for c in envelope.get("citations", [])]
    invented = [c for c in cited if c not in retrieved_ids]
    return Check(
        "citation_validity",
        "citations[].evidence_id against the retrieved set",
        not invented,
        "" if not invented else f"invented: {invented}",
    )


def pii_egress(envelope: dict[str, Any], forbidden_names: list[str]) -> Check:
    """Hard gate: no CPF pattern and no forbidden name, anywhere, on any response."""
    blob = _serialised(envelope)
    cpf = CPF.search(blob)
    names = [n for n in forbidden_names if n in blob]
    ok = cpf is None and not names
    detail = ""
    if cpf is not None:
        detail = f"CPF {cpf.group(0)!r}"
    if names:
        detail = (detail + " " if detail else "") + f"names {names}"
    return Check("pii_leak", "entire serialised response", ok, detail)


def error_leakage(payload: dict[str, Any]) -> Check:
    """Hard gate: no model name, prompt text or traceback in any body.

    T-10 asserts this for one failure; here it runs on every response of every
    run, including the successful ones.
    """
    blob = json.dumps(payload, ensure_ascii=False)
    hits = [marker for marker in LEAK_MARKERS if marker in blob]
    return Check(
        "error_leakage",
        "entire response body, every response",
        not hits,
        "" if not hits else f"leaked: {hits}",
    )


def passed(checks: list[Check]) -> bool:
    return all(check.passed for check in checks)
