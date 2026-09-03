"""Fail if the requirement register is dishonest.

Rules:
- status is one of done | partial | pending | cut
- cut must carry a reason in notes
- partial must name the remaining gap in notes
- an R- row with status done must have a non-empty tests list
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "docs" / "requirements.yaml"
STATUSES = frozenset({"done", "partial", "pending", "cut"})

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
