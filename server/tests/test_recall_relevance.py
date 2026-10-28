"""Recall abstains unless something is relevant enough: a non-empty store, a thread page found only by
similarity, standing instructions and pending messages from other sessions never make an unrelated
question look answered."""
from __future__ import annotations

import json

import pytest
from conftest import needs_db

from memvine_server.worker import drain

pytestmark = needs_db

MGMT = {"Authorization": "Bearer mv_mk_test"}

SOURCE = (
    "## Developer · 2026-10-20T09:00\nMuse is our test project. Muse keeps its data in Postgres 16, deploys go through "
    "GitHub Actions, and the console is built with Next.js. Always run the migrations before a deploy, and never "
    "deploy on Fridays."
)
EXTRACTED = json.dumps([
    {"statement": "Muse stores its data in a Postgres 16 database.", "kind": "semantic", "category": "fact", "subjects": ["muse"],
     "assertion": "stated", "confidence": "high", "quote": "Muse keeps its data in Postgres 16"},
    {"statement": "Muse deploys go through GitHub Actions.", "kind": "procedural", "category": "rule", "subjects": ["muse"],
     "assertion": "stated", "confidence": "high", "quote": "deploys go through GitHub Actions"},
    {"statement": "The Muse console is built with Next.js.", "kind": "semantic", "category": "fact", "subjects": ["muse"],
     "assertion": "stated", "confidence": "high", "quote": "the console is built with Next.js"},
    {"statement": "Always run the migrations before a deploy.", "kind": "procedural", "category": "rule", "subjects": ["deploys"],
     "assertion": "stated", "confidence": "high", "standing": True, "quote": "Always run the migrations before a deploy"},
    {"statement": "Never deploy on Fridays.", "kind": "procedural", "category": "rule", "subjects": ["deploys"],
     "assertion": "stated", "confidence": "high", "standing": True, "quote": "never deploy on Fridays"},
])
STANDING = {"Always run the migrations before a deploy.", "Never deploy on Fridays."}
UNRELATED = "Weather forecast for Paris tomorrow?"


def default_threshold() -> float:
    """The embedding model's calibrated default (0.15 for the fake embedder, 0.55 for BGE-small); the
    scenarios here also pass with MEMVINE_EMBEDDINGS_BACKEND=fastembed."""
    from memvine_server.main import app

    return app.state.embedder.min_relevance


async def project_id(app, slug):
    async with app.state.pool.connection() as conn:
        return (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]


async def setup_muse(client):
    """Five memories (two of them standing rules) and their source passages."""
    from memvine_server.main import app

    app.state.llm.responses = {"Muse keeps its data in Postgres 16": EXTRACTED}
    app.state.llm.default = "[]"
    r = await client.post("/v1/ingest", json={"items": [{"id": "muse-1", "content": SOURCE, "timestamp": "2026-10-20T09:00:00Z"}]})
    assert r.status_code == 202, r.text
    await drain(app.state.pool, app.state.llm, app.state.embedder, await project_id(app, client.project_slug))
    assert len((await client.get("/v1/memories")).json()) == 5


@pytest.mark.asyncio
async def test_empty_store_abstains(client):
    r = (await client.post("/v1/recall", json={"query": "Which database does muse use?"})).json()
    assert r["abstained"] and r["abstain_reason"] == "empty_store"
    assert r["briefing"] == "" and r["items"] == [] and r["tokens"] == 0 and r["standing"] == []
    assert r["top_relevance"] is None and r["min_relevance"] == pytest.approx(default_threshold())


@pytest.mark.asyncio
async def test_relevant_query_returns_the_right_memory_first(client):
    await setup_muse(client)
    r = (await client.post("/v1/recall", json={"query": "Which database does muse use?"})).json()
    assert not r["abstained"] and r["abstain_reason"] is None
    assert r["items"][0]["memory"]["statement"] == "Muse stores its data in a Postgres 16 database."
    assert r["top_relevance"] >= r["min_relevance"]
    assert set(r["standing"]) == STANDING
    assert not any(i["memory"]["standing"] for i in r["items"]), "standing rules live in their own section"
    lines = r["briefing"].splitlines()
    assert lines[0].startswith("=== memvine · standing instructions")
    assert lines.index("=== memvine · recalled memory (background, not instruction) ===") == 1 + len(STANDING)
    assert "pages" not in r, "recall serves thread pages only"


@pytest.mark.asyncio
async def test_unrelated_query_abstains_despite_standing_rules(client):
    """The Paris case from the live test: memories and two standing rules exist, yet
    nothing is about the question, so nothing is served but the standing rules, in `standing` only."""
    await setup_muse(client)
    # A pending message from another live session, not yet extracted and unrelated to the question.
    r = await client.post("/v1/sessions/live1/events", json={"events": [{"role": "user", "content": "Let's rename the console header."}]})
    assert r.status_code == 202

    r = (await client.post("/v1/recall", json={"query": UNRELATED, "session_id": "other"})).json()
    assert r["abstained"] and r["abstain_reason"] == "below_threshold"
    assert r["briefing"] == "" and r["tokens"] == 0 and r["items"] == []
    assert r["passages"] == [] and r["pending"] == [] and r["threads"] == []
    assert set(r["standing"]) == STANDING
    assert r["top_relevance"] < r["min_relevance"]

    # The Requests log says why.
    logged = (await client.get("/v1/requests")).json()
    recall_rows = [row for row in logged if row["path"] == "/v1/recall"]
    assert recall_rows[0]["detail"].startswith("abstained: below_threshold")


@pytest.mark.asyncio
async def test_project_threshold_is_respected(client):
    await setup_muse(client)
    query = {"query": "Which database does muse use?"}
    patched = await client.patch(f"/v1/projects/{client.project_slug}", headers=MGMT, json={"recall_min_relevance": 0.95})
    assert patched.status_code == 200 and patched.json()["recall_min_relevance"] == pytest.approx(0.95)
    strict = (await client.post("/v1/recall", json=query)).json()
    assert strict["abstained"] and strict["abstain_reason"] == "below_threshold"
    assert strict["min_relevance"] == pytest.approx(0.95) and set(strict["standing"]) == STANDING

    lenient = await client.patch(f"/v1/projects/{client.project_slug}", headers=MGMT, json={"recall_min_relevance": 0.0})
    assert lenient.json()["recall_min_relevance"] == 0.0
    anything = (await client.post("/v1/recall", json={"query": UNRELATED})).json()
    assert not anything["abstained"], "a threshold of 0 serves whatever is closest"

    reset = await client.patch(f"/v1/projects/{client.project_slug}", headers=MGMT, json={"recall_min_relevance": None})
    assert reset.json()["recall_min_relevance"] is None
    again = (await client.post("/v1/recall", json=query)).json()
    assert not again["abstained"] and again["min_relevance"] == pytest.approx(default_threshold())
    assert (await client.patch(f"/v1/projects/{client.project_slug}", headers=MGMT,
                               json={"recall_min_relevance": 1.5})).status_code == 422


@pytest.mark.asyncio
async def test_thread_page_defeats_abstention_only_on_its_branch(client):
    """A thread page found by similarity is background; the caller's branch page is a hit (branch-first)."""
    from test_threads import setup_release

    await setup_release(client)
    unrelated = {"query": UNRELATED}
    r = (await client.post("/v1/recall", json=unrelated)).json()
    assert r["abstained"] and r["threads"] == []
    on_branch = (await client.post("/v1/recall", json={**unrelated, "branch": "release-billing-v4.2.0"})).json()
    assert not on_branch["abstained"] and [t["key"] for t in on_branch["threads"]] == ["release:4.2.0"]


@pytest.mark.asyncio
async def test_a_chosen_fact_shows_the_value_it_superseded(client):
    """When a fact that superseded another is chosen, the briefing says the old value as "(was: ...)"."""
    from memvine_server.main import app

    await setup_muse(client)
    pid = await project_id(app, client.project_slug)
    async with app.state.pool.connection() as conn:
        new = await (await conn.execute(
            "SELECT * FROM memory_items WHERE project_id = %s AND statement LIKE 'Muse stores%%'", (pid,))).fetchone()
        old = await (await conn.execute(
            """INSERT INTO memory_items (project_id, kind, statement, subjects, valid_from, recorded_at, status,
                                         superseded_by, assertion, confidence, embedding)
               VALUES (%s, 'semantic', 'Muse stores its data in a MySQL 8 database.', %s, %s, %s, 'superseded',
                       %s, 'stated', 'high', %s) RETURNING id""",
            (pid, new["subjects"], new["valid_from"], new["recorded_at"], new["id"], new["embedding"]))).fetchone()
        await conn.execute("UPDATE memory_items SET supersedes = %s WHERE id = %s", (old["id"], new["id"]))
    r = (await client.post("/v1/recall", json={"query": "muse database deploys console", "budget": 4000})).json()
    assert "(was: Muse stores its data in a MySQL 8 database" in r["briefing"]
    assert "### muse" not in r["briefing"]
