from __future__ import annotations

import os

from fastapi import FastAPI

from app.api.errors import TraceIdMiddleware, install_error_handlers
from app.api.routes import router
from app.llm.base import LLMProvider
from app.llm.fake import FakeProvider
from app.retrieval.base import Retriever
from app.retrieval.memory import InMemoryRetriever
from app.storage.base import ConversationRepository
from app.storage.memory import InMemoryConversationRepository


def build_provider(name: str) -> LLMProvider:
    if name == "fake":
        return FakeProvider()
    if name == "chaos":
        raise RuntimeError(
            "LLM_PROVIDER=chaos is not implemented yet (S6). Use LLM_PROVIDER=fake."
        )
    if name == "openai":
        raise RuntimeError(
            "LLM_PROVIDER=openai is not implemented yet (S5). Use LLM_PROVIDER=fake."
        )
    raise RuntimeError(
        f"Unknown LLM_PROVIDER={name!r}. Use fake, chaos, or openai."
    )


def create_app(
    *,
    llm: LLMProvider | None = None,
    retriever: Retriever | None = None,
    repo: ConversationRepository | None = None,
    provider_name: str | None = None,
) -> FastAPI:
    if llm is None:
        name = provider_name or os.getenv("LLM_PROVIDER", "fake")
        provider = build_provider(name)
    else:
        provider = llm
        name = provider_name or getattr(llm, "name", "fake")

    app = FastAPI(title="InsurCo claims assistant")
    app.state.llm = provider
    app.state.retriever = retriever or InMemoryRetriever()
    app.state.repo = repo or InMemoryConversationRepository()
    app.state.provider_name = name
    app.add_middleware(TraceIdMiddleware)
    install_error_handlers(app)
    app.include_router(router)
    return app


app = create_app()
