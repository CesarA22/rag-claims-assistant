import pytest
from httpx import ASGITransport, AsyncClient

from app.llm.fake import FakeProvider
from app.main import create_app
from app.retrieval.memory import InMemoryRetriever
from app.storage.memory import InMemoryConversationRepository


@pytest.fixture
def llm() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def retriever() -> InMemoryRetriever:
    return InMemoryRetriever()


@pytest.fixture
def repo() -> InMemoryConversationRepository:
    return InMemoryConversationRepository()


@pytest.fixture(
    params=[
        "memory",
        # Marked db so the default socket-disabled run deselects it outright
        # rather than paying a connection timeout to skip.
        pytest.param("sql", marks=pytest.mark.db),
    ]
)
async def repos(request):
    """The ConversationRepository contract, driven through both implementations.

    `memory` runs in the default socket-disabled suite. `sql` needs Postgres, so
    it is skipped unless the database is reachable — the `db` marker already
    deselects it by default, and this keeps `-m db` honest on a machine where the
    container is not up.
    """
    if request.param == "memory":
        yield InMemoryConversationRepository()
        return

    pytest.importorskip("sqlalchemy")
    from sqlalchemy import text

    from app.storage.db import create_engine, session_factory
    from app.storage.sql import SqlConversationRepository

    engine = create_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any driver error means "no database"
        await engine.dispose()
        pytest.skip(f"postgres unreachable: {type(exc).__name__}")

    sessions = session_factory(engine)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE conversations CASCADE"))
    try:
        yield SqlConversationRepository(sessions)
    finally:
        await engine.dispose()


@pytest.fixture
def app(llm: FakeProvider, retriever: InMemoryRetriever, repo: InMemoryConversationRepository):
    return create_app(llm=llm, retriever=retriever, repo=repo, provider_name="fake")


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as async_client:
        yield async_client
