"""Deliberate failure injection. Wraps a real inner provider so a non-failing
call still returns a grounded, citable answer.

One uniform draw per call so CHAOS_TIMEOUT_RATE and CHAOS_500_RATE compose:
0.3 + 0.2 means 50% failures, not 44%.
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.domain.errors import ProviderTimeout, ProviderUnavailable
from app.llm.base import Completion, Message


@dataclass(frozen=True)
class ChaosConfig:
    timeout_rate: float = 0.0
    server_error_rate: float = 0.0
    latency_ms: float = 0.0
    seed: int | None = None

    @classmethod
    def from_env(cls) -> ChaosConfig:
        seed_raw = os.getenv("CHAOS_SEED")
        return cls(
            timeout_rate=float(os.getenv("CHAOS_TIMEOUT_RATE", "0")),
            server_error_rate=float(os.getenv("CHAOS_500_RATE", "0")),
            latency_ms=float(os.getenv("CHAOS_LATENCY_MS", "0")),
            seed=int(seed_raw) if seed_raw else None,
        )


class ChaosProvider:
    name = "chaos"

    def __init__(
        self,
        inner: Any,
        config: ChaosConfig | None = None,
        *,
        rng: random.Random | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.inner = inner
        self.config = config or ChaosConfig.from_env()
        if rng is not None:
            self._rng = rng
        elif self.config.seed is not None:
            self._rng = random.Random(self.config.seed)
        else:
            self._rng = random.Random()
        self._sleep = sleep or asyncio.sleep

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        max_output_tokens: int | None = None,
    ) -> Completion:
        delay_s = self.config.latency_ms / 1000.0
        if delay_s > 0:
            if timeout_s is not None and delay_s > timeout_s:
                raise ProviderTimeout()
            await self._sleep(delay_s)

        draw = self._rng.random()
        if draw < self.config.timeout_rate:
            raise ProviderTimeout()
        if draw < self.config.timeout_rate + self.config.server_error_rate:
            raise ProviderUnavailable()

        return await self.inner.complete(
            messages,
            schema=schema,
            timeout_s=timeout_s,
            max_output_tokens=max_output_tokens,
        )
