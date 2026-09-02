from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from pgvector.asyncpg import register_vector

_MIGRATIONS = Path(__file__).resolve().parent / "migrations"

DEFAULT_DATABASE_URL = "postgresql://insurco:insurco@localhost:5433/insurco"


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def apply_migrations(url: str | None = None) -> None:
    sql = (_MIGRATIONS / "001_chunks.sql").read_text(encoding="utf-8")
    conn = await asyncpg.connect(url or database_url())
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def create_pool(url: str | None = None) -> asyncpg.Pool:
    dsn = url or database_url()
    await apply_migrations(dsn)
    return await asyncpg.create_pool(dsn, init=_init_connection)


@asynccontextmanager
async def connect(url: str | None = None) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(url)
    try:
        yield pool
    finally:
        await pool.close()
