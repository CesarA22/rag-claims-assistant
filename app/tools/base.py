from typing import Any, Protocol

from app.domain.models import Evidence


class Tool(Protocol):
    name: str
    parameters: dict[str, Any]

    async def run(self, params: dict[str, Any]) -> Evidence: ...
