"""Print how many chunks the index holds, so a shell script can branch on it.

The container entrypoint needs an ingest-if-empty guard and `sh` should not be
parsing Python. Exits non-zero on any failure — a crash here must stop the boot,
not be read as "not zero, so the index is populated".
"""

from __future__ import annotations

import asyncio

import asyncpg

from app.storage.db import database_url


async def count() -> int:
    conn = await asyncpg.connect(database_url())
    try:
        return await conn.fetchval("SELECT count(*) FROM chunks")
    finally:
        await conn.close()


def main() -> int:
    print(asyncio.run(count()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
