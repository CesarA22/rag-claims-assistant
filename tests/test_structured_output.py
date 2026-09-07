"""T-49 / T-50 / R-01: the structured-output contract, offline.

The failure this guards is probabilistic and lives on the wire: with
`strict: False` the live model honoured the draft schema on roughly one call in
three and echoed the JSON Schema itself back on the others. Nothing in the
offline suite could see it — `FakeProvider` discards `schema` entirely and
`jsonschema` is not a dependency — so the suite passed identically before and
after the fix.

These tests do not reproduce that failure. They pin the two things that make the
fix hold: the schemas stay in the shape strict mode requires, and a body that is
not the schema fails as a provider-contract error instead of a refusal. The live
10-call gate is the evidence that the wire behaviour changed; this is the
regression signal that stops it drifting back.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.errors import ModelContract
from app.llm.base import Completion
from app.services.ask import DRAFT_SCHEMA, _parse_draft
from evals.judge import VERDICT_SCHEMA

# The JSON Schema the API echoed back when strict was False — the actual
# observed failure body, top-level keys and all.
SCHEMA_ECHO = {"type": "object", "properties": {}, "required": []}


def _objects(node: Any) -> list[dict[str, Any]]:
    """Every `type: object` subschema, at any depth."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found.extend(_objects(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_objects(item))
    return found


@pytest.mark.parametrize(
    ("name", "schema"),
    [("DRAFT_SCHEMA", DRAFT_SCHEMA), ("VERDICT_SCHEMA", VERDICT_SCHEMA)],
)
def test_every_schema_object_is_strict_shaped(name: str, schema: dict[str, Any]):
    """T-49 / R-01: strict mode's two structural rules hold at every depth.

    Both schemas, not just the draft: `strict` is set on the adapter and applies
    to whatever schema it is handed. A judge schema that keeps the loose shape is
    a 400 at the first judged case, which exits judge_run.py and leaves the four
    `grade: judge` golden cases ungraded.
    """
    objects = _objects(schema)
    assert objects, f"{name} declares no objects — the walker is looking at the wrong shape"
    for obj in objects:
        assert obj.get("additionalProperties") is False, (
            f"{name}: object {sorted(obj.get('properties', {}))} omits additionalProperties: false"
        )
        assert sorted(obj.get("required", [])) == sorted(obj.get("properties", {})), (
            f"{name}: object {sorted(obj.get('properties', {}))} does not require every property"
        )


def test_citations_is_required_and_never_nullable():
    """T-49 / R-01: an absent citation list is `[]`, not `null`.

    The usual strict-mode workaround for an optional field is to union it with
    null. Here that reintroduces the bug being fixed: `citations: null` fails
    `Draft` validation, which is the same path that produced the spurious
    citation refusal.
    """
    assert "citations" in DRAFT_SCHEMA["required"]
    assert DRAFT_SCHEMA["properties"]["citations"]["type"] == "array"


def test_a_schema_echo_fails_the_turn_rather_than_refusing():
    """T-50 / R-02: a body that is not the draft schema raises ModelContract.

    It used to become `refused` with the citation-validation message — telling
    the analyst the sources did not support an answer the model never produced.
    A refusal is a claim about the corpus; this is a claim about the provider.
    """
    completion = Completion(text="{}", parsed=SCHEMA_ECHO, truncated=False)

    with pytest.raises(ModelContract) as caught:
        _parse_draft(completion)

    assert caught.value.code == "model_contract"
    # Keys only. The body can quote the retrieved corpus and ingest-time
    # redaction does not remove policyholder names.
    assert caught.value.context["parsed_keys"] == ["properties", "required", "type"]


def test_a_truncated_body_still_refuses_gracefully():
    """T-50 / R-05: hitting our own output cap is a refusal, not a 502.

    `max_output_tokens` is budget policy we chose. When it cuts the JSON
    mid-object the provider kept its side of the contract, so the turn degrades
    to a refusal that says so — the same 200 the analyst got before.
    """
    completion = Completion(text='{"outcome":"ans', parsed=None, truncated=True)

    draft = _parse_draft(completion)

    assert draft.outcome == "refused"
    assert "limite de tamanho" in draft.answer
    assert draft.citations == []


def test_unparseable_without_the_truncation_flag_is_a_contract_failure():
    """T-50 / R-02: prose where JSON was demanded is the provider's failure.

    The two cases above are separated by the provider's own `incomplete` status,
    not by whether the text happened to parse — otherwise a model that answered
    in prose would be reported to the analyst as an over-long answer.
    """
    completion = Completion(text="Claro! A vigência é de 12 meses.", parsed=None, truncated=False)

    with pytest.raises(ModelContract) as caught:
        _parse_draft(completion)

    assert caught.value.context["parsed_keys"] == []
