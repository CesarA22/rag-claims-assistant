"""R-05 / R-04: retry, budget, breaker. Time is injected; nothing waits on the clock."""

from __future__ import annotations

import asyncio

import pytest

from app.domain.errors import (
    CircuitOpen,
    InvalidRequest,
    ProviderDegraded,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.llm.base import Message
from app.llm.fake import FakeProvider
from app.llm.resilient import ResilienceConfig, ResilientProvider
from app.services.budget import question_budget

MSGS = [Message(role="system", content="You are an assistant."), Message(role="user", content="q")]


class Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _wrap(
    inner: FakeProvider | None = None,
    clock: Clock | None = None,
    **cfg: object,
) -> tuple[ResilientProvider, FakeProvider, Clock]:
    inner = inner or FakeProvider()
    clock = clock or Clock()

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    provider = ResilientProvider(
        inner,
        ResilienceConfig(**cfg),  # type: ignore[arg-type]
        now=clock,
        sleep=sleep,
        rand=lambda: 1.0,
    )
    return provider, inner, clock


async def test_timeout_is_retried_and_deadline_shrinks():
    """T-05 / R-05: timeout → typed error, retry attempted, derived deadline shrinks."""
    llm, inner, _clock = _wrap()
    inner.enqueue(ProviderTimeout())
    inner.enqueue(FakeProvider.answered("ok", ["eid-1"]))

    result = await llm.complete(MSGS)

    assert result.text == "ok"
    assert inner.calls == 2
    assert inner.timeouts[0] is not None and inner.timeouts[1] is not None
    assert inner.timeouts[1] < inner.timeouts[0]
    assert inner.max_output_tokens_seen == [800, 800]


async def test_three_failures_exactly_two_retries_then_degrade():
    """T-06 / R-05: three failures → exactly two retries, then ProviderDegraded."""
    llm, inner, _clock = _wrap()
    inner.enqueue(ProviderUnavailable())
    inner.enqueue(ProviderUnavailable())
    inner.enqueue(ProviderUnavailable())

    with pytest.raises(ProviderDegraded) as caught:
        await llm.complete(MSGS)

    assert inner.calls == 3
    assert caught.value.attempts == 3
    assert caught.value.underlying_code == "provider_unavailable"
    assert "gpt" not in str(caught.value).lower()


async def test_invalid_request_is_not_retried_and_does_not_trip_breaker():
    """T-07 / R-05: InvalidRequest → zero retries, breaker untouched."""
    llm, inner, _clock = _wrap(breaker_failures=1)
    inner.enqueue(InvalidRequest("malformed"))

    with pytest.raises(InvalidRequest):
        await llm.complete(MSGS)

    assert inner.calls == 1
    assert llm.breaker.state == "closed"
    assert llm.breaker.consecutive_failures == 0


async def test_breaker_opens_probes_and_closes():
    """T-08 / R-05: breaker opens, provider not called; half-open probe after the window; success closes it."""
    llm, inner, clock = _wrap(breaker_failures=2, max_retries=0, breaker_reset_s=10.0)
    inner.enqueue(ProviderUnavailable())
    with pytest.raises(ProviderDegraded):
        await llm.complete(MSGS)
    inner.enqueue(ProviderUnavailable())
    with pytest.raises(ProviderDegraded):
        await llm.complete(MSGS)

    assert llm.breaker.state == "open"
    calls = inner.calls
    with pytest.raises(CircuitOpen):
        await llm.complete(MSGS)
    assert inner.calls == calls

    clock.advance(10.0)
    inner.enqueue(FakeProvider.answered("recovered", ["eid-1"]))
    recovered = await llm.complete(MSGS)
    assert recovered.text == "recovered"
    assert llm.breaker.state == "closed"
    assert inner.calls == calls + 1

    await llm.complete(MSGS)
    assert inner.calls == calls + 2


async def test_retry_that_would_breach_cost_ceiling_degrades():
    """T-29 / R-04: a retry that would breach the ceiling degrades instead."""
    llm, inner, _clock = _wrap(cost_ceiling_usd=1e-12, max_retries=2)
    inner.enqueue(ProviderTimeout())
    inner.enqueue(ProviderTimeout())
    inner.enqueue(ProviderTimeout())

    with pytest.raises(ProviderDegraded) as caught:
        await llm.complete(MSGS)

    assert inner.calls == 1
    assert caught.value.attempts == 1


async def test_half_open_is_single_flight():
    """T-31 / R-05: twenty concurrent calls at the reset window → exactly one probe."""
    llm, inner, clock = _wrap(breaker_failures=1, max_retries=0, breaker_reset_s=5.0)
    inner.enqueue(ProviderUnavailable())
    with pytest.raises(ProviderDegraded):
        await llm.complete(MSGS)
    assert llm.breaker.state == "open"
    tripped = inner.calls

    clock.advance(5.0)
    inner.hold = asyncio.Event()
    inner.entered = asyncio.Event()
    inner.enqueue(FakeProvider.answered("probe-ok", ["eid-1"]))

    tasks = [asyncio.create_task(llm.complete(MSGS)) for _ in range(20)]
    await inner.entered.wait()
    for _ in range(200):
        if sum(1 for task in tasks if task.done()) >= 19:
            break
        await asyncio.sleep(0)
    assert sum(1 for task in tasks if task.done()) == 19
    assert inner.calls == tripped + 1
    for task in tasks:
        if task.done():
            with pytest.raises(CircuitOpen):
                task.result()

    inner.hold.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    successes = [item for item in results if not isinstance(item, BaseException)]
    rejected = [item for item in results if isinstance(item, CircuitOpen)]
    assert len(successes) == 1
    assert successes[0].text == "probe-ok"
    assert len(rejected) == 19
    assert llm.breaker.state == "closed"
    assert inner.calls == tripped + 1


async def test_min_retry_budget_degrades_without_another_call():
    """T-32 / R-04: remaining 0.4s < min_retry_budget_s → no call, degrade immediately."""
    clock = Clock()
    llm, inner, clock = _wrap(clock=clock, min_retry_budget_s=1.5, request_budget_s=6.0)
    with question_budget(now=clock, request_budget_s=6.0):
        clock.t = 5.6
        with pytest.raises(ProviderDegraded) as caught:
            await llm.complete(MSGS)

    assert inner.calls == 0
    assert caught.value.attempts == 0
    assert caught.value.underlying_code == "provider_timeout"
