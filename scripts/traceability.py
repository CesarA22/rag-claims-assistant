"""Fail if the requirement register is dishonest.

Rules:
- status is one of done | partial | pending | cut
- cut must carry a reason in notes
- partial must name the remaining gap in notes
- an R- row with status done must have a non-empty tests list
- every id in a `tests:` list exists somewhere in the suite

That last rule is why D-05 could list `tests: [T-27]` for three sessions while
T-27 existed nowhere in the repository. A register that names a test nobody wrote
is worse than one that admits a gap: it converts a missing test into a claimed
one, and the whole point of the file is to be the place that cannot do that.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "docs" / "requirements.yaml"
STATUSES = frozenset({"done", "partial", "pending", "cut"})

# Where a test id may live, and nowhere else.
#
# The web half is NOT optional: T-41 and T-42 exist only in
# web/src/components/*.test.tsx, so a check that walks tests/ alone reports two
# phantoms that are not phantoms — and the reflex fix for that is an allowlist,
# which would blind the rule to two real requirements permanently.
#
# And the roots must stay narrow. A repository-wide glob makes the rule vacuous:
# every id is mentioned somewhere under docs/plans/, so nothing would ever fail.
SCAN_ROOTS = (
    (ROOT / "tests", "*.py"),
    (ROOT / "web" / "src", "*.test.tsx"),
)

_TEST_ID = re.compile(r"\bT-\d{2,}\b")

_ID = re.compile(r"^  - id: (\S+)\s*$")
_KEY = re.compile(r"^    ([A-Za-z0-9_]+):\s*(.*)$")
_LIST_ITEM = re.compile(r"^      - (.+)$")
_INLINE_LIST = re.compile(r"^\[(.*)\]$")


def load_requirements(path: Path = REGISTER) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    list_key: str | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            records.append(current)
            current = None

    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("#") or not raw.strip():
            continue
        if raw.strip() == "requirements:":
            continue
        id_match = _ID.match(raw)
        if id_match:
            flush()
            current = {"id": id_match.group(1)}
            list_key = None
            continue
        if current is None:
            continue
        key_match = _KEY.match(raw)
        if key_match:
            key, value = key_match.group(1), key_match.group(2).strip()
            list_key = None
            inline = _INLINE_LIST.match(value)
            if inline:
                items = [
                    item.strip().strip("'\"")
                    for item in inline.group(1).split(",")
                    if item.strip()
                ]
                current[key] = items
            elif value == "":
                current[key] = []
                list_key = key
            else:
                current[key] = value.strip("'\"")
            continue
        item_match = _LIST_ITEM.match(raw)
        if item_match and list_key is not None:
            values = current.setdefault(list_key, [])
            assert isinstance(values, list)
            values.append(item_match.group(1).strip())
    flush()
    return records


def known_test_ids() -> set[str]:
    """Every T- id mentioned in the suite itself.

    Read from the test files rather than from a hand-kept list, because a list
    is a third thing to keep in sync. The consequence, and the one rule this
    imposes on test authors: an id written anywhere in a test file — a docstring
    included — counts as existing. Do not name a dead id in prose, or it stops
    reading as dead here. A missing scan root raises rather than
    contributing nothing: a silently empty scan either fails every requirement
    at once — which invites the wrong fix — or, if someone then makes an empty
    scan permissive, passes everything forever. Loud is the only safe direction.
    """
    found: set[str] = set()
    for root, pattern in SCAN_ROOTS:
        if not root.is_dir():
            raise RuntimeError(
                f"traceability scan root {root} is missing. Run this from a full "
                f"checkout — the Docker image copies scripts/ but neither tests/ "
                f"nor web/, so this check must never run from inside it."
            )
        for path in root.rglob(pattern):
            found.update(_TEST_ID.findall(path.read_text(encoding="utf-8")))
    return found


def check(records: list[dict[str, object]] | None = None) -> list[str]:
    if records is None:
        records = load_requirements()
    errors: list[str] = []
    for row in records:
        ident = str(row.get("id", "?"))
        status = str(row.get("status", ""))
        notes = str(row.get("notes", "")).strip()
        tests = row.get("tests", [])
        if status not in STATUSES:
            errors.append(f"{ident}: status {status!r} is not {sorted(STATUSES)}")
            continue
        if status == "cut" and not notes:
            errors.append(f"{ident}: cut without a reason in notes")
        if status == "partial" and not notes:
            errors.append(f"{ident}: partial without a named gap in notes")
        if ident.startswith("R-") and status == "done":
            if not isinstance(tests, list) or not tests:
                errors.append(f"{ident}: done with an empty tests list")

    # Ids are taken from the PARSED `tests` field, never by regexing the raw
    # YAML. T-12 appears only in R-10's notes prose and the string "T-20..T-26"
    # appears there too; a regex over the file trips on both and an implementer
    # then writes an allowlist, which is dead code for a problem that does not
    # exist.
    known = known_test_ids()
    for row in records:
        ident = str(row.get("id", "?"))
        tests = row.get("tests", [])
        if not isinstance(tests, list):
            continue
        for test_id in tests:
            if test_id not in known:
                errors.append(
                    f"{ident}: lists {test_id}, which exists in no test file"
                )
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("requirement register failed:", file=sys.stderr)
        for item in errors:
            print(f"  {item}", file=sys.stderr)
        return 1
    print(f"{len(load_requirements())} requirements, register ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
