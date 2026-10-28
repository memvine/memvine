"""Connection pool and schema bootstrap.

A single async psycopg pool is shared by the API and the worker. Before it opens, pending schema
migrations are applied (migrate.py).
"""
from __future__ import annotations

import logging
from contextvars import ContextVar

import psycopg
from pgvector.psycopg import register_vector_async
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import settings
from .migrate import MigrationError, migrate, status

log = logging.getLogger("memvine.db")

# The project the current request is confined to, as text; '' for work that spans projects (the
# worker, organization administration). Set by authentication; every connection checked out of
# the pool carries it to Postgres, whose row-level security policies (migrations/rls.sql) enforce it.
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


async def ensure_schema(conn) -> list[str]:
    """Apply pending migrations, or with auto_migrate off refuse to run on a database that has any."""
    if not settings.auto_migrate:
        st = await status(conn)
        if not st.up_to_date:
            raise MigrationError(
                "the database needs schema migrations and MEMVINE_AUTO_MIGRATE is off: "
                "take a backup, then run `memvine-server migrate`")
        return []
    steps = await migrate(conn)
    for step in steps:
        log.warning("schema: %s", step)  # visible in the server log at the default level
    return steps


async def open_pool(dsn: str | None = None) -> AsyncConnectionPool:
    """Migrate the schema, then open the pool.

    Every pooled connection registers the vector type on connect, so the extension (created by
    the first migration) has to exist before the first pooled connection is made; migrations run
    on a plain connection.
    """
    dsn = dsn or settings.database_url
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await ensure_schema(conn)
    pool = make_pool(dsn)
    await pool.open()
    return pool
