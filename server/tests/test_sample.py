"""Sample data: loaded instantly with no model call and no charge, shown as sources, recallable with
its supersede chain and contradiction, idempotent, and gone in one delete. Passes with the fake
embedder and with MEMVINE_EMBEDDINGS_BACKEND=fastembed (where the Playground's try-queries are
also checked against it)."""
from __future__ import annotations

import pytest
from conftest import needs_db

from memvine_server.sample import sample_size

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}

# The console Playground's try-queries (console/app/playground/RecallForm.tsx) and the memory each must rank first.
TRY_QUERIES = {
    "What is in release 4.2.0?": "Release 4.2.0 ships",
    "Who merges release work?": "Only Priya merges release work",
    "Why did we drop InvoiceFieldAliases?": "Drop InvoiceFieldAliases",
    "When is the launch?": "The public launch moved to",
    "How do escalations work?": "Escalations go to the #support-tier2",
}


class NoLLM:
    """Any model call fails the test: the sample must not need one."""

    def __init__(self):
        self.fast = self

    async def complete(self, system, user, *, max_tokens=4096):
        raise AssertionError("loading the sample called the model")


def as_manager(client):
    return {**MGMT, "X-Memvine-Project": client.project_slug}


async def project(client):
    return next(p for p in (await client.get("/v1/projects", headers=MGMT)).json() if p["slug"] == client.project_slug)


@pytest.mark.asyncio
async def test_sample_loads_recalls_and_deletes(client):
    from memvine_server.main import app

    sources, memories = sample_size()
    llm, app.state.llm = app.state.llm, NoLLM()
    try:
        r = await client.post("/v1/sample", headers=as_manager(client))
    finally:
        app.state.llm = llm
    assert r.status_code == 200, r.text
    assert r.json() == {"loaded": True, "sources": sources, "memories": memories}

    # Free: no credits charged. Marked: every source is a sample, already synthesized, with its memories.
    p = await project(client)
    assert float(p["month_credits"]) == 0
    assert p["source_count"] == sources and p["sample_source_count"] == sources and p["memory_count"] == memories
    listed = (await client.get("/v1/episodes", headers=as_manager(client))).json()
    assert len(listed) == sources and all(e["id"].startswith("sample:") and e["label"].startswith("Sample") for e in listed)
    assert all(e["job_status"] == "done" and e["memory_count"] >= 1 for e in listed)
    assert (await client.get("/v1/jobs")).json()["pending"] == 0, "nothing queued for synthesis"

    # The supersede chain: the old launch date is superseded by the new one.
    items = (await client.get("/v1/memories")).json()
    assert len(items) == memories
    new = next(m for m in items if m["statement"].startswith("The public launch moved to"))
    old = next(m for m in items if m["statement"].startswith("The public launch is planned for"))
    assert new["status"] == "active" and new["supersedes"] == old["id"]
    assert old["status"] == "superseded" and old["superseded_by"] == new["id"] and old["valid_to"] is not None
    assert new["evidence"][0]["episode_id"] == "sample:launch-update"
    # The contradiction: both stay active, each pointing at the other.
    wait = next(m for m in items if m["statement"].startswith("Weekend escalations wait"))
    page = next(m for m in items if m["statement"].startswith("Weekend escalations page"))
    assert wait["status"] == page["status"] == "active"
    assert wait["conflicts_with"] == [page["id"]] and page["conflicts_with"] == [wait["id"]]

    # Recall returns the new value, with the old one as `previous`, and never the superseded item itself.
    r = (await client.post("/v1/recall", json={"query": "When is the launch?"})).json()
    assert not r["abstained"]
    assert r["items"][0]["memory"]["id"] == new["id"] and r["items"][0]["previous"] == old["statement"]
    assert old["id"] not in {i["memory"]["id"] for i in r["items"]}
    assert "Never deploy on Fridays." in r["standing"]

    # Loading again is a no-op.
    r = await client.post("/v1/sample", headers=as_manager(client))
    assert r.status_code == 200 and r.json()["loaded"] is False
    assert len((await client.get("/v1/memories")).json()) == memories
    assert len((await client.get("/v1/episodes", headers=as_manager(client))).json()) == sources

    # One delete removes every sample source and its memories.
    r = await client.delete("/v1/sample", headers=as_manager(client))
    assert r.status_code == 200 and r.json() == {"sources": sources, "memories": memories}
    assert (await client.get("/v1/memories")).json() == []
    assert (await client.get("/v1/episodes", headers=as_manager(client))).json() == []
    p = await project(client)
    assert p["memory_count"] == 0 and p["source_count"] == 0 and p["sample_source_count"] == 0
    r = (await client.post("/v1/recall", json={"query": "When is the launch?"})).json()
    assert r["abstained"] and r["abstain_reason"] == "empty_store"


@pytest.mark.asyncio
async def test_sample_keeps_real_sources_and_replaces_a_partial_sample(client):
    from memvine_server.main import app

    sources, memories = sample_size()
    await client.post("/v1/ingest", json={"items": [{"id": "real-1", "content": "## user · 2026-10-20T09:00\nHello there.",
                                                     "timestamp": "2026-10-20T09:00:00Z"}]})
    assert (await client.post("/v1/sample", headers=as_manager(client))).json()["loaded"] is True
    # A sample source deleted by hand: loading again restores the whole sample.
    assert (await client.delete("/v1/episodes/sample:on-call", headers=as_manager(client))).status_code == 204
    assert len((await client.get("/v1/memories")).json()) == memories - 1
    assert (await client.post("/v1/sample", headers=as_manager(client))).json()["loaded"] is True
    assert len((await client.get("/v1/memories")).json()) == memories
    # Deleting the sample leaves the project's own sources alone.
    assert (await client.delete("/v1/sample", headers=as_manager(client))).json()["sources"] == sources
    assert [e["id"] for e in (await client.get("/v1/episodes", headers=as_manager(client))).json()] == ["real-1"]
    async with app.state.pool.connection() as conn:
        queued = await (await conn.execute(
            "SELECT count(*) AS n FROM jobs j JOIN projects p ON p.id = j.project_id WHERE p.slug = %s AND j.status = 'queued'",
            (client.project_slug,))).fetchone()
    assert queued["n"] == 1, "only the real source waits for synthesis"


@pytest.mark.asyncio
async def test_sample_at_project_creation(client):
    slug = f"{client.project_slug}-s"
    r = await client.post("/v1/projects", headers=MGMT, json={"name": slug, "slug": slug, "sample": True})
    try:
        assert r.status_code == 201, r.text
        sources, memories = sample_size()
        assert r.json()["sample_source_count"] == sources and r.json()["memory_count"] == memories
        items = (await client.get("/v1/memories", headers={**MGMT, "X-Memvine-Project": slug})).json()
        assert all(m["category"] and m["scope"] == "project" for m in items), "knowledge carries a category"
        r = (await client.post("/v1/recall", headers={**MGMT, "X-Memvine-Project": slug}, json={"query": "When is the launch?"})).json()
        assert r["items"][0]["memory"]["statement"].startswith("The public launch moved to") and r["items"][0]["previous"]
    finally:
        await client.delete(f"/v1/projects/{slug}", headers=MGMT)


@pytest.mark.asyncio
async def test_playground_try_queries_against_the_sample(client):
    """Each try-query finds its memory first; the unrelated one abstains. Meaningful with the real
    embedder (MEMVINE_EMBEDDINGS_BACKEND=fastembed); the fake one only shares words."""
    from memvine_server.main import app

    if type(app.state.embedder).__name__ != "FastEmbedEmbedder":
        pytest.skip("needs MEMVINE_EMBEDDINGS_BACKEND=fastembed")
    await client.post("/v1/sample", headers=as_manager(client))
    for query, expected in TRY_QUERIES.items():
        r = (await client.post("/v1/recall", json={"query": query})).json()
        assert not r["abstained"], query
        assert r["items"][0]["memory"]["statement"].startswith(expected), (query, [i["memory"]["statement"] for i in r["items"]])
    r = (await client.post("/v1/recall", json={"query": "What is the weather in Paris?"})).json()
    assert r["abstained"] and r["abstain_reason"] == "below_threshold", r
