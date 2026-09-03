"""T-30 / R-05: seeded chaos composes rates; latency is injected, not slept."""

from __future__ import annotations

import random

import pytest

from app.domain.errors import ProviderTimeout, ProviderUnavailable
from app.llm.base import Message
from app.llm.chaos import ChaosConfig, ChaosProvider
from app.llm.fake import FakeProvider

MSGS = [Message(role="user", content="q")]


async def test_seeded_chaos_composes_rates_and_is_reproducible():
    """T-30 / R-05: one draw per call; timeout and 500 rates compose; same seed repeats."""
    cfg = ChaosConfig(timeout_rate=0.3, server_error_rate=0.2, seed=7)

    async def run(provider: ChaosProvider) -> list[str]:
        outcomes: list[str] = []
        for _ in range(40):
            try:
                await provider.complete(MSGS)
                outcomes.append("ok")
            except ProviderTimeout:
                outcomes.append("timeout")
            except ProviderUnavailable:
                outcomes.append("unavailable")
        return outcomes

    first = ChaosProvider(FakeProvider(), cfg)
    second = ChaosProvider(FakeProvider(), cfg)
    a = await run(first)
    b = await run(second)
    assert a == b
    assert a.count("timeout") > 0
    assert a.count("unavailable") > 0
    assert a.count("ok") > 0


class QueueRng(random.Random):
    def __init__(self, values: list[float]) -> None:
        super().__init__(0)
        self._values = iter(values)

    def random(self) -> float:
        return next(self._values)


async def test_scripted_draw_maps_to_timeout_then_500_then_ok():
    """T-30 / R-05: 0.3 + 0.2 compose on one draw, not two independent coins."""
    inner = FakeProvider()
    inner.enqueue(FakeProvider.answered("ok", ["eid"]))
    chaos = ChaosProvider(
        inner,
        ChaosConfig(timeout_rate=0.3, server_error_rate=0.2),
        rng=QueueRng([0.05, 0.4, 0.9]),
    )

    with pytest.raises(ProviderTimeout):
        await chaos.complete(MSGS)
    with pytest.raises(ProviderUnavailable):
        await chaos.complete(MSGS)
    result = await chaos.complete(MSGS)
    assert result.text == "ok"
    assert inner.calls == 1


async def test_injected_latency_does_not_sleep_past_deadline():
    """T-30 / R-05: latency past the handed-down timeout raises ProviderTimeout without sleeping it."""
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    chaos = ChaosProvider(
        FakeProvider(),
        ChaosConfig(latency_ms=400, timeout_rate=0, server_error_rate=0),
        sleep=sleep,
    )
    with pytest.raises(ProviderTimeout):
        await chaos.complete(MSGS, timeout_s=0.05)
    assert slept == []

    chaos_ok = ChaosProvider(
        FakeProvider(),
        ChaosConfig(latency_ms=40, timeout_rate=0, server_error_rate=0),
        sleep=sleep,
    )
    await chaos_ok.complete(MSGS, timeout_s=1.0)
    assert slept == [0.04]
