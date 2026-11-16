"""Team spaces (migration 0014): a person's sessions stay in a project of their own, and the team's
project takes only the memories they chose to share, each with who said it, when and in what words."""
from __future__ import annotations

import json
import uuid

import pytest
from conftest import needs_db
from test_e2e import EXTRACT_1, EXTRACT_2, SESSION_1, SESSION_2, ingest_and_synthesize

from memvine_server.worker import drain

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}


async def _app():
    from memvine_server.main import app

    return app


async def team_space(client, **settings) -> dict:
    """A second project in the same organization, as a team space; returns {slug, headers}."""
    slug = f"team-{uuid.uuid4().hex[:8]}"
    created = await client.post("/v1/projects", json={"name": slug, "slug": slug, "team_space": True, **settings}, headers=MGMT)
    assert created.status_code == 201, created.text
    assert created.json()["team_space"] is True
    key = await client.post(f"/v1/projects/{slug}/keys", json={"name": "team"}, headers=MGMT)
    return {"slug": slug, "headers": {"Authorization": f"Bearer {key.json()['key']}"}}


@pytest.mark.asyncio
async def test_a_team_space_refuses_conversation_and_takes_shared_memories(client):
    team = await team_space(client)
    info = (await client.get("/v1/project", headers=team["headers"])).json()
    assert info == {"slug": team["slug"], "name": team["slug"], "memory_mode": "coding", "team_space": True, "share_policy": "ask"}
    own = (await client.get("/v1/project")).json()
    assert own["team_space"] is False

    episode = {"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}
    refused = await client.post("/v1/ingest", json={"items": [episode]}, headers=team["headers"])
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "team-space", refused.text
    events = {"events": [{"role": "user", "content": "Target launch is 29 Oct."}]}
    refused = await client.post("/v1/sessions/ses_1/events", json=events, headers=team["headers"])
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "team-space", refused.text
    listing = {**MGMT, "X-Memvine-Project": team["slug"]}
    assert (await client.get("/v1/sessions", headers=listing)).json() == []
    async with (await _app()).state.pool.connection() as conn:
        held = await (await conn.execute("SELECT (SELECT count(*) FROM episodes e JOIN projects p ON p.id = e.project_id WHERE p.slug = %s) AS n", (team["slug"],))).fetchone()
    assert held["n"] == 0, "a refused request leaves nothing behind"

    shared = await client.post("/v1/memories", headers=team["headers"], json={
        "statement": "Retries cap at 3: the broker blocks a 4th retry.", "kind": "procedural", "category": "rule",
        "subjects": ["retries"], "said_by": "dana@northwind.example", "said_at": "2026-02-04T09:30:00Z",
        "quote": "never set retries above 3 on the sync job: the broker blocks a 4th retry"})
    assert shared.status_code == 201, shared.text
    memory = shared.json()["memory"]
    assert shared.json()["relation"] == "new"
    assert memory["valid_from"].startswith("2026-02-04T09:30")
    (evidence,) = memory["evidence"]
    assert evidence["identity"] == "dana@northwind.example"
    assert evidence["quote"] == "never set retries above 3 on the sync job: the broker blocks a 4th retry"
    assert evidence["episode_id"].startswith("shared:")
    # The team's source for it is the quoted words and nothing else.
    source = (await client.get(f"/v1/episodes/{evidence['episode_id']}", headers=listing)).json()
    assert source["content"] == evidence["quote"] and source["identity"] == "dana@northwind.example"

    recalled = (await client.post("/v1/recall", json={"query": "How many retries are allowed?"}, headers=team["headers"])).json()
    assert "Retries cap at 3" in recalled["briefing"]
    # The person's own project knows nothing of it: the two are separate stores.
    mine = (await client.post("/v1/recall", json={"query": "How many retries are allowed?"})).json()
    assert "Retries cap at 3" not in (mine.get("briefing") or "")


@pytest.mark.asyncio
async def test_a_project_that_holds_conversation_cannot_become_a_team_space(client):
    from memvine_server.main import app

    await ingest_and_synthesize(client, app, [{"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}])
    turned = await client.patch(f"/v1/projects/{client.project_slug}", json={"team_space": True}, headers=MGMT)
    assert turned.status_code == 409 and turned.json()["error"]["code"] == "has-conversation", turned.text

    team = await team_space(client)
    auto = await client.patch(f"/v1/projects/{team['slug']}", json={"share_policy": "auto"}, headers=MGMT)
    assert auto.status_code == 200 and auto.json()["share_policy"] == "auto" and auto.json()["team_space"] is True
    assert (await client.get("/v1/project", headers=team["headers"])).json()["share_policy"] == "auto"
    bad = await client.patch(f"/v1/projects/{team['slug']}", json={"share_policy": "everything"}, headers=MGMT)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_kept_suggests_what_to_share_until_it_is_shared(client):
    from memvine_server.main import app

    preference = {"statement": "Priya prefers short answers with the conclusion first.", "kind": "semantic", "category": "preference",
                  "subjects": ["priya"], "assertion": "stated", "confidence": "high", "quote": "Target launch is 29 Oct."}
    app.state.llm.responses = {"Target launch is 29 Oct": json.dumps([*json.loads(EXTRACT_1), preference]), **app.state.llm.responses}
    events = [{"role": "user", "content": SESSION_1.split("\n\n")[0].split("\n", 1)[1], "at": "2026-09-18T10:11:00Z"},
              {"role": "assistant", "content": SESSION_1.split("\n\n")[1].split("\n", 1)[1], "at": "2026-09-18T10:12:00Z"}]
    assert (await client.post("/v1/sessions/ses_s/events", json={"events": events, "identity": "priya@northwind.example"})).status_code == 202
    await client.post("/v1/sessions/ses_s/close")
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, project_id)

    kept = (await client.get("/v1/sessions/ses_s/kept")).json()["kept"]
    by_category = {k["category"]: k for k in kept}
    assert set(by_category) == {"fact", "rule", "preference"}, kept
    assert by_category["fact"]["suggest_share"] and by_category["rule"]["suggest_share"]
    assert not by_category["preference"]["suggest_share"], "a preference is one person's"
    assert by_category["rule"]["quote"] == "Refunds over $500 always need a human approver"
    assert not any(k["shared"] for k in kept)

    # Sharing: the plugin posts it to the team space with its author, then marks its own copy.
    team = await team_space(client)
    rule = (await client.get(f"/v1/memories/{by_category['rule']['id']}")).json()
    there = await client.post("/v1/memories", headers=team["headers"], json={
        "statement": rule["statement"], "kind": rule["kind"], "category": rule["category"], "subjects": rule["subjects"],
        "said_by": rule["evidence"][0]["identity"], "said_at": rule["valid_from"], "quote": rule["evidence"][0]["quote"]})
    assert there.status_code == 201, there.text
    assert there.json()["memory"]["evidence"][0]["identity"] == "priya@northwind.example"
    marked = await client.patch(f"/v1/memories/{rule['id']}", json={"shared_ref": there.json()["memory"]["id"]})
    assert marked.status_code == 200 and marked.json()["shared_ref"] == there.json()["memory"]["id"] and marked.json()["shared_at"]

    kept = {k["category"]: k for k in (await client.get("/v1/sessions/ses_s/kept")).json()["kept"]}
    assert kept["rule"]["shared"] and not kept["rule"]["suggest_share"]
    assert kept["fact"]["suggest_share"] and not kept["fact"]["shared"]

    # Sharing the same thing twice leaves one memory in the team space.
    app.state.llm.responses = {"): Refunds over $500 need a human approver": json.dumps({"match": 1, "relation": "same", "reason": "restated"}),
                               **app.state.llm.responses}
    again = await client.post("/v1/memories", headers=team["headers"], json={
        "statement": rule["statement"], "kind": rule["kind"], "category": rule["category"], "subjects": rule["subjects"],
        "said_by": "dana@northwind.example", "quote": "refunds over $500 need a human"})
    assert again.status_code == 201 and again.json()["relation"] == "same", again.text
    assert again.json()["memory"]["id"] == there.json()["memory"]["id"]

    # Taking the mark off makes it a suggestion again.
    cleared = await client.patch(f"/v1/memories/{rule['id']}", json={"shared_ref": None})
    assert cleared.json()["shared_ref"] is None and cleared.json()["shared_at"] is None


# ------------------------------------------------------------------ sharing from the console


async def seeded(client):
    """The person's own project with the launch fact and the refunds rule, said by Priya; and a team space."""
    from memvine_server.main import app

    await ingest_and_synthesize(client, app, [{"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z",
                                               "identity": "priya@northwind.example"}])
    memories = (await client.get("/v1/memories")).json()
    memories = memories.get("items", memories) if isinstance(memories, dict) else memories
    by_start = {m["statement"].split()[0]: m for m in memories}
    return app, by_start["Launch"], by_start["Refunds"], await team_space(client)


def console(client) -> dict:
    """The console's caller: an organization-level credential naming the person's project."""
    return {**MGMT, "X-Memvine-Project": client.project_slug}


async def team_memories(client, team) -> list[dict]:
    listed = (await client.get("/v1/memories", headers={**MGMT, "X-Memvine-Project": team["slug"]})).json()
    return listed.get("items", listed) if isinstance(listed, dict) else listed


@pytest.mark.asyncio
async def test_share_from_the_console_and_take_it_back(client):
    app, launch, rule, team = await seeded(client)
    shared = await client.post(f"/v1/memories/{rule['id']}/share", json={"team": team["slug"]}, headers=console(client))
    assert shared.status_code == 200, shared.text
    body = shared.json()
    assert body["relation"] == "new" and body["team"] == team["slug"] and body["memory"]["shared_ref"] == body["team_memory_id"]
    (theirs,) = await team_memories(client, team)
    assert theirs["id"] == body["team_memory_id"] and theirs["statement"] == rule["statement"] and theirs["category"] == "rule"
    (evidence,) = theirs["evidence"]
    assert evidence["identity"] == "priya@northwind.example" and evidence["quote"] == "Refunds over $500 always need a human approver"
    assert evidence["episode_id"].startswith("shared:")
    # The team space still holds no session and none of the person's source.
    assert (await client.get("/v1/sessions", headers={**MGMT, "X-Memvine-Project": team["slug"]})).json() == []
    source = (await client.get(f"/v1/episodes/{evidence['episode_id']}", headers={**MGMT, "X-Memvine-Project": team["slug"]})).json()
    assert source["content"] == evidence["quote"]

    again = await client.post(f"/v1/memories/{rule['id']}/share", json={"team": team["slug"]}, headers=console(client))
    assert again.status_code == 409 and again.json()["error"]["code"] == "already-shared"
    # A project key reaches one project: it cannot share into another.
    by_key = await client.post(f"/v1/memories/{launch['id']}/share", json={"team": team["slug"]})
    assert by_key.status_code == 403 and by_key.json()["error"]["code"] == "share-needs-person"
    # Only a team space takes a share.
    not_team = await client.post(f"/v1/memories/{launch['id']}/share", json={"team": client.project_slug}, headers=console(client))
    assert not_team.status_code == 404
    assert (await client.post(f"/v1/memories/{launch['id']}/share", json={"team": "no-such-space"}, headers=console(client))).status_code == 404

    back = await client.delete(f"/v1/memories/{rule['id']}/share", headers=console(client))
    assert back.status_code == 200 and back.json()["taken_back"] is True and back.json()["memory"]["shared_ref"] is None
    assert [m["status"] for m in await team_memories(client, team)] == ["retired"]
    assert (await client.delete(f"/v1/memories/{rule['id']}/share", headers=console(client))).status_code == 400
    # The person's own memory is untouched, and can be shared again.
    assert (await client.get(f"/v1/memories/{rule['id']}")).json()["status"] == "active"
    assert (await client.post(f"/v1/memories/{rule['id']}/share", json={"team": team["slug"]}, headers=console(client))).status_code == 200


@pytest.mark.asyncio
async def test_what_a_teammate_also_shared_stays_when_one_takes_theirs_back(client):
    app, _, rule, team = await seeded(client)
    first = (await client.post(f"/v1/memories/{rule['id']}/share", json={"team": team["slug"]}, headers=console(client))).json()
    # A teammate shares the same rule from their own project, in their own words.
    app.state.llm.responses = {"): Refunds over $500 need a human approver": json.dumps({"match": 1, "relation": "same", "reason": "restated"}),
                               **app.state.llm.responses}
    theirs = await client.post("/v1/memories", headers=team["headers"], json={
        "statement": rule["statement"], "kind": rule["kind"], "category": "rule", "subjects": rule["subjects"],
        "said_by": "dana@northwind.example", "quote": "refunds over $500 need a human"})
    assert theirs.json()["relation"] == "same" and theirs.json()["memory"]["id"] == first["team_memory_id"]

    back = (await client.delete(f"/v1/memories/{rule['id']}/share", headers=console(client))).json()
    assert back["taken_back"] is False, "the teammate's support keeps it"
    (kept,) = await team_memories(client, team)
    assert kept["status"] == "active" and [e["identity"] for e in kept["evidence"]] == ["dana@northwind.example"]


@pytest.mark.asyncio
async def test_a_changed_memory_replaces_the_teams_copy(client):
    from memvine_server.main import app as the_app

    app, launch, _, team = await seeded(client)
    old = (await client.post(f"/v1/memories/{launch['id']}/share", json={"team": team["slug"]}, headers=console(client))).json()

    # The launch moves, said in a later session: the person's memory replaces its old value.
    app.state.llm.responses = {"moving launch from 29 Oct to 12 Nov": EXTRACT_2, **app.state.llm.responses}
    turns = SESSION_2.split("\n\n")
    events = [{"role": "user", "content": turns[0].split("\n", 1)[1], "at": "2026-10-03T14:02:00Z"},
              {"role": "assistant", "content": turns[1].split("\n", 1)[1], "at": "2026-10-03T14:03:00Z"}]
    assert (await client.post("/v1/sessions/ses_u/events", json={"events": events, "identity": "priya@northwind.example"})).status_code == 202
    await client.post("/v1/sessions/ses_u/close")
    async with the_app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(the_app.state.pool, the_app.state.llm, the_app.state.embedder, project_id)

    kept = (await client.get("/v1/sessions/ses_u/kept")).json()["kept"]
    moved = next(k for k in kept if k["statement"].startswith("Launch date is 12 Nov"))
    assert moved["supersedes"] == launch["id"] and moved["updates_shared"] and moved["suggest_share"], moved

    new = await client.post(f"/v1/memories/{moved['id']}/share", json={"team": team["slug"]}, headers=console(client))
    assert new.status_code == 200 and new.json()["relation"] == "update", new.text
    by_id = {m["id"]: m for m in await team_memories(client, team)}
    assert by_id[old["team_memory_id"]]["status"] == "superseded"
    assert by_id[new.json()["team_memory_id"]]["status"] == "active"
    assert by_id[new.json()["team_memory_id"]]["supersedes"] == old["team_memory_id"]
    recalled = (await client.post("/v1/recall", json={"query": "When is the launch?"}, headers=team["headers"])).json()
    assert recalled["items"][0]["memory"]["statement"].startswith("Launch date is 12 Nov") and "was: Launch date is 29 Oct" in recalled["briefing"]
