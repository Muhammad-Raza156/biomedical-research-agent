"""LangGraph checkpoints in Postgres (one pool per worker process)."""

from __future__ import annotations

import asyncio
import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)


async def make_checkpointer(dsn: str, max_size: int = 10) -> tuple[AsyncPostgresSaver, AsyncConnectionPool]:
    """Open a pool, create checkpoint tables if needed (retrying if two workers race), return both."""
    pool = AsyncConnectionPool(conninfo=dsn, max_size=max_size, open=False,
                               kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    await pool.open(wait=True)
    saver = AsyncPostgresSaver(pool)
    for attempt in range(5):
        try:
            await saver.setup()      # idempotent migrations
            break
        except Exception as exc:     # e.g. both replicas creating the migrations table at once
            if attempt == 4:
                raise
            log.warning("checkpointer setup failed (%s); retrying", exc)
            await asyncio.sleep(1 + attempt)
    return saver, pool
