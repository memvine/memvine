"""Synthesis health (health.py): failed jobs, a stuck queue and a silent worker are reported by
GET /v1/health/synthesis and posted to the alert webhook at most once per repeat window."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from conftest import needs_db

from memvine_server import health
from memvine_server.config import settings

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}


async def project_id(conn, slug):
    return (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]


async def add_job(conn, pid, episode, status, *, minutes_ago=0, background=False):
    await conn.execute(
        "INSERT INTO jobs (project_id, kind, episode_id, status, attempts, created_at, finished_at, background) "
        "VALUES (%s, 'extract', %s, %s, 3, now() - make_interval(mins => %s), "
        "CASE WHEN %s = 'failed' THEN now() - make_interval(mins => %s) END, %s)",
        (pid, episode, status, minutes_ago, status, minutes_ago, background))


async def worker_polled(conn, minutes_ago):
    await conn.execute("DELETE FROM worker_heartbeats")
    await conn.execute(
        "INSERT INTO worker_heartbeats (worker_id, host, pid, last_poll_at) VALUES ('test', 'test', 1, now() - make_interval(mins => %s))",
        (minutes_ago,))


async def test_health_reports_failed_stuck_and_silent_worker(client):
    from memvine_server.main import app

    slug = client.project_slug
    get = lambda: client.get("/v1/health/synthesis", params={"project": slug}, headers=MGMT)  # noqa: E731
    async with app.state.pool.connection() as conn:
        pid = await project_id(conn, slug)
        await worker_polled(conn, 1)

        r = await get()
        assert r.status_code == 200, r.text
        assert r.json()["healthy"] is True and r.json()["issues"] == [] and r.json()["scope"] == "project"

        # Three sources failed today (one twice), one failed two days ago: three count.
        for ep in ("a", "b", "c", "c"):
            await add_job(conn, pid, ep, "failed", minutes_ago=30)
        await add_job(conn, pid, "old", "failed", minutes_ago=48 * 60)
        # A live job waiting 47 minutes is stuck; an imported one waiting longer is not.
        await add_job(conn, pid, "waiting", "queued", minutes_ago=47)
        await add_job(conn, pid, "imported", "queued", minutes_ago=300, background=True)

        body = (await get()).json()
        assert body["healthy"] is False
        issues = {i["condition"]: i for i in body["issues"]}
        assert set(issues) == {"failed", "stuck"}
        assert issues["failed"]["message"] == "3 sources failed to synthesize in the last day — see Sources"
        assert issues["stuck"]["message"] == "Synthesis is stuck: the oldest source has waited 47 min"
        assert body["failed_count"] == 3 and 46 < body["oldest_queued_minutes"] < 49

        # The operator's server-wide view names the project.
        wide = (await client.get("/v1/health/synthesis", headers=MGMT)).json()
        assert wide["scope"] == "server"
        assert any(slug in [p["slug"] for p in i.get("projects", [])] for i in wide["issues"])

        # A paused project's queue waits on purpose.
        await conn.execute("UPDATE projects SET processing_paused = true WHERE id = %s", (pid,))
        assert "stuck" not in {i["condition"] for i in (await get()).json()["issues"]}

        # The worker stopped polling 20 minutes ago (threshold 10).
        await worker_polled(conn, 20)
        worker = next(i for i in (await get()).json()["issues"] if i["condition"] == "worker")
        assert worker["message"] == "The synthesis worker has not polled for 20 min"
        await conn.execute("DELETE FROM worker_heartbeats")
        worker = next(i for i in (await get()).json()["issues"] if i["condition"] == "worker")
        assert worker["message"] == "No synthesis worker has polled the queue yet"


async def test_health_needs_a_manager(client):
    # A project key is not a manager of anything.
    r = await client.get("/v1/health/synthesis")
    assert r.status_code == 403
    r = await client.get("/v1/health/synthesis", params={"project": "no-such-project"}, headers=MGMT)
    assert r.status_code == 404


async def test_worker_heartbeat(client):
    from memvine_server.main import app

    async with app.state.pool.connection() as conn:
        await conn.execute("DELETE FROM worker_heartbeats")
        await health.beat(app.state.pool, force=True)
        row = await (await conn.execute("SELECT worker_id, pid FROM worker_heartbeats")).fetchone()
        assert row["worker_id"] == health.WORKER_ID
        body = (await client.get("/v1/health/synthesis", params={"project": client.project_slug}, headers=MGMT)).json()
        assert body["healthy"] is True and body["worker_last_poll_at"]


class Stub:
    """A local stand-in for a Slack incoming webhook."""

    def __init__(self):
        self.posts: list[dict] = []
        self.status = 200
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                stub.posts.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(stub.status)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


async def test_webhook_posts_each_condition_once_per_hour(client, stub, monkeypatch):
    from memvine_server.main import app

    monkeypatch.setattr(settings, "alert_webhook_url", stub.url)
    async with app.state.pool.connection() as conn:
        await conn.execute("DELETE FROM alert_state")
        await worker_polled(conn, 0)
        pid = await project_id(conn, client.project_slug)
        await add_job(conn, pid, "x", "failed", minutes_ago=5)

        assert "failed" in await health.check_and_alert(conn)
        texts = [p["text"] for p in stub.posts]
        assert any(t.startswith("Memvine: ") and "failed to synthesize" in t and "see Sources" in t for t in texts)

        # Still failing a minute later: not posted again within the hour.
        before = len(stub.posts)
        assert "failed" not in await health.check_and_alert(conn)
        assert len(stub.posts) == before

        # An hour on, it is posted again.
        await conn.execute("UPDATE alert_state SET last_sent_at = now() - interval '61 minutes'")
        assert "failed" in await health.check_and_alert(conn)

        # A webhook that refuses the post is retried at the next check, not an hour later.
        await conn.execute("DELETE FROM alert_state")
        stub.status = 500
        assert "failed" not in await health.check_and_alert(conn)
        stub.status = 200
        assert "failed" in await health.check_and_alert(conn)
        await conn.execute("DELETE FROM alert_state")


async def test_no_webhook_no_post(client, stub, monkeypatch):
    from memvine_server.main import app

    monkeypatch.setattr(settings, "alert_webhook_url", "")
    async with app.state.pool.connection() as conn:
        assert await health.check_and_alert(conn) == []
    assert stub.posts == []
