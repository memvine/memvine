"""Instant ingest: one small episode learned from within the request, returned with its edges.

The fake model stands in for a real one: these tests prove the pipeline (thread page, knowledge,
reconcile, edges, billing, the job not running twice). Whether a real model gets three facts and
an edge out of the console's prefilled snippet is checked by hand against a live server."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import pytest
from conftest import needs_db

from memvine_server.billing import ingest_credits
from memvine_server.extraction.llm import FakeLLM, LLMError
from memvine_server.extraction.prompts import KNOWLEDGE_SYSTEM, THREADS_SYSTEM

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}

# The console's prefilled paste snippet (console/lib/paste-snippet.ts); keep the two the same.
SNIPPET = """Standup, Tue 27 Oct 2026

Sam: The nightly invoice export kept timing out on large accounts, so I capped export retries at 3 per batch.
Priya: Why 3?
Sam: The billing API rate-limits us to 100 requests a minute. Past 3 retries the batches just queue behind the limit, so each export retry now waits 30 seconds before it runs.
Priya: Good. Then the release moves from Thu 29 Oct to Tue 3 Nov, so finance gets a week to check invoices from the new export.
Sam: I'll keep the old CSV exporter as a fallback until 3 Nov; two customers still pull from it.
Priya: From now on any change that touches billing gets a review from Dana before merge."""

CAP = "Invoice export retries are capped at 3 per batch."
WAIT = "Each invoice export retry waits 30 seconds, so the 3 retries per batch stay under the billing API rate limit."
THREADS = json.dumps({"threads": [{
    "key": "topic:invoice-export", "kind": "topic", "title": "Invoice export reliability", "status": "open",
    "summary": "The nightly invoice export timed out on large accounts; retries are capped and spaced out.",
    "sections": [{"label": "Decisions", "items": [{"text": "Cap export retries at 3 per batch", "date": "2026-10-27",
                                                   "evidence": [{"episode_id": None, "quote": "capped export retries at 3 per batch"}]}]}],
    "aliases": [], "related": []}]})


def knowledge(statement, category, quote, subjects, thread=None, why=""):
    assert quote in SNIPPET, quote
    return {"statement": statement, "category": category, "why": why, "thread_key": thread, "scope": "project",
            "subjects": subjects, "assertion": "stated", "confidence": "high", "valid_from": "2026-10-27", "quote": quote}


KNOWLEDGE = json.dumps([
    knowledge(CAP, "decision", "capped export retries at 3 per batch", ["export retries"], "topic:invoice-export",
              why="more retries only queue behind the billing API's rate limit"),
    knowledge(WAIT, "fact", "each export retry now waits 30 seconds before it runs", ["export retries"], "topic:invoice-export"),
    knowledge("The release moved from Thu 29 Oct to Tue 3 Nov 2026 so finance can check invoices from the new export.",
              "decision", "the release moves from Thu 29 Oct to Tue 3 Nov", ["release"]),
    knowledge("The old CSV exporter stays as a fallback until 3 Nov 2026 because two customers still pull from it.",
              "decision", "keep the old CSV exporter as a fallback until 3 Nov", ["csv exporter"]),
    knowledge("Any change that touches billing needs a review from Dana before merge.", "rule",
              "any change that touches billing gets a review from Dana before merge", ["billing"]),
])


@dataclass
class Router:
    """One fake per step, chosen by system prompt. The reconcile judge says the wait detail extends
    the retry cap, and that anything else is different."""
    threads: FakeLLM = field(default_factory=lambda: FakeLLM(default=THREADS))
    knowledge: FakeLLM = field(default_factory=lambda: FakeLLM(default=KNOWLEDGE))
    judged: list[str] = field(default_factory=list)
    fail: bool = False

    async def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        if self.fail:
            raise LLMError("model unavailable")
        if system == THREADS_SYSTEM:
            return await self.threads.complete(system, user)
        if system == KNOWLEDGE_SYSTEM:
            return await self.knowledge.complete(system, user)
        self.judged.append(user)
        if f"NEW (2026-10-27): {WAIT}" in user:
            for n, statement in re.findall(r"EXISTING (\d+) \([^)]*\): (.*)", user):
                if statement == CAP:
                    return json.dumps({"match": int(n), "relation": "extends", "reason": "adds the wait to the cap"})
        return json.dumps({"match": None, "relation": "different", "reason": "distinct"})


@pytest.fixture
async def billing(client):
    from memvine_server.main import app

    r = await client.patch(f"/v1/projects/{client.project_slug}", headers=MGMT,
                           json={"description": "billing: invoices, exports and releases"})
    assert r.status_code == 200
    saved, router = app.state.llm, Router()
    app.state.llm = router
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    try:
        yield router, project_id
    finally:
        app.state.llm = saved


def episode(content=SNIPPET, id="paste-1"):
    return {"id": id, "content": content, "timestamp": "2026-10-27T09:30:00Z", "label": "Pasted in the console"}


async def credits(project_id):
    from memvine_server.main import app

    async with app.state.pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT coalesce(sum(credits), 0) AS c, count(*) AS n FROM usage_events WHERE project_id = %s AND kind = 'ingest'",
            (project_id,))).fetchone()
    return row["c"], row["n"]


@pytest.mark.asyncio
async def test_instant_ingest_returns_facts_with_edges_and_updates_the_thread_page(client, billing):
    from memvine_server.main import app
    from memvine_server.worker import drain

    router, project_id = billing
    r = await client.post("/v1/ingest", json={"items": [episode()], "instant": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == [{"id": "paste-1", "outcome": "processed"}] and body["queued"] == 0
    assert body["instant"]["status"] == "done" and body["instant"]["reason"] is None
    memories = body["instant"]["memories"]
    statements = [m["memory"]["statement"] for m in memories]
    assert len(memories) >= 3 and len(memories) == 5, statements
    by = {m["memory"]["statement"]: m for m in memories}

    # The wait extends the cap; the cap comes first, so both ends are drawn before the edge.
    wait, cap = by[WAIT], by[CAP]
    assert statements.index(CAP) < statements.index(WAIT)
    assert [(e["kind"], e["direction"], e["item"]["id"]) for e in wait["edges"] if e["item"]] == [
        ("extends", "out", cap["memory"]["id"])]
    assert ("extends", "in", wait["memory"]["id"]) in [(e["kind"], e["direction"], e["item"]["id"]) for e in cap["edges"] if e["item"]]
    assert cap["memory"]["category"] == "decision" and cap["memory"]["why"]
    assert cap["memory"]["evidence"][0]["quote"] == "capped export retries at 3 per batch"
    # The thread page was written in the same request, and the facts hang off it.
    thread = next(e["thread"] for e in cap["edges"] if e["kind"] == "decided_in")
    assert thread["title"] == "Invoice export reliability"
    threads = (await client.get("/v1/threads")).json()
    assert [t["title"] for t in threads] == ["Invoice export reliability"]

    # The episode's job is done and the worker never runs it again.
    jobs = (await client.get("/v1/jobs")).json()["jobs"]
    extract = [j for j in jobs if j["episode_id"] == "paste-1"]
    assert len(extract) == 1 and extract[0]["status"] == "done" and extract[0]["result"]["instant"], jobs
    calls = len(router.knowledge.calls)
    await drain(app.state.pool, router, app.state.embedder, project_id)
    assert len(router.knowledge.calls) == calls, "the queued job was not run a second time"
    assert len((await client.get("/v1/memories")).json()) == 5

    # Billed exactly as a queued episode of the same size.
    assert await credits(project_id) == (ingest_credits(len(SNIPPET)), 1)

    # Re-sending the same text is free, runs no model, and says what it already taught.
    again = await client.post("/v1/ingest", json={"items": [episode()], "instant": True})
    assert again.status_code == 200
    assert again.json()["items"] == [{"id": "paste-1", "outcome": "unchanged"}]
    assert [m["memory"]["id"] for m in again.json()["instant"]["memories"]] == [m["memory"]["id"] for m in memories]
    assert len(router.knowledge.calls) == calls and await credits(project_id) == (ingest_credits(len(SNIPPET)), 1)


@pytest.mark.asyncio
async def test_instant_is_for_one_small_episode(client, billing):
    _, project_id = billing
    two = await client.post("/v1/ingest", json={"items": [episode(), episode(id="paste-2")], "instant": True})
    assert two.status_code == 400 and two.json()["error"]["code"] == "instant-too-large"
    big = await client.post("/v1/ingest", json={"items": [episode("x " * 2001)], "instant": True})
    assert big.status_code == 400 and "4,000" in big.json()["error"]["message"]
    edge = await client.post("/v1/ingest", json={"items": [episode("y" * 4000)], "instant": True})
    assert edge.status_code == 200, "4,000 characters is still instant"
    assert edge.json()["instant"]["memories"] == [], "nothing in it is quoted, so nothing is kept"
    _, charged = await credits(project_id)
    assert charged == 1, "refused requests are not billed"


@pytest.mark.asyncio
async def test_instant_falls_back_to_the_queue(client, billing):
    from memvine_server.main import app
    from memvine_server.worker import drain

    router, project_id = billing
    router.fail = True
    r = await client.post("/v1/ingest", json={"items": [episode()], "instant": True})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["items"] == [{"id": "paste-1", "outcome": "queued"}] and body["queued"] == 1
    assert body["instant"]["status"] == "queued" and "model unavailable" in body["instant"]["reason"]
    assert body["instant"]["memories"] == []
    assert (await client.get("/v1/memories")).json() == [] and (await client.get("/v1/threads")).json() == []
    # The episode and its job survived the failed attempt; the worker picks it up as usual.
    router.fail = False
    assert await drain(app.state.pool, router, app.state.embedder, project_id) >= 1
    assert len((await client.get("/v1/memories")).json()) == 5

    paused = await client.patch(f"/v1/projects/{client.project_slug}", headers=MGMT, json={"processing_paused": True})
    assert paused.status_code == 200
    r = await client.post("/v1/ingest", json={"items": [episode("Priya: the export moved to the new billing API.", "paste-2")],
                                              "instant": True})
    assert r.status_code == 202 and r.json()["instant"]["status"] == "queued" and "paused" in r.json()["instant"]["reason"]


@pytest.mark.asyncio
async def test_without_instant_nothing_changes(client):
    r = await client.post("/v1/ingest", json={"items": [episode()]})
    assert r.status_code == 202
    assert r.json() == {"items": [{"id": "paste-1", "outcome": "queued"}], "queued": 1, "instant": None}
