"""Shared fixtures. Database tests need Postgres from server/docker-compose.yml
(or MEMVINE_TEST_DATABASE_URL); they are skipped when it is not reachable."""
from __future__ import annotations

import asyncio
import os
import uuid

import psycopg
import pytest
import pytest_asyncio

# Configure before the app module reads settings.
os.environ.setdefault("MEMVINE_LLM_BACKEND", "fake")
os.environ.setdefault("MEMVINE_EMBEDDINGS_BACKEND", "fake")
os.environ.setdefault("MEMVINE_WORKER_ENABLED", "false")
os.environ.setdefault("MEMVINE_MANAGEMENT_KEY", "mv_mk_test")

# Tests run in their own database, never the development one: a test that drains the queue
# must not be able to pick up a real run's jobs (it once did, with the fake model).
ADMIN_DSN = os.environ.get("MEMVINE_TEST_ADMIN_URL", "postgresql://memvine:memvine@localhost:5433/memvine")
TEST_DSN = os.environ.get("MEMVINE_TEST_DATABASE_URL", "postgresql://memvine:memvine@localhost:5433/memvine_test")
os.environ["MEMVINE_DATABASE_URL"] = TEST_DSN


def _ensure_test_database() -> None:
    name = TEST_DSN.rsplit("/", 1)[-1]
    with psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=2) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            conn.execute(f'CREATE DATABASE "{name}"')


def _db_available() -> bool:
    try:
        _ensure_test_database()
        with psycopg.connect(TEST_DSN, connect_timeout=2):
            pass
    except Exception:  # noqa: BLE001
        return False
    # The suite's schema comes from the migrations, as a server's does; a migration error fails
    # loudly rather than skipping every test.
    from memvine_server.migrate import migrate_database

    asyncio.run(migrate_database(TEST_DSN))
    return True


@pytest_asyncio.fixture(autouse=True)
async def _fresh_rate_limits():
    """Every test starts with empty rate-limit counters (all tests share one client address)."""
    from memvine_server.security import limiter

    await limiter.reset()
    yield
    await limiter.reset()


needs_db = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable; run `docker compose up -d` in server/")


@pytest_asyncio.fixture
async def client():
    """An httpx client over the ASGI app, with a fresh project and key per test."""
    import httpx

    from memvine_server.main import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            slug = f"t-{uuid.uuid4().hex[:8]}"
            created = await http.post(
                "/v1/projects", json={"name": slug, "slug": slug}, headers={"Authorization": "Bearer mv_mk_test"}
            )
            assert created.status_code == 201, created.text
            key = await http.post(
                f"/v1/projects/{slug}/keys", json={"name": "test"}, headers={"Authorization": "Bearer mv_mk_test"}
            )
            http.headers["Authorization"] = f"Bearer {key.json()['key']}"
            http.project_slug = slug  # type: ignore[attr-defined]
            yield http
            async with app.state.pool.connection() as conn:
                await conn.execute("DELETE FROM projects WHERE slug = %s", (slug,))
