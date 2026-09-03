from __future__ import annotations

import os

from fastapi import FastAPI

from app.api.errors import TraceIdMiddleware, configure_logging, install_error_handlers
from app.api.routes import router
from app.llm.base import LLMProvider
from app.llm.chaos import ChaosConfig, ChaosProvider
from app.llm.fake import FakeProvider
from app.llm.openai_provider import OpenAIProvider
from app.llm.resilient import ResilienceConfig, ResilientProvider
from app.retrieval.base import Retriever
from app.retrieval.memory import InMemoryRetriever
from app.services.budget import Pricing
from app.storage.base import ConversationRepository
from app.storage.memory import InMemoryConversationRepository


def build_inner(name: str) -> LLMProvider:
    if name == "fake":
        return FakeProvider()
    if name == "chaos":
        return ChaosProvider(FakeProvider(), ChaosConfig.from_env())
    if name == "openai":
        return OpenAIProvider()
    raise RuntimeError(
        f"Unknown LLM_PROVIDER={name!r}. Use fake, chaos, or openai."
    )


def build_provider(name: str) -> LLMProvider:
    return ResilientProvider(
        build_inner(name),
        ResilienceConfig.from_env(),
        pricing=Pricing.from_env(),
    )


def create_app(
    *,
    llm: LLMProvider | None = None,
    retriever: Retriever | None = None,
    repo: ConversationRepository | None = None,
    provider_name: str | None = None,
) -> FastAPI:
    configure_logging()
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
