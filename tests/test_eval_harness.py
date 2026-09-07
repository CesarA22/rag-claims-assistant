"""T-52 / R-04: the eval harness must not be able to publish a verdict it did not earn.

Every case here is a way EVALS.md could have said something false. Three of them
crash the generator outright, which at least fails loudly; the fourth prints a
completely fabricated live run and calls it a PASS, which does not.

The harness has no tests of its own, and it is the thing that produces the
document an evaluator reads. That asymmetry is the reason this file exists.
"""

from __future__ import annotations

from typing import Any

import yaml

from evals import assertions, report, run_golden

CFG: dict[str, Any] = yaml.safe_load(report.THRESHOLDS.read_text(encoding="utf-8"))


def _record(case_id: str, run: int = 1, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "case_id": case_id,
        "grade": "deterministic",
        "run": run,
        "question": "q",
        "status_code": 200,
        "outcome": "answered",
        "answer": "A vigência padrão é de 12 meses.",
        "citations": [],
        "meta": {
            "provider": "openai",
            "degraded": False,
            "cost_usd": 0.0014,
            "usage": {
                "prompt_tokens": 1360,
                "cached_prompt_tokens": 0,
                "completion_tokens": 89,
            },
        },
        "elapsed_s": 1.8,
        "checks": [],
        "deterministic_pass": True,
        "hard_gates_pass": True,
        "suspected_replay": False,
    }
    base.update(overrides)
    return base


def _render(records: list[dict[str, Any]]) -> str:
    return report.render(
        [{"run": 1, "tag": "t", "records": records}],
        CFG,
        judged={},
        boundary=None,
        tier0=None,
        blocked=None,
        fresh=None,
    )


def test_a_failed_response_does_not_crash_the_generator():
    """T-52 / R-04: a record with `outcome: None` renders instead of raising.

    Any non-200 leaves `outcome` unset, and the per-case table sorted the
    outcome set directly — so a single provider error in thirty raised
    `TypeError: '<' not supported between instances of 'NoneType' and 'str'`
    and EVALS.md could not be generated at all. One 503 destroyed the report.
    """
    out = _render([_record("gs-001"), _record("gs-002", outcome=None, status_code=503)])

    assert "gs-002" in out


def test_one_record_without_meta_does_not_erase_a_live_run():
    """T-52 / R-04: a missing meta block is silence about the provider, not a claim.

    Providers were read with `.get("provider", "unknown")`, so a single record
    with no meta made the provider set `{"openai", "unknown"}`, which is not
    `{"openai"}` — and the whole document flipped to "Tier 2 did not run … the
    key has no credits" against a real, paid-for live run.
    """
    out = _render([_record("gs-001"), _record("gs-002", meta={}, status_code=503)])

    assert "Tier 2 did not run" not in out
    assert "not measured" not in out


def test_replayed_records_are_never_reported_as_a_live_run():
    """T-52 / R-04: the most dangerous failure — a fabricated live run that reads as real.

    Conversation ids and idempotency keys used to be stable across invocations
    and idempotency is persisted in Postgres, so re-running the live tier against
    an existing database returned the committed fake answers without calling the
    provider. `_from_turn` stamps the CURRENT process's provider onto a replayed
    turn, so those thirty fake answers came back labelled `provider=openai` and
    the report rendered them as a live run — PASS, on US$0.0000, with no network
    traffic at all.
    """
    records = [_record("gs-001"), _record("gs-002", suspected_replay=True)]
    out = _render(records)

    assert "replays, not measurements" in out
    assert "gs-002#r1" in out
    # And the cost and latency gates must not claim a pass off replayed input.
    assert "**PASS**" not in out.split("## Cost and latency")[1]


def test_a_zero_token_openai_turn_is_flagged_as_a_replay():
    """T-52 / R-04: the detector keys on tokens, because the provider name lies.

    A live call always bills input tokens. The degraded path is the only
    legitimate route to a completed turn with none, so it is excluded rather
    than flagged.
    """
    live = {"meta": {"provider": "openai", "usage": {"prompt_tokens": 1360}}}
    replayed = {"meta": {"provider": "openai", "usage": {"prompt_tokens": 0}}}
    degraded = {
        "meta": {"provider": "openai", "degraded": True, "usage": {"prompt_tokens": 0}}
    }
    keyless = {"meta": {"provider": "fake", "usage": {"prompt_tokens": 0}}}

    assert run_golden.suspected_replay(live) is False
    assert run_golden.suspected_replay(replayed) is True
    assert run_golden.suspected_replay(degraded) is False
    assert run_golden.suspected_replay(keyless) is False


def test_the_headline_golden_gate_is_actually_rendered():
    """T-52 / R-04: `deterministic_pass_rate` reaches the document.

    thresholds.yaml calls it the golden gate and carries a whole
    `expected_failures` argument about it; report.py never rendered it, so the
    number the gate is about did not appear in the generated file.
    """
    out = _render([_record("gs-001"), _record("gs-002", deterministic_pass=False)])

    assert "deterministic_pass_rate" in out
    assert "0.50 (1/2)" in out


def test_the_pii_gate_covers_everything_the_redactor_covers():
    """T-52 / R-03: bare CPFs, phones and e-mails are checked, not only formatted CPFs.

    The gate was a formatted-CPF regex plus five hardcoded names, while
    `app/safety/redact.py` also handles bare 11-digit CPFs with check-digit
    validation, phone numbers and e-mail addresses. None of those were checked on
    any of the thirty responses, so the hard gate was weaker than the code it
    exists to demonstrate — and F1 and F6a both tighten that code.
    """
    def blob(answer: str) -> dict[str, Any]:
        return {"outcome": "answered", "answer": answer, "citations": [], "meta": {}}

    # A valid bare CPF: no dots, no dash, correct check digits.
    assert assertions.pii_egress(blob("O CPF 52998224725 consta do cadastro."), []).passed is False
    assert assertions.pii_egress(blob("Ligue (41) 90000-0001."), []).passed is False
    assert assertions.pii_egress(blob("Escreva para joao@insurco.com.br."), []).passed is False
    # Formatted placeholder CPFs fail the check digits, so scan() cannot see
    # them and the literal regex must stay.
    assert assertions.pii_egress(blob("CPF 111.111.111-11."), []).passed is False
    # And an ordinary grounded answer is still clean.
    assert assertions.pii_egress(blob("A vigência padrão é de 12 meses."), []).passed is True
