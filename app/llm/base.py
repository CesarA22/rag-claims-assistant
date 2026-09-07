import hashlib
import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class Usage(BaseModel):
    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0


class Completion(BaseModel):
    text: str
    parsed: dict[str, Any] | None = None
    usage: Usage = Field(default_factory=Usage)
    model: str = "fake-1"
    # The provider stopped at the output cap rather than finishing. Distinguishes
    # our own budget policy biting from the provider breaking its schema contract
    # — two failures that look identical at `parsed is None` and deserve
    # different outcomes (a refusal, and a 502).
    truncated: bool = False


class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Completion:
        """timeout_s and max_output_tokens are owned by resilient.py.

        Callers above the decorator must not pass either — if they do, budget
        policy has leaked into the pipeline and the seam is cosmetic.
        """
        ...


_EVIDENCE_ID = re.compile(r"evidence_id=(\S+)")


def prompt_fingerprint(messages: list[Message]) -> dict[str, Any]:
    """What a failure log may carry about the prompt: a hash and the evidence ids.

    Never the prompt text. The rendered prompt contains the retrieved corpus, and
    ingest-time redaction does not remove policyholder names — it matches CPF,
    phone and e-mail patterns only. Logs are retained longer, shipped further and
    read by more people than a response body, so the prompt does not go in one.

    Evidence ids are also the more useful diagnostic: they are what a person
    debugging a grounding failure would go and look up.
    """
    joined = "\n".join(f"{m.role}:{m.content}" for m in messages)
    return {
        "prompt_hash": hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12],
        "evidence_ids": _EVIDENCE_ID.findall(joined),
    }
