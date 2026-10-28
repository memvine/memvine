"""The Sources listing reports each episode's synthesis progress: queued, running, a failed
attempt waiting to retry (with its error), failed for good, and done with how much memory came out."""
from __future__ import annotations

import pytest
from conftest import needs_db
from test_e2e import SESSION_1, wire_fake_llm

from memvine_server.worker import MAX_ATTEMPTS, run_one

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}


class Broken:
    """A model that always fails, as a provider outage would."""

    def __init__(self):
        self.fast = self

    async def complete(self, system, user, *, max_tokens=4096):
        raise RuntimeError("provider exploded")


@pytest.mark.asyncio
async def test_episode_listing_reports_synthesis_progress(client):
    from memvine_server.main import app

    listing = lambda: client.get("/v1/episodes", headers={**MGMT, "X-Memvine-Project": client.project_slug})  # noqa: E731
    r = await client.post("/v1/ingest", json={"items": [{"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}]})
    assert r.status_code == 202, r.text
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]

    ep = (await listing()).json()[0]
    assert ep["job_status"] == "queued" and ep["job_error"] is None and ep["job_attempts"] == 0
    assert ep["job_retry_at"] is None and ep["memory_count"] == 0 and ep["thread_count"] == 0

    # A failed attempt goes back to the queue with its error and a time for the next try.
    assert await run_one(app.state.pool, Broken(), app.state.embedder, project_id)
    ep = (await listing()).json()[0]
    assert ep["job_status"] == "queued" and "provider exploded" in ep["job_error"]
    assert ep["job_attempts"] == 1 and ep["job_retry_at"] is not None

    # Out of attempts: failed, with the error kept.
    for _ in range(MAX_ATTEMPTS - 1):
        async with app.state.pool.connection() as conn:
            await conn.execute("UPDATE jobs SET run_after = now() WHERE project_id = %s", (project_id,))
        assert await run_one(app.state.pool, Broken(), app.state.embedder, project_id)
    ep = (await listing()).json()[0]
    assert ep["job_status"] == "failed" and "provider exploded" in ep["job_error"] and ep["job_retry_at"] is None
    assert ep["job_finished_at"] is not None

    # Retried with a working model: done, with the memories it made counted.
    assert (await client.post(f"/v1/jobs/{ep['job_id']}/retry")).status_code == 202
    wire_fake_llm(app)
    assert await run_one(app.state.pool, app.state.llm, app.state.embedder, project_id)
    ep = (await listing()).json()[0]
    assert ep["job_status"] == "done" and ep["job_error"] is None and ep["memory_count"] == 2
    assert ep["job_result"]["candidates"] == 2
