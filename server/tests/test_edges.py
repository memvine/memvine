"""The memory graph: named edges in the API. The sample project, loaded through the API, shows the
superseded launch date with a `supersedes` edge and the weekend-escalation disagreement with a
`contradicts` edge, in recall (include.related), in /v1/memories/{id}/related and in /v1/graph with
their threads. Reconcile records `extends`."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import needs_db

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}


def ref(refs, kind):
    return [r for r in refs if r["kind"] == kind]


@pytest.fixture
async def sample(client):
    """A project created with the sample, addressed with the management key."""
    slug = f"{client.project_slug}-g"
    r = await client.post("/v1/projects", headers=MGMT, json={"name": slug, "slug": slug, "sample": True})
    assert r.status_code == 201, r.text
    headers = {**MGMT, "X-Memvine-Project": slug}
    items = (await client.get("/v1/memories", headers=headers)).json()
    by = {
        "launch_new": next(m for m in items if m["statement"].startswith("The public launch moved to")),
        "launch_old": next(m for m in items if m["statement"].startswith("The public launch is planned for")),
        "wait": next(m for m in items if m["statement"].startswith("Weekend escalations wait")),
        "page": next(m for m in items if m["statement"].startswith("Weekend escalations page")),
    }
    try:
        yield headers, by
    finally:
        await client.delete(f"/v1/projects/{slug}", headers=MGMT)


@pytest.mark.asyncio
async def test_sample_recall_includes_related_edges(client, sample):
    headers, m = sample
    new, old, wait, page = m["launch_new"], m["launch_old"], m["wait"], m["page"]

    plain = (await client.post("/v1/recall", headers=headers, json={"query": "When is the launch?"})).json()
    assert all(i["related"] is None for i in plain["items"]), "edges only when asked"
    r = (await client.post("/v1/recall", headers=headers,
                           json={"query": "When is the launch?", "include": {"related": True}})).json()
    assert not r["abstained"]
    first = r["items"][0]
    assert first["memory"]["id"] == new["id"] and first["previous"] == old["statement"], "existing fields unchanged"
    assert ref(first["related"], "supersedes") == [
        {"kind": "supersedes", "direction": "out", "id": old["id"], "statement": old["statement"]}]
    assert ref(first["related"], "decided_in") == [
        {"kind": "decided_in", "direction": "out", "id": new["thread_id"], "statement": "Public launch"}]
    assert r["briefing"] == plain["briefing"], "edges never change what the briefing packs"
    assert r["tokens"] > plain["tokens"], "but they are counted"

    r = (await client.post("/v1/recall", headers=headers,
                           json={"query": "Do weekend escalations page anyone?", "include": {"related": True}})).json()
    served = {i["memory"]["id"]: i for i in r["items"]}
    assert wait["id"] in served or page["id"] in served
    for this, other in ((wait, page), (page, wait)):
        if this["id"] in served:
            assert ref(served[this["id"]]["related"], "contradicts") == [
                {"kind": "contradicts", "direction": "both", "id": other["id"], "statement": other["statement"]}]
    assert all(len(i["related"]) <= 6 for i in r["items"])


@pytest.mark.asyncio
async def test_sample_related_endpoint(client, sample):
    headers, m = sample
    new, old, wait, page = m["launch_new"], m["launch_old"], m["wait"], m["page"]

    r = (await client.get(f"/v1/memories/{new['id']}/related", headers=headers)).json()
    assert r["memory"]["id"] == new["id"]
    kinds = {(e["kind"], e["direction"]) for e in r["edges"]}
    assert kinds == {("supersedes", "out"), ("decided_in", "out")}
    sup = next(e for e in r["edges"] if e["kind"] == "supersedes")
    assert sup["item"]["id"] == old["id"] and sup["item"]["status"] == "superseded"
    assert sup["item"]["evidence"][0]["identity"] == "Priya", "who said it rides on the evidence"
    thread = next(e for e in r["edges"] if e["kind"] == "decided_in")["thread"]
    assert thread["title"] == "Public launch" and thread["key"] == "topic:public-launch" and thread["summary"]

    r = (await client.get(f"/v1/memories/{old['id']}/related", headers=headers)).json()
    assert [(e["kind"], e["direction"], e["item"]["id"]) for e in r["edges"] if e["item"]] == [("supersedes", "in", new["id"])]

    r = (await client.get(f"/v1/memories/{wait['id']}/related", headers=headers)).json()
    assert [(e["kind"], e["direction"], e["item"]["id"]) for e in r["edges"] if e["item"]] == [("contradicts", "both", page["id"])]

    missing = await client.get("/v1/memories/00000000-0000-0000-0000-000000000000/related", headers=headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_sample_graph(client, sample):
    headers, m = sample
    new, old, wait, page = m["launch_new"], m["launch_old"], m["wait"], m["page"]

    g = (await client.get("/v1/graph", headers=headers)).json()
    assert not g["truncated"] and len(g["nodes"]) <= 300
    nodes = {n["id"]: n for n in g["nodes"]}
    edges = {(e["kind"], e["from"], e["to"]) for e in g["edges"]}
    assert {new["id"], old["id"], wait["id"], page["id"]} <= set(nodes)
    assert nodes[old["id"]]["memory"]["status"] == "superseded"
    assert ("supersedes", new["id"], old["id"]) in edges
    assert ("contradicts", *sorted((wait["id"], page["id"]))) in edges
    launch, escalations = f"thread:{new['thread_id']}", f"thread:{wait['thread_id']}"
    assert nodes[launch]["type"] == "thread" and nodes[launch]["label"] == "Public launch"
    assert nodes[escalations]["thread"]["title"] == "Support escalations"
    for fact, hub in ((new, launch), (old, launch), (wait, escalations), (page, escalations)):
        assert ("decided_in", fact["id"], hub) in edges
    # Who said it: a field on the fact, drawn to a person node.
    assert nodes[page["id"]]["said_by"] == ["Dana"] and nodes["person:Dana"]["type"] == "person"
    assert {n["type"] for n in g["nodes"]} >= {"fact", "thread", "person"}
    assert all(e["from"] in nodes and e["to"] in nodes for e in g["edges"])
    assert not any(n["grew"] for n in g["nodes"]), "nothing grows without `since`"

    # Since 13 days ago: the new launch date is new, the old one changed (superseded 12 days ago).
    since = (datetime.now(UTC) - timedelta(days=13)).isoformat()
    g = (await client.get("/v1/graph", headers=headers, params={"since": since})).json()
    grew = {n["id"] for n in g["nodes"] if n["grew"]}
    assert {new["id"], old["id"], wait["id"], page["id"]} <= grew
    refunds = next(n for n in g["nodes"] if n["label"].startswith("Refunds over $500"))
    assert refunds["id"] not in grew, "learned 21 days ago and unchanged"

    # One thread's neighborhood.
    g = (await client.get("/v1/graph", headers=headers, params={"thread": new["thread_id"]})).json()
    assert {n["id"] for n in g["nodes"] if n["type"] == "fact"} == {new["id"], old["id"]}
    assert {(e["kind"], e["from"], e["to"]) for e in g["edges"]} >= {("supersedes", new["id"], old["id"])}
    assert any(n["id"] == launch for n in g["nodes"])
    # One subject's.
    g = (await client.get("/v1/graph", headers=headers, params={"subject": "escalations"})).json()
    assert {wait["id"], page["id"]} <= {n["id"] for n in g["nodes"]}
    assert ("contradicts", *sorted((wait["id"], page["id"]))) in {(e["kind"], e["from"], e["to"]) for e in g["edges"]}
    missing = await client.get("/v1/graph", headers=headers, params={"thread": "00000000-0000-0000-0000-000000000000"})
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_reconcile_records_extends(client):
    from memvine_server.main import app

    llm = app.state.llm
    saved = dict(llm.responses)
    llm.responses = {"NEW (": json.dumps({"match": 1, "relation": "extends", "reason": "adds the reason"})}
    try:
        base = (await client.post("/v1/memories", json={"statement": "Queue retries cap at 3 attempts.",
                                                        "subjects": ["queue retries"]})).json()
        detail = (await client.post("/v1/memories", json={
            "statement": "Queue retries cap at 3 attempts because the broker rate-limits them.",
            "subjects": ["queue retries"]})).json()
    finally:
        llm.responses = saved
    assert base["relation"] == "new"
    assert detail["relation"] == "extends" and detail["related"] == base["memory"]["id"]
    assert detail["memory"]["status"] == "active"
    assert (await client.get(f"/v1/memories/{base['memory']['id']}")).json()["status"] == "active", "both stay active"

    r = (await client.get(f"/v1/memories/{detail['memory']['id']}/related")).json()
    assert [(e["kind"], e["direction"], e["item"]["id"]) for e in r["edges"]] == [("extends", "out", base["memory"]["id"])]
    r = (await client.get(f"/v1/memories/{base['memory']['id']}/related")).json()
    assert [(e["kind"], e["direction"], e["item"]["id"]) for e in r["edges"]] == [("extends", "in", detail["memory"]["id"])]
    g = (await client.get("/v1/graph")).json()
    assert {(e["kind"], e["from"], e["to"]) for e in g["edges"]} == {("extends", detail["memory"]["id"], base["memory"]["id"])}


@pytest.mark.asyncio
async def test_reconcile_prompt_offers_extends():
    from memvine_server.extraction.prompts import RECONCILE_SYSTEM

    assert '"extends"' in RECONCILE_SYSTEM and "\n- extends:" in RECONCILE_SYSTEM


@pytest.mark.asyncio
async def test_anchored_edges_say_whether_the_file_is_current(client):
    from memvine_server.main import app

    made = (await client.post("/v1/memories", json={"statement": "Queue retries cap at 3 in src/queue.py.",
                                                    "subjects": ["queue"]})).json()
    mid = made["memory"]["id"]
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
        await conn.execute("INSERT INTO memory_anchors (project_id, memory_id, repo, path, blob) VALUES (%s, %s, 'acme/app', "
                           "'src/queue.py', 'a1b4c9e0000')", (project_id, mid))
    r = (await client.get(f"/v1/memories/{mid}/related")).json()
    f = next(e for e in r["edges"] if e["kind"] == "anchored")["file"]
    assert (f["path"], f["version"], f["current"]) == ("src/queue.py", "a1b4c9e0000", None), "no version reported since"

    await client.post("/v1/code/check", json={"repo": "acme/app", "files": {"src/queue.py": "ffff0000"}})
    r = (await client.get(f"/v1/memories/{mid}/related")).json()
    f = next(e for e in r["edges"] if e["kind"] == "anchored")["file"]
    assert f["current"] is False and f["current_version"] == "ffff0000"
    assert r["memory"]["status"] == "stale"
    rec = (await client.post("/v1/recall", json={"query": "queue retries cap", "include": {"related": True}})).json()
    item = next(i for i in rec["items"] if i["memory"]["id"] == mid)
    assert item["related"] == [{"kind": "anchored", "direction": "out", "id": None, "statement": "src/queue.py@a1b4c9e (changed)"}]
    g = (await client.get("/v1/graph")).json()
    assert any(n["id"] == "file:acme/app:src/queue.py" and n["file"]["version"] == "ffff0000" for n in g["nodes"])
    assert {(e["kind"], e["to"], e["version"], e["current"]) for e in g["edges"]} == {
        ("anchored", "file:acme/app:src/queue.py", "a1b4c9e0000", False)}
