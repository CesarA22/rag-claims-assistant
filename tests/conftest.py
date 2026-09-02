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
