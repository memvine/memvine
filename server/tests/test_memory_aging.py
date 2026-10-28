"""Memory stays honest over time:

1. A time-bound fact (extraction's valid_until) stops being served as current once its end passes,
   but a historical question or an as_of before the end still sees it; the API marks it expired.
2. What no recall served in memory_unused_days is listed for review (never hidden or deleted).
3. A near-duplicate restatement merges into the existing item without asking the model.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from conftest import needs_db

from memvine_server.extraction.extract import knowledge_from
from memvine_server.extraction.prompts import KNOWLEDGE_SYSTEM
from memvine_server.worker import drain

# ---------------------------------------------------------------- extraction (no database)


def test_knowledge_prompt_asks_for_valid_until():
    assert '"valid_until": string | null' in KNOWLEDGE_SYSTEM


def test_valid_until_is_parsed_as_the_end_of_that_day():
    source = "The shared scratch org expires on 28 Oct, so do not park test data there."
    raw = json.dumps([
        {"statement": "The shared scratch org expires on 28 Oct 2026.", "category": "fact", "subjects": ["scratch org"],
         "confidence": "high", "valid_from": None, "valid_until": "2026-10-28", "quote": "The shared scratch org expires on 28 Oct"},
        # An "end" before the start is a misread start date: dropped, the item kept without an end.
        {"statement": "The payment provider enforces idle refresh-token expiry from 4 Nov 2026.", "category": "fact",
         "subjects": ["payment provider"], "confidence": "high", "valid_from": "2026-11-04", "valid_until": "2026-11-01",
         "quote": "do not park test data there"},
    ])
    items = knowledge_from(raw, source).items
    assert items[0].valid_until == datetime(2026, 10, 28, 23, 59, 59, tzinfo=timezone.utc)
    assert items[1].valid_until is None


# ---------------------------------------------------------------- database

ORG_SOURCE = "Heads up: the shared scratch org expires on 20 Oct 2026, after that use the new sandbox."
ORG_FACT = "The shared scratch org expires on 20 Oct 2026; after that use the new sandbox."
FREEZE_SOURCE = "Merge freeze for 4.2.0 holds until the freeze ends on 30 Dec 2099."
FREEZE_FACT = "No merges to main while the 4.2.0 merge freeze holds, until 30 Dec 2099."


async def project_id_of(client, app):
    async with app.state.pool.connection() as conn:
        return (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]


async def ingest_time_bound_facts(client, app):
    llm = app.state.llm
    llm.responses = {
        # The second episode's prompt carries the first as context: its key goes first.
        "Merge freeze for 4.2.0": json.dumps([
            {"statement": FREEZE_FACT, "category": "rule", "subjects": ["merge freeze"], "confidence": "high",
             "valid_from": None, "valid_until": "2099-12-30", "quote": "Merge freeze for 4.2.0 holds"}]),
        "shared scratch org expires": json.dumps([
            {"statement": ORG_FACT, "category": "fact", "subjects": ["scratch org"], "confidence": "high",
             "valid_from": None, "valid_until": "2026-10-20", "quote": "the shared scratch org expires on 20 Oct 2026"}]),
    }
    llm.default = "[]"
    r = await client.post("/v1/ingest", json={"items": [
        {"id": "org", "content": ORG_SOURCE, "timestamp": "2026-10-01T09:00:00Z"},
        {"id": "freeze", "content": FREEZE_SOURCE, "timestamp": "2026-10-02T09:00:00Z"},
    ]})
    assert r.status_code == 202, r.text
    await drain(app.state.pool, llm, app.state.embedder, await project_id_of(client, app))
    assert not [j for j in (await client.get("/v1/jobs")).json()["jobs"] if j["status"] == "failed"]


@needs_db
@pytest.mark.asyncio
async def test_a_fact_past_its_stated_end_is_not_served_as_current(client):
    from memvine_server.main import app

    await ingest_time_bound_facts(client, app)
    memories = {m["statement"]: m for m in (await client.get("/v1/memories")).json()}
    org, freeze = memories[ORG_FACT], memories[FREEZE_FACT]
    # Stored with its end; the status is unchanged, "expired" is derived from valid_to.
    assert org["valid_to"].startswith("2026-10-20T23:59:59") and org["status"] == "active" and org["expired"]
    assert freeze["valid_to"].startswith("2099-12-30") and not freeze["expired"]
    assert [m["id"] for m in (await client.get("/v1/memories", params={"status": "expired"})).json()] == [org["id"]]
    assert org["id"] not in [m["id"] for m in (await client.get("/v1/memories", params={"status": "active"})).json()]

    # Now: the expired fact is not served; the still-running one is, with its end.
    r = (await client.post("/v1/recall", json={"query": "shared scratch org sandbox"})).json()
    assert org["id"] not in [i["memory"]["id"] for i in r["items"]]
    assert "scratch org expires" not in r["briefing"].split("--- source excerpts")[0]
    r = (await client.post("/v1/recall", json={"query": "merge freeze main"})).json()
    assert freeze["id"] in [i["memory"]["id"] for i in r["items"]]
    assert "until 30 Dec 2099]" in r["briefing"]

    # History and a point in time before the end still see it, marked as ended.
    r = (await client.post("/v1/recall", json={"query": "shared scratch org sandbox", "include_history": True})).json()
    assert org["id"] in [i["memory"]["id"] for i in r["items"]]
    assert "ended 20 Oct" in r["briefing"]
    r = (await client.post("/v1/recall", json={"query": "shared scratch org sandbox", "as_of": "2026-10-10T00:00:00Z"})).json()
    assert org["id"] in [i["memory"]["id"] for i in r["items"]]
    r = (await client.post("/v1/recall", json={"query": "shared scratch org sandbox", "as_of": "2026-10-25T00:00:00Z"})).json()
    assert org["id"] not in [i["memory"]["id"] for i in r["items"]]


@needs_db
@pytest.mark.asyncio
async def test_unused_memories_surface_for_review(client):
    from memvine_server.config import settings
    from memvine_server.main import app

    old = (await client.post("/v1/memories", json={"statement": "The billing exports land in the finance bucket nightly.",
                                                   "subjects": ["billing exports"]})).json()["memory"]
    used = (await client.post("/v1/memories", json={"statement": "Release notes are written by the on-call lead.",
                                                    "subjects": ["release notes"]})).json()["memory"]
    fresh = (await client.post("/v1/memories", json={"statement": "Grafana dashboards live in the observability folder.",
                                                     "subjects": ["grafana"]})).json()["memory"]
    days = settings.memory_unused_days
    async with app.state.pool.connection() as conn:
        await conn.execute("UPDATE memory_items SET created_at = now() - make_interval(days => %s) WHERE id = ANY(%s)",
                           (days + 5, [old["id"], used["id"]]))

    # A recall with the project's key serves "used" and records it, in one statement.
    r = (await client.post("/v1/recall", json={"query": "Who writes the release notes?"})).json()
    assert used["id"] in [i["memory"]["id"] for i in r["items"]]
    assert old["id"] not in [i["memory"]["id"] for i in r["items"]]
    after = (await client.get(f"/v1/memories/{used['id']}")).json()
    assert after["recall_count"] == 1 and after["last_recalled_at"] is not None

    unused = (await client.get("/v1/memories", params={"unused": "true"})).json()
    assert [m["id"] for m in unused] == [old["id"]], "old and never served; the fresh one is too new, the used one was served"
    assert len((await client.get("/v1/memories")).json()) == 3, "nothing is hidden for being unused"
    project = next(p for p in (await client.get("/v1/projects", headers={"Authorization": "Bearer mv_mk_test"})).json()
                   if p["slug"] == client.project_slug)
    assert project["unused_count"] == 1 and project["unused_days"] == days

    # Served long ago counts as unused again; the console's playground (management key) never counts.
    async with app.state.pool.connection() as conn:
        await conn.execute("UPDATE memory_items SET last_recalled_at = now() - make_interval(days => %s) WHERE id = %s",
                           (days + 1, used["id"]))
    assert {m["id"] for m in (await client.get("/v1/memories", params={"unused": "true"})).json()} == {old["id"], used["id"]}
    r = (await client.post("/v1/recall", json={"query": "billing exports finance bucket"},
                           headers={"Authorization": "Bearer mv_mk_test", "X-Memvine-Project": client.project_slug})).json()
    assert old["id"] in [i["memory"]["id"] for i in r["items"]]
    assert (await client.get(f"/v1/memories/{old['id']}")).json()["recall_count"] == 0
    assert fresh["id"] not in {m["id"] for m in (await client.get("/v1/memories", params={"unused": "true"})).json()}


@needs_db
@pytest.mark.asyncio
async def test_a_near_duplicate_merges_without_asking_the_model(client):
    from memvine_server.main import app
    from memvine_server.reconcile import IDENTICAL_SIMILARITY, NEAR_DUPLICATE_SIMILARITY

    first = "The staging database is shared with the partner integration team on weekdays."
    reworded = "The staging database is shared with the partner integration team on weekdays and holidays."
    changed = "The staging database is now shared with the partner integration team on weekdays."
    a, b, c = await app.state.embedder.embed([first, reworded, changed])
    cos = sum(x * y for x, y in zip(a, b))
    assert NEAR_DUPLICATE_SIMILARITY <= cos < IDENTICAL_SIMILARITY, cos

    llm = app.state.llm
    llm.responses, llm.default, llm.calls = {}, json.dumps({"match": None, "relation": "different"}), []
    base = (await client.post("/v1/memories", json={"statement": first, "subjects": ["staging database"], "category": "fact"})).json()
    again = (await client.post("/v1/memories", json={"statement": reworded, "subjects": ["staging database"], "category": "fact"})).json()
    assert again["relation"] == "same" and again["memory"]["id"] == base["memory"]["id"]
    assert again["memory"]["statement"] == first, "the existing wording stays"
    assert len(again["memory"]["evidence"]) == 2, "the restatement adds evidence"
    assert not [u for _, u in llm.calls if "NEW (" in u], "no judge was asked"

    # A different category, or wording that announces a change, goes to the judge instead.
    rule = (await client.post("/v1/memories", json={"statement": reworded, "subjects": ["staging database"],
                                                    "kind": "procedural", "category": "rule"})).json()
    assert rule["memory"]["id"] != base["memory"]["id"]
    if sum(x * y for x, y in zip(a, c)) >= NEAR_DUPLICATE_SIMILARITY:
        update = (await client.post("/v1/memories", json={"statement": changed, "subjects": ["staging database"],
                                                          "category": "fact"})).json()
        assert update["relation"] != "same"
    assert [u for _, u in llm.calls if "NEW (" in u], "the judge decided those"
