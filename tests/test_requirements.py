"""T-55 / R-01: the register may not claim a test that does not exist."""

from pathlib import Path

import pytest

from scripts import traceability
from scripts.traceability import check, known_test_ids, load_requirements


def test_register_vocabulary_and_honesty():
    """Register uses done|partial|pending|cut; cut/partial carry notes; done R-rows have tests."""
    assert check() == []
    ids = {str(row["id"]) for row in load_requirements()}
    assert "R-10" in ids
    assert {"D-01", "D-02", "D-03", "D-04", "D-05"} <= ids


def test_a_listed_test_that_does_not_exist_fails_the_register():
    """T-55 / R-01: the rule that would have caught D-05 listing an unwritten test.

    D-05 carried `tests: [T-27]` for three sessions while T-27 existed nowhere in
    the repository. The register's whole value is being the one place that cannot
    claim coverage it does not have, and it was quietly doing exactly that.
    """
    # Assembled rather than written out, because known_test_ids() harvests every
    # T-nn it finds in a test file — spelling the phantom here would make it
    # real to the check under test. That hazard is the rule's one sharp edge and
    # this is the place it bites first.
    phantom = "T-" + "99"
    rows = load_requirements()
    rows[0]["tests"] = [*rows[0]["tests"], phantom]

    errors = check(rows)

    assert any(phantom in error for error in errors)


def test_the_web_suite_is_scanned_and_is_not_optional():
    """T-55 / R-01: T-41 and T-42 live only under web/src, and are not phantoms.

    A check that walks tests/ alone reports those two as missing. The reflex fix
    for that is an allowlist, which would permanently blind the rule to two real
    requirements — so the web root is asserted rather than assumed.
    """
    roots = {root.name for root, _ in traceability.SCAN_ROOTS}
    assert roots == {"tests", "src"}

    ids = known_test_ids()
    assert {"T-41", "T-42", "T-27"} <= ids     # web-only ids
    assert {"T-01", "T-40"} <= ids             # python-only ids


def test_a_missing_scan_root_raises_instead_of_scanning_nothing(monkeypatch):
    """T-55 / R-01: a silent empty scan is the dangerous failure, not a loud one.

    An empty scan either fails every requirement at once — which invites an
    allowlist as the fix — or, once someone makes empty mean permissive, passes
    forever. It matters because the Docker image copies scripts/ but neither
    tests/ nor web/, so this check must never be run from inside it.
    """
    monkeypatch.setattr(traceability, "SCAN_ROOTS", ((Path("does-not-exist"), "*.py"),))

    with pytest.raises(RuntimeError, match="scan root"):
        known_test_ids()


def test_the_register_claims_every_test_that_exists():
    """T-55 / R-01: the other direction — a written test no requirement points at.

    T-39's own docstrings said "T-39 / R-04" while R-04 listed [T-29, T-32,
    T-34]. That is not dishonesty in the register, so `check()` does not fail on
    it — but it is coverage nobody can find from the register, which is the
    document an evaluator traces the brief against.
    """
    listed: set[str] = set()
    for row in load_requirements():
        tests = row.get("tests", [])
        if isinstance(tests, list):
            listed |= set(tests)

    assert known_test_ids() - listed == set()
