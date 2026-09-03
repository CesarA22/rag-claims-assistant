from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from pgvector.asyncpg import register_vector
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

DEFAULT_DATABASE_URL = "postgresql://insurco:insurco@localhost:5433/insurco"

# Two pools per process reach one Postgres: this engine (conversation store) and
# the asyncpg pool below (retrieval). Sizes are explicit because the defaults are
# not survivable at four workers — SQLAlchemy defaults to 5 + 10 overflow and
# asyncpg to 10, which is ~100 connections against a default max_connections of
# 100. Twenty concurrent questions is exactly when that would be discovered.
ENGINE_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
ENGINE_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "5"))
ASYNCPG_MIN_SIZE = int(os.getenv("DB_ASYNCPG_MIN_SIZE", "2"))
ASYNCPG_MAX_SIZE = int(os.getenv("DB_ASYNCPG_MAX_SIZE", "10"))


def database_url() -> str:
    """Bare DSN, for `asyncpg.connect` and `asyncpg.create_pool`."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def alembic_url() -> str:
    """The same DSN with the async driver SQLAlchemy needs.

    One source of truth: alembic.ini deliberately does not carry a URL, so a
    migration and the running app cannot disagree about which database they mean.
    """
    url = database_url()
    if url.startswith("postgresql+"):
        return url
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def create_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(
        url or alembic_url(),
        pool_size=ENGINE_POOL_SIZE,
        max_overflow=ENGINE_MAX_OVERFLOW,
        pool_pre_ping=True,
    )


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def create_pool(url: str | None = None) -> asyncpg.Pool:
    """Retrieval's asyncpg pool.

    Applies no schema. Alembic owns every table, including `chunks` — two schema
    tools on one database is how environments drift.
    """
    return await asyncpg.create_pool(
        url or database_url(),
        init=_init_connection,
        min_size=ASYNCPG_MIN_SIZE,
        max_size=ASYNCPG_MAX_SIZE,
    )


@asynccontextmanager
async def connect(url: str | None = None) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(url)
    try:
        yield pool
    finally:
        await pool.close()
