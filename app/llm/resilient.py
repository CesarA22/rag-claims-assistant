"""Decorator holding ALL provider policy.

timeout_s and max_output_tokens are computed here and passed down. Callers
above this module must never pass either.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.domain.errors import (
    CircuitOpen,
    InvalidRequest,
    InsurCoError,
    ProviderDegraded,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
)
from app.llm.base import Completion, Message
from app.services.budget import Pricing, QuestionBudget, current_budget, question_budget

logger = logging.getLogger(__name__)

RETRYABLE = (ProviderTimeout, ProviderUnavailable, RateLimited)

BreakerState = Literal["closed", "open", "half_open"]


@dataclass(frozen=True)
class ResilienceConfig:
    request_budget_s: float = 6.0
    min_retry_budget_s: float = 1.5
    max_retries: int = 2
    backoff_base_s: float = 0.25
    backoff_max_s: float = 2.0
    breaker_failures: int = 5
    breaker_reset_s: float = 30.0
    cost_ceiling_usd: float = 0.05
    max_output_tokens: int = 800
    reasoning_effort: str = "none"
    model: str = "gpt-5.4-mini"

    @classmethod
    def from_env(cls) -> ResilienceConfig:
        return cls(
            request_budget_s=float(os.getenv("LLM_REQUEST_BUDGET_S", "6.0")),
            min_retry_budget_s=float(os.getenv("LLM_MIN_RETRY_BUDGET_S", "1.5")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
            backoff_base_s=float(os.getenv("LLM_BACKOFF_BASE_S", "0.25")),
            backoff_max_s=float(os.getenv("LLM_BACKOFF_MAX_S", "2.0")),
            breaker_failures=int(os.getenv("LLM_BREAKER_FAILURES", "5")),
            breaker_reset_s=float(os.getenv("LLM_BREAKER_RESET_S", "30.0")),
            cost_ceiling_usd=float(os.getenv("LLM_COST_CEILING_USD", "0.05")),
            max_output_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "800")),
            reasoning_effort=os.getenv("LLM_REASONING_EFFORT", "none"),
            model=os.getenv("LLM_MODEL", "gpt-5.4-mini"),
        )


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int,
        reset_s: float,
        now: Callable[[], float],
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_s = reset_s
        self._now = now
        self.state: BreakerState = "closed"
        self.consecutive_failures = 0
        self._opened_at: float | None = None
        self._opened_at_wall: float | None = None
        self._probing = False

    def admit(self) -> Literal["allow", "probe", "reject"]:
        if self.state == "closed":
            return "allow"
        if self.state == "half_open":
            return "reject"
        assert self._opened_at is not None
        if self._now() - self._opened_at < self.reset_s:
            return "reject"
        if self._probing:
            return "reject"
        self._probing = True
        self.state = "half_open"
        logger.info("breaker_half_open consecutive_failures=%s", self.consecutive_failures)
        return "probe"

    def record_success(self) -> None:
        was_open = self.state != "closed"
        self.state = "closed"
        self.consecutive_failures = 0
        self._opened_at = None
        self._opened_at_wall = None
        self._probing = False
        if was_open:
            logger.info("breaker_closed")

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        was_probe = self.state == "half_open" or self._probing
        self._probing = False
        if was_probe or self.consecutive_failures >= self.failure_threshold:
            self.state = "open"
            self._opened_at = self._now()
            self._opened_at_wall = time.time()
            logger.warning(
                "breaker_open consecutive_failures=%s",
                self.consecutive_failures,
            )

    def snapshot(self) -> dict[str, Any]:
        reset_in_s = 0.0
        if self.state == "open" and self._opened_at is not None:
            reset_in_s = max(0.0, self.reset_s - (self._now() - self._opened_at))
        return {
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "opened_at": self._opened_at_wall,
            "reset_in_s": reset_in_s,
        }


class ResilientProvider:
    def __init__(
        self,
        inner: Any,
        config: ResilienceConfig | None = None,
        *,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        rand: Callable[[], float] | None = None,
        pricing: Pricing | None = None,
    ) -> None:
        self.inner = inner
        self.config = config or ResilienceConfig()
        self.pricing = pricing or Pricing()
        self._now = now or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._rand = rand or random.random
        self.breaker = CircuitBreaker(
            failure_threshold=self.config.breaker_failures,
            reset_s=self.config.breaker_reset_s,
            now=self._now,
        )

    @property
    def name(self) -> str:
        return getattr(self.inner, "name", "resilient")

    def start_question(self):
        return question_budget(
            now=self._now,
            request_budget_s=self.config.request_budget_s,
            ceiling_usd=self.config.cost_ceiling_usd,
            pricing=self.pricing,
        )

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Completion:
        del timeout_s, max_output_tokens
        budget = current_budget()
        if budget is None:
            budget = QuestionBudget(
                deadline=self._now() + self.config.request_budget_s,
                ceiling_usd=self.config.cost_ceiling_usd,
                pricing=self.pricing,
                now=self._now,
            )

        admission = self.breaker.admit()
        if admission == "reject":
            raise CircuitOpen(context=self._log_context(messages))

        is_probe = admission == "probe"
        retries = 0 if is_probe else self.config.max_retries
        last_error: InsurCoError | None = None

        for attempt in range(retries + 1):
            remaining = budget.remaining_s()
            if remaining < self.config.min_retry_budget_s:
                raise self._degraded(messages, last_error, attempts=attempt, why="time")
            if attempt > 0:
                delay = self._backoff_s(attempt - 1)
                if remaining <= delay + self.config.min_retry_budget_s:
                    raise self._degraded(messages, last_error, attempts=attempt, why="time")
                if not budget.can_afford(messages):
                    raise self._degraded(messages, last_error, attempts=attempt, why="cost")
                logger.info("llm_retry attempt=%s delay_s=%.3f", attempt, delay)
                await self._sleep(delay)
                remaining = budget.remaining_s()
                if remaining < self.config.min_retry_budget_s:
                    raise self._degraded(messages, last_error, attempts=attempt, why="time")

            logger.info("llm_attempt attempt=%s timeout_s=%.3f", attempt, remaining)
            try:
                completion = await asyncio.wait_for(
                    self.inner.complete(
                        messages,
                        schema=schema,
                        timeout_s=remaining,
                        max_output_tokens=self.config.max_output_tokens,
                    ),
                    timeout=remaining,
                )
            except TimeoutError:
                last_error = ProviderTimeout(context=self._log_context(messages))
                self.breaker.record_failure()
                if is_probe:
                    raise self._degraded(
                        messages, last_error, attempts=attempt + 1, why="time"
                    ) from last_error
                continue
            except RETRYABLE as exc:
                last_error = exc
                if not exc.context:
                    exc.context = self._log_context(messages)
                self.breaker.record_failure()
                if is_probe:
                    raise self._degraded(
                        messages, last_error, attempts=attempt + 1, why="retryable"
                    ) from exc
                continue
            except InvalidRequest:
                raise
            except InsurCoError:
                raise

            budget.charge(completion.usage)
            self.breaker.record_success()
            return completion

        raise self._degraded(
            messages, last_error, attempts=retries + 1, why="retries_exhausted"
        )

    def _backoff_s(self, retry_index: int) -> float:
        cap = min(self.config.backoff_base_s * (2**retry_index), self.config.backoff_max_s)
        return self._rand() * cap

    def _log_context(self, messages: list[Message]) -> dict[str, Any]:
        prompt = messages[0].content if messages else ""
        return {"model": self.config.model, "prompt": prompt}

    def _degraded(
        self,
        messages: list[Message],
        last_error: InsurCoError | None,
        *,
        attempts: int,
        why: str,
    ) -> ProviderDegraded:
        underlying = last_error.code if last_error is not None else (
            "provider_timeout" if why == "time" else "provider_unavailable"
        )
        logger.warning(
            "llm_degraded why=%s attempts=%s underlying=%s",
            why,
            attempts,
            underlying,
        )
        return ProviderDegraded(
            underlying_code=underlying,
            attempts=attempts,
            context=self._log_context(messages),
        )
