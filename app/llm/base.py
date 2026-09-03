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
