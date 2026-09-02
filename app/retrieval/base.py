from typing import Protocol

from app.domain.models import Evidence


class Retriever(Protocol):
    async def search(
        self,
        query: str,
        *,
        product: str | None = None,
        k: int = 5,
    ) -> list[Evidence]: ...
