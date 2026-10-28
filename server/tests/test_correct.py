"""A person's correction: POST /v1/memories with `supersedes` replaces that fact without asking the
judge, keeps its category, thread, subjects and audience, and shows as a `supersedes` edge. And
GET /v1/graph?all=true returns every thread, not only the open ones."""
from __future__ import annotations

import json

import pytest
from conftest import needs_db

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}


@pytest.fixture
async def sample(client):
    """A project created with the sample, addressed with the management key."""
    slug = f"{client.project_slug}-c"
    r = await client.post("/v1/projects", headers=MGMT, json={"name": slug, "slug": slug, "sample": True})
    assert r.status_code == 201, r.text
    headers = {**MGMT, "X-Memvine-Project": slug}
    try:
        yield slug, headers
    finally:
        await client.delete(f"/v1/projects/{slug}", headers=MGMT)


def find(items, prefix):
    return next(m for m in items if m["statement"].startswith(prefix))


@pytest.mark.asyncio
async def test_correction_supersedes_without_the_judge(client, sample):
    from memvine_server.main import app

    _, headers = sample
    old = find((await client.get("/v1/memories", headers=headers)).json(), "The public launch moved to")
    assert old["thread_id"] and old["category"], "the sample's fact has a thread and a category"

    llm = app.state.llm
    saved, calls = dict(llm.responses), len(llm.calls)
    # Were the judge asked, it would keep both.
    llm.responses = {"NEW (": json.dumps({"match": None, "relation": "different", "reason": "distinct"})}
    try:
        r = await client.post("/v1/memories", headers=headers, json={
            "statement": "The public launch is on hold until legal signs off on the refund copy.", "supersedes": old["id"]})
    finally:
        llm.responses = saved
    assert r.status_code == 201, r.text
    assert len(llm.calls) == calls, "no model call when the person says which fact this replaces"
    body = r.json()
    new = body["memory"]
    assert body["relation"] == "update" and body["related"] == old["id"]
    assert new["supersedes"] == old["id"] and new["status"] == "active"
    assert new["assertion"] == "stated" and new["confidence"] == "high"
    for field in ("kind", "subjects", "category", "thread_id", "scope", "audience"):
        assert new[field] == old[field], field
    assert new["why"] is None

    replaced = (await client.get(f"/v1/memories/{old['id']}", headers=headers)).json()
    assert replaced["status"] == "superseded" and replaced["superseded_by"] == new["id"] and replaced["valid_to"]

    rel = (await client.get(f"/v1/memories/{new['id']}/related", headers=headers)).json()
    kinds = {(e["kind"], e["direction"]) for e in rel["edges"]}
    assert ("supersedes", "out") in kinds and ("decided_in", "out") in kinds
    sup = next(e for e in rel["edges"] if e["kind"] == "supersedes")
    assert sup["item"]["id"] == old["id"]

    g = (await client.get("/v1/graph", headers=headers)).json()
    assert ("supersedes", new["id"], old["id"]) in {(e["kind"], e["from"], e["to"]) for e in g["edges"]}
    assert ("decided_in", new["id"], f"thread:{old['thread_id']}") in {(e["kind"], e["from"], e["to"]) for e in g["edges"]}

    # It is no longer current: correcting it again is refused.
    again = await client.post("/v1/memories", headers=headers, json={"statement": "The launch is next week.", "supersedes": old["id"]})
    assert again.status_code == 400 and again.json()["error"]["code"] == "memory-not-current"


@pytest.mark.asyncio
async def test_correction_overrides_and_refusals(client, sample):
    _, headers = sample
    items = (await client.get("/v1/memories", headers=headers)).json()
    target = find(items, "Weekend escalations wait")

    # Given fields win; the rest are inherited.
    r = await client.post("/v1/memories", headers=headers, json={
        "statement": "Weekend escalations page the on-call lead after 2 hours.", "supersedes": target["id"],
        "subjects": ["on-call"], "why": "Customers waited all weekend.", "category": "rule"})
    assert r.status_code == 201, r.text
    new = r.json()["memory"]
    assert new["subjects"] == ["on-call"] and new["why"] == "Customers waited all weekend." and new["category"] == "rule"
    assert new["thread_id"] == target["thread_id"] and new["kind"] == target["kind"]

    # A retired fact cannot be superseded.
    other = find(items, "Refunds over $500")
    assert (await client.patch(f"/v1/memories/{other['id']}", headers=headers, json={"status": "retired"})).status_code == 200
    r = await client.post("/v1/memories", headers=headers, json={"statement": "Refunds over $900 need approval.", "supersedes": other["id"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "memory-not-current"

    # Unknown id, or another project's memory: not found, and nothing is stored.
    before = len((await client.get("/v1/memories", headers=headers, params={"limit": 200})).json())
    r = await client.post("/v1/memories", headers=headers, json={
        "statement": "Something else entirely.", "supersedes": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not-found"
    foreign = (await client.post("/v1/memories", json={"statement": "Deploys go out on Tuesdays."})).json()["memory"]
    r = await client.post("/v1/memories", headers=headers, json={"statement": "Deploys go out on Thursdays.", "supersedes": foreign["id"]})
    assert r.status_code == 404
    assert (await client.get(f"/v1/memories/{foreign['id']}")).json()["status"] == "active"
    assert len((await client.get("/v1/memories", headers=headers, params={"limit": 200})).json()) == before

    # A fact only supersedes one in its own audience.
    assert (await client.put("/v1/audiences/finance", headers=headers, json={"kind": "fixed", "members": ["alice@example.com"]})).status_code == 200
    current = find((await client.get("/v1/memories", headers=headers)).json(), "Weekend escalations page the on-call")
    r = await client.post("/v1/memories", headers=headers, json={
        "statement": "Weekend escalations page the on-call lead after 1 hour.", "supersedes": current["id"], "audience": "finance"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "audience-mismatch"


@pytest.mark.asyncio
async def test_graph_all_returns_every_thread(client, sample):
    from memvine_server.main import app

    slug, headers = sample
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]
        thread_id = (await (await conn.execute(
            "INSERT INTO threads (project_id, kind, key, title, status, summary, first_seen_at, last_activity_at) "
            "VALUES (%s, 'topic', 'topic:old-billing', 'Old billing migration', 'done', 'Finished.', now(), now()) RETURNING id",
            (project_id,))).fetchone())["id"]
    hub = f"thread:{thread_id}"

    default = (await client.get("/v1/graph", headers=headers)).json()
    assert hub not in {n["id"] for n in default["nodes"]}, "a finished thread with no recent facts is not in the default view"

    g = (await client.get("/v1/graph", headers=headers, params={"all": "true"})).json()
    ids = {n["id"] for n in g["nodes"]}
    threads = (await client.get("/v1/threads", headers=headers)).json()
    assert {f"thread:{t['id']}" for t in threads} <= ids
    assert {n["id"] for n in default["nodes"]} <= ids, "everything includes what the default view shows"
    assert not g["truncated"] and len(g["nodes"]) <= 300
    assert all(e["from"] in ids and e["to"] in ids for e in g["edges"])


@pytest.mark.asyncio
async def test_mcp_remember_supersedes(client):
    from test_e2e import mcp_call

    old = (await client.post("/v1/memories", json={"statement": "Deploys go out on Tuesdays.", "kind": "procedural",
                                                     "subjects": ["deploys"]})).json()["memory"]
    stored = await mcp_call(client, "tools/call", {"name": "memory_remember", "arguments": {
        "statement": "Deploys go out on Thursdays.", "supersedes": old["id"]}})
    outcome = json.loads(stored["result"]["content"][0]["text"])
    assert outcome["relation"] == "update" and outcome["related"] == old["id"]
    new = (await client.get(f"/v1/memories/{outcome['stored']}")).json()
    assert new["supersedes"] == old["id"] and new["kind"] == "procedural" and new["subjects"] == ["deploys"]

    refused = await mcp_call(client, "tools/call", {"name": "memory_remember", "arguments": {
        "statement": "Deploys go out on Fridays.", "supersedes": old["id"]}})
    assert refused["result"]["content"][0]["text"].startswith("Not stored:")
