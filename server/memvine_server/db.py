"""Connection pool and schema bootstrap.

A single async psycopg pool is shared by the API and the worker. The schema is
plain SQL applied with IF NOT EXISTS, which is enough while nothing is deployed;
migrations come when the first real database exists.
"""
from __future__ import annotations

import hashlib
import logging
from contextvars import ContextVar
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector_async
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import settings

SCHEMA = Path(__file__).with_name("schema.sql").read_text()
SCHEMA_HASH = hashlib.sha256(SCHEMA.encode()).hexdigest()
log = logging.getLogger("memvine.db")

# The project the current request is confined to, as text; '' for work that spans projects (the
# worker, organization administration). Set by authentication; every connection checked out of
# the pool carries it to Postgres, whose row-level security policies (schema.sql) enforce it.
request_project: ContextVar[str] = ContextVar("memvine_request_project", default="")
_rls_warned = False


async def _configure(conn) -> None:
    global _rls_warned
    await register_vector_async(conn)
    if not settings.row_security:
        return
    try:
        await conn.execute("SET ROLE memvine_app")
    except psycopg.Error:
        # No role (the database user may not create roles): queries still filter by project, but
        # nothing beneath them does.
        if not _rls_warned:
            log.warning("row-level security is off: role memvine_app is missing")
            _rls_warned = True


class ScopedPool(AsyncConnectionPool):
    """Hands out connections tagged with the request's project (see request_project)."""

    async def getconn(self, timeout: float | None = None):
        conn = await super().getconn(timeout=timeout)
        try:
            await conn.execute("SELECT set_config('memvine.project_id', %s, false)", (request_project.get(),))
        except BaseException:
            await self.putconn(conn)
            raise
        return conn


def make_pool(dsn: str | None = None) -> AsyncConnectionPool:
    return ScopedPool(
        dsn or settings.database_url,
        min_size=1,
        max_size=16,  # worker loops hold one each for a job's duration; the rest serve requests
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True},
        configure=_configure,
    )


async def apply_schema(conn) -> bool:
    """Apply schema.sql unless this exact text was applied before.

    CREATE INDEX IF NOT EXISTS takes a table lock before it checks for the index, so
    re-applying an unchanged schema would wait behind every running synthesis job.
    The hash of the applied text is kept, and an unchanged schema is skipped outright.
    """
    await conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (hash text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    row = await (await conn.execute("SELECT 1 FROM schema_meta WHERE hash = %s", (SCHEMA_HASH,))).fetchone()
    if row:
        return False
    await conn.execute(SCHEMA)
    await conn.execute("INSERT INTO schema_meta (hash) VALUES (%s) ON CONFLICT DO NOTHING", (SCHEMA_HASH,))
    return True


async def open_pool(dsn: str | None = None) -> AsyncConnectionPool:
    """Create extensions and schema, then open the pool.

    Every pooled connection registers the vector type on connect, so the
    extension has to exist before the first pooled connection is made; that
    first statement runs on a plain connection.
    """
    dsn = dsn or settings.database_url
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await apply_schema(conn)
    pool = make_pool(dsn)
    await pool.open()
    return pool
