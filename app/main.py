from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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

# Before anything reads the environment. `app = create_app()` at the bottom of
# this module runs at import, and with LLM_PROVIDER=openai and the key only in
# .env that construction raised inside the OpenAI SDK — uvicorn died with a
# message that looked nothing like "missing key". load_dotenv never overrides a
# variable the environment already set, so compose and CI stay authoritative.
load_dotenv()


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


async def build_retriever(arm: str) -> tuple[Any, Retriever]:
    """The asyncpg pool and a HybridRetriever over it.

    Returns both because the caller owns closing the pool. Lexical is the
    default arm: the vector arm costs an embedding call per question and buys
    nothing the ambiguity gate or the state demo needs.
    """
    from app.retrieval.embeddings import EmbeddingCache
    from app.retrieval.hybrid import HybridRetriever
    from app.storage.db import create_pool

    if arm not in ("lexical", "vector", "hybrid"):
        raise RuntimeError(
            f"Unknown RETRIEVER_ARM={arm!r}. Use lexical, vector, or hybrid."
        )
    embed_query = None if arm == "lexical" else EmbeddingCache().embed_one
    pool = await create_pool()
    return pool, HybridRetriever(pool, embed_query, arm=arm)


def build_repo(name: str) -> tuple[ConversationRepository, object | None]:
    """Returns the repository and the engine to dispose on shutdown, if any."""
    if name == "memory":
        return InMemoryConversationRepository(), None
    if name == "sql":
        from app.storage.db import create_engine, session_factory
        from app.storage.sql import SqlConversationRepository

        engine = create_engine()
        return SqlConversationRepository(session_factory(engine)), engine
    raise RuntimeError(f"Unknown STORAGE={name!r}. Use memory or sql.")


def create_app(
    *,
    llm: LLMProvider | None = None,
    retriever: Retriever | None = None,
    repo: ConversationRepository | None = None,
    provider_name: str | None = None,
    storage: str | None = None,
) -> FastAPI:
    configure_logging()
    if llm is None:
        name = provider_name or os.getenv("LLM_PROVIDER", "fake")
        provider = build_provider(name)
    else:
        provider = llm
        name = provider_name or getattr(llm, "name", "fake")

    storage_name = storage or os.getenv("STORAGE", "memory")
    engine = None
    if repo is None:
        repo, engine = build_repo(storage_name)
    elif storage is None:
        storage_name = "memory"

    # memory by default so `pytest --disable-socket` and a keyless clone still
    # start. hybrid is what the gs-009 gate needs: InMemoryRetriever holds two
    # chunks spanning one product, so grounding.is_ambiguous can never fire.
    retriever_name = "memory" if retriever is not None else os.getenv("RETRIEVER", "memory")
    if retriever_name not in ("memory", "hybrid"):
        raise RuntimeError(f"Unknown RETRIEVER={retriever_name!r}. Use memory or hybrid.")

    app = FastAPI(title="InsurCo claims assistant")
    app.state.llm = provider
    app.state.retriever = retriever or InMemoryRetriever()
    app.state.repo = repo
    app.state.provider_name = name
    app.state.storage = storage_name
    app.state.engine = engine
    app.state.retriever_name = retriever_name
    app.state.pool = None

    if retriever_name == "hybrid":
        # create_pool is async and create_app is not, so the pool is built on
        # startup rather than at construction. app.state.retriever is replaced
        # in place; nothing reads it before startup completes.
        @app.on_event("startup")
        async def _open_pool() -> None:
            app.state.pool, app.state.retriever = await build_retriever(
                os.getenv("RETRIEVER_ARM", "lexical")
            )

        @app.on_event("shutdown")
        async def _close_pool() -> None:
            if app.state.pool is not None:
                await app.state.pool.close()

    if engine is not None:
        @app.on_event("shutdown")
        async def _dispose_engine() -> None:
            await engine.dispose()

    app.add_middleware(TraceIdMiddleware)
    install_error_handlers(app)
    app.include_router(router)
    return app


DEFAULT_WEB_DIST = "web/dist"


def mount_client(api: FastAPI, dist: Path | None = None) -> FastAPI:
    """A shell serving `api` under /api and the built client at /.

    `web/src/api.ts` hardcodes `BASE = '/api'` and the Vite dev proxy strips that
    prefix before forwarding (`vite.config.ts`), so the client asks for
    /api/healthz and the backend serves /healthz. Mounting reproduces that
    contract exactly: no route in `app/api/routes.py` moves, and the tests that
    drive `create_app()` keep hitting /healthz directly.

    Two things a mount does not give you for free, both load-bearing here:

    - **Lifespan.** Starlette routes only HTTP and websocket scopes into a
      mounted app; the lifespan scope never reaches it, so `create_app()`'s
      startup hook would never run and `state.retriever` would stay the
      two-chunk `InMemoryRetriever` while the app looked healthy. The shell
      therefore drives the child's lifespan explicitly. T-44 pins this.
    - **The order of the mounts.** `/api` is registered before `/`, because
      Starlette matches routes in order and a StaticFiles mount at / would
      otherwise swallow every API path.

    Middleware and exception handlers *are* kept — the child is a full ASGI app,
    so `TraceIdMiddleware` and the RFC 9457 handlers still run, and
    `request.app.state` inside it resolves to the child, which is where
    `state.llm`, `state.repo` and `state.pool` live.
    """

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with api.router.lifespan_context(api):
            yield

    # The shell carries no routes of its own, so its default /docs, /redoc and
    # /openapi.json would serve an empty schema — and they are registered before
    # the mounts, so they would shadow the client at those three paths. Off. The
    # API's own docs are where the API is, at /api/docs.
    shell = FastAPI(
        title="InsurCo claims assistant",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    shell.mount("/api", api)
    directory = dist or Path(os.getenv("WEB_DIST", DEFAULT_WEB_DIST))
    if directory.is_dir():
        # html=True serves index.html at /. The client has no router, so there
        # are no deep links needing an SPA fallback.
        shell.mount("/", StaticFiles(directory=directory, html=True), name="client")
    return shell


def create_container_app() -> FastAPI:
    """The single-origin app the image runs: `uvicorn --factory`.

    A factory rather than a module-level object on purpose. `app` below is
    already built at import, and a second module-level app would build a second
    provider and a second SQLAlchemy engine in every process that imports
    `app.main` — which is every test in the suite. This way `import app.main`
    costs exactly what it costs today.
    """
    return mount_client(create_app())


app = create_app()
