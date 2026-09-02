"""Deterministic PII detection and redaction. Shared by ingest and the output scrubber."""

from __future__ import annotations

import re
from typing import Literal

PiiKind = Literal["cpf", "phone", "email"]

PII_HEADER = re.compile(r"^(segurado|nome|cpf|telefone|e-?mail)$", re.IGNORECASE)

_CPF_FORMATTED = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
_CPF_BARE = re.compile(r"\b\d{11}\b")
_PHONE = re.compile(r"\(\d{2}\)\s*\d{4,5}-?\d{4}")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _cpf_check_digits(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit():
        return False

    def check(seq: str, weight: int) -> int:
        total = sum(int(d) * w for d, w in zip(seq, range(weight, 1, -1)))
        remainder = total % 11
        return 0 if remainder < 2 else 11 - remainder

    return check(digits[:9], 10) == int(digits[9]) and check(digits[:10], 11) == int(
        digits[10]
    )


def _is_cpf(value: str) -> bool:
    return _cpf_check_digits(re.sub(r"\D", "", value))


def scan(text: str) -> set[PiiKind]:
    found: set[PiiKind] = set()
    if any(_is_cpf(m.group(0)) for m in _CPF_FORMATTED.finditer(text)) or any(
        _is_cpf(m.group(0)) for m in _CPF_BARE.finditer(text)
    ):
        found.add("cpf")
    if _PHONE.search(text):
        found.add("phone")
    if _EMAIL.search(text):
        found.add("email")
    return found


def redact(text: str) -> str:
    text = _EMAIL.sub("[EMAIL]", text)
    text = _CPF_FORMATTED.sub("[CPF]", text)

    def _bare(match: re.Match[str]) -> str:
        return "[CPF]" if _is_cpf(match.group(0)) else match.group(0)

    text = _CPF_BARE.sub(_bare, text)
    return _PHONE.sub("[TELEFONE]", text)


def redact_table_columns(
    headers: list[str], rows: list[list[str | None]]
) -> tuple[list[str], list[list[str | None]], list[int]]:
    """Blank cells whose header names a person, CPF, phone or e-mail."""
    indexes = [
        i
        for i, header in enumerate(headers)
        if header and PII_HEADER.match(header.strip())
    ]
    if not indexes:
        return headers, rows, []
    blanked: list[list[str | None]] = []
    for row in rows:
        copy = list(row)
        for i in indexes:
            if i < len(copy) and copy[i]:
                copy[i] = ""
        blanked.append(copy)
    return headers, blanked, indexes
