"""Per-question deadline and cost ledger.

Facts live here (what was spent, what remains). Judgment about whether that
is enough to try again lives in resilient.py.

The ledger is mutated in place and published on a ContextVar. A child task
inherits a copy of the context, so ContextVar.set() inside a task never
reaches the parent — every writer must call charge() on this shared object.
question_budget() is the only thing that ever sets the var.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import time

from app.llm.base import Message, Usage

# Conventional estimate is chars/4. Dividing by 3 rounds against us on a
# ceiling gate: we occasionally degrade a question we could have afforded,
# and never bill one we could not.
_CHARS_PER_TOKEN = 3

_MILLION = 1_000_000.0


@dataclass(frozen=True)
class Pricing:
    input_per_m: float = 0.75
    cached_input_per_m: float = 0.075
    output_per_m: float = 4.50

    @classmethod
    def from_env(cls) -> Pricing:
        return cls(
            input_per_m=float(os.getenv("LLM_PRICE_INPUT_PER_M", "0.75")),
            cached_input_per_m=float(os.getenv("LLM_PRICE_CACHED_INPUT_PER_M", "0.075")),
            output_per_m=float(os.getenv("LLM_PRICE_OUTPUT_PER_M", "4.50")),
        )

    def cost_usd(self, usage: Usage) -> float:
        uncached = max(0, usage.prompt_tokens - usage.cached_prompt_tokens)
        return (
            uncached * self.input_per_m
            + usage.cached_prompt_tokens * self.cached_input_per_m
            + (usage.completion_tokens + usage.reasoning_tokens) * self.output_per_m
        ) / _MILLION


class QuestionBudget:
    def __init__(
        self,
        *,
        deadline: float,
        ceiling_usd: float,
        pricing: Pricing,
        now: Callable[[], float],
    ) -> None:
        self.deadline = deadline
        self.ceiling_usd = ceiling_usd
        self.pricing = pricing
        self._now = now
        self.spent_usd = 0.0
        self.prompt_tokens = 0
        self.cached_prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0

    def remaining_s(self) -> float:
        return max(0.0, self.deadline - self._now())

    def charge(self, usage: Usage) -> None:
        self.spent_usd += self.pricing.cost_usd(usage)
        self.prompt_tokens += usage.prompt_tokens
        self.cached_prompt_tokens += usage.cached_prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.reasoning_tokens += usage.reasoning_tokens

    def estimate(self, messages: list[Message]) -> float:
        chars = sum(len(m.content) for m in messages)
        tokens = max(1, chars // _CHARS_PER_TOKEN)
        return tokens * self.pricing.input_per_m / _MILLION

    def can_afford(self, messages: list[Message]) -> bool:
        return self.spent_usd + self.estimate(messages) <= self.ceiling_usd


_current: ContextVar[QuestionBudget | None] = ContextVar("question_budget", default=None)


@contextmanager
def question_budget(
    *,
    now: Callable[[], float] | None = None,
    request_budget_s: float = 6.0,
    ceiling_usd: float = 0.05,
    pricing: Pricing | None = None,
) -> Iterator[QuestionBudget]:
    clock = now or time.monotonic
    budget = QuestionBudget(
        deadline=clock() + request_budget_s,
        ceiling_usd=ceiling_usd,
        pricing=pricing or Pricing(),
        now=clock,
    )
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)


def current_budget() -> QuestionBudget | None:
    return _current.get()
