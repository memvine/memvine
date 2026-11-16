"""Personal projects (migration 0014): a project only its owner opens. Inside one organization, its
owner (a plain member) fills a personal project with something of every kind; the organization's
owner, an admin and an organization key then aim every project route at it. Nothing of it may
come back, nothing may change, and it is missing from every organization-wide view.

Routes come from the app, as in test_tenant_isolation: a new project route is covered at once."""
from __future__ import annotations

import json
import uuid

import pytest
from conftest import needs_db
from test_tenant_isolation import BODIES, MARKER, all_routes, bearer, fill, snapshot

pytestmark = needs_db
# What an organization sees of itself: these must leave a personal project out.
ORG_VIEWS = {("GET", "/v1/projects"), ("GET", "/v1/audit"), ("GET", "/v1/health/synthesis")}
# The snapshot's organization-wide parts: an owner or admin may change those; the project's own they may not.
ORG_STATE = {"projects", "org", "members", "invites", "org_keys", "user_sessions", "password"}


@pytest.mark.asyncio
async def test_nobody_else_in_the_organization_reaches_a_personal_project(client):
    from memvine_server.config import settings
    from memvine_server.main import app

    tag = uuid.uuid4().hex[:6]
    owner = (await client.post("/v1/auth/signup", json={"email": f"owner-{tag}@example.com", "password": "x" * 12, "name": "owner",
                                                        "organization": f"Northwind {tag}"})).json()
    ho = bearer(owner["token"])

    async def join(who: str, role: str) -> dict:
        invite = (await client.post("/v1/org/invites", json={"role": role}, headers=ho)).json()
        joined = await client.post("/v1/auth/signup", json={"email": f"{who}-{tag}@example.com", "password": "x" * 12, "invite": invite["token"]})
        assert joined.status_code in (200, 201), joined.text
        return bearer(joined.json()["token"])

    hp = await join("priya", "member")   # the person whose project it is
    hq = await join("quentin", "admin")  # an admin of the same organization
    org_key = (await client.post("/v1/org/keys", json={"name": "ci"}, headers=ho)).json()["key"]

    # A member cannot create one of the organization's projects, but can create their own.
    assert (await client.post("/v1/projects", json={"name": f"Shared {tag}"}, headers=hp)).status_code == 403
    assert (await client.post("/v1/projects", json={"name": "x", "personal": True}, headers=bearer(org_key))).status_code == 400
    assert (await client.post("/v1/projects", json={"name": "x", "personal": True, "team_space": True}, headers=hp)).status_code == 400
    made = await client.post("/v1/projects", json={"name": f"Quokka notes {tag}", "slug": f"mine-{tag}", "personal": True,
                                                   "description": "quokka billing"}, headers=hp)
    assert made.status_code == 201 and made.json()["personal"] is True, made.text
    slug = made.json()["slug"]
    mine = {**hp, "X-Memvine-Project": slug}

    # Its owner, a plain member, runs it like an owner: keys, settings, categories, deleting a session.
    key = (await client.post(f"/v1/projects/{slug}/keys", json={"name": "quokka laptop"}, headers=hp)).json()
    hk = {**bearer(key["key"]), "User-Agent": "quokka-agent/1.0"}
    assert (await client.patch(f"/v1/projects/{slug}", json={"memory_instructions": "Never keep quokka feeding times."}, headers=hp)).status_code == 200
    assert (await client.post(f"/v1/projects/{slug}/categories", json={"slug": "quokka-notes", "name": "Quokka notes",
                                                                       "description": "quokka husbandry"}, headers=hp)).status_code == 201
    assert (await client.post("/v1/sample", headers=hk)).status_code == 200
    assert (await client.post("/v1/ingest", json={"items": [{"id": f"ep-{tag}", "content": "The quokka ledger closes on Fridays.",
                                                             "timestamp": "2026-10-01T10:00:00Z"}]}, headers=hk)).status_code == 202
    assert (await client.post(f"/v1/sessions/sess-{tag}/events", json={"events": [{"role": "user", "content": "quokka plans"}]}, headers=hk)).status_code == 202
    assert (await client.post(f"/v1/sessions/gone-{tag}/events", json={"events": [{"role": "user", "content": "quokka scrap"}]}, headers=hk)).status_code == 202
    assert (await client.delete(f"/v1/sessions/gone-{tag}", headers=mine)).status_code in (200, 204), "its owner may delete a session"
    assert (await client.put(f"/v1/identities/ident-{tag}", json={"traits": {"team": "quokka"}}, headers=hk)).status_code == 200
    assert (await client.put(f"/v1/audiences/aud-{tag}", json={"kind": "fixed", "members": [f"ident-{tag}"]}, headers=hk)).status_code == 200
    app.state.llm.responses["NEW"] = json.dumps({"relation": "different", "reason": "new fact"})
    mem = (await client.post("/v1/memories", json={"statement": "The quokka invoice run starts at 02:00 UTC.", "kind": "semantic"}, headers=hk)).json()["memory"]
    threads = (await client.get("/v1/threads", headers=hk)).json()
    jobs = (await client.get("/v1/jobs", headers=hk)).json()["jobs"]

    import psycopg
    from psycopg.rows import dict_row
    async with await psycopg.AsyncConnection.connect(settings.database_url, row_factory=dict_row, autocommit=True) as conn:
        await conn.execute("UPDATE jobs SET status = 'failed', finished_at = now() WHERE id = %s", (jobs[0]["id"],))  # shows in health
        b = await (await conn.execute("SELECT p.id AS project_id, p.org_id, p.owner_user_id AS user_id FROM projects p WHERE p.slug = %s", (slug,))).fetchone()
    assert b["user_id"], "the project has an owner"
    ids = {"slug": slug, "episode_id": f"ep-{tag}", "session_id": f"sess-{tag}", "identity": f"ident-{tag}", "memory_id": mem["id"],
           "thread_id": threads[0]["id"] if threads else uuid.uuid4(), "job_id": jobs[0]["id"], "category": "quokka-notes",
           "key_id": key["id"]}

    # Priya sees her project everywhere she should.
    assert slug in [p["slug"] for p in (await client.get("/v1/projects", headers=hp)).json()]
    assert MARKER in (await client.get("/v1/memories", headers=mine)).text.lower()
    assert (await client.get("/v1/sessions", headers=mine)).json()

    before = await snapshot(settings.database_url, b)
    assert before["memories"] and before["sessions"] and before["episodes"] and before["categories"]

    callers = {"the organization's owner": ho, "an admin": hq, "an organization key": bearer(org_key)}
    routes = {(m, r.path): r for r in all_routes(app) for m in r.methods if m != "HEAD"}
    aimed = {k: v for k, v in routes.items()
             if "require_project" in {d.call.__name__ for d in v.dependant.dependencies} or "{slug}" in k[1] or k in ORG_VIEWS}
    assert len(aimed) > 50 and set(aimed) <= set(BODIES), "every project route, each with a body in test_tenant_isolation"
    leaks, reached = [], []
    for who, headers in callers.items():
        for (method, path) in aimed:
            route_ids = {**ids, "rand": uuid.uuid4().hex[:6]}
            if path.startswith("/v1/audiences"):
                route_ids["slug"] = f"aud-{tag}"
            url = fill(path, route_ids) if "{" in path else path
            params = {} if (method, path) in ORG_VIEWS else {"project": slug}
            if path == "/v1/projects/{slug}" and method == "DELETE":
                params["confirm"] = slug
            if path == "/v1/code/anchors":
                params["repo"] = "northwind/billing"
            if path == "/v1/profiles":
                params.update(subject_kind="person", subject=ids["identity"])
            if path == "/v1/events":
                params["memory_id"] = ids["memory_id"]
            r = await client.request(method, url, params=params, json=fill(BODIES[(method, path)], route_ids), headers=headers)
            if MARKER in r.text.lower() or slug in r.text:
                leaks.append(f"{who}: {method} {url} -> {r.status_code}: {r.text[:200]}")
            if (method, path) not in ORG_VIEWS and r.status_code != 404:
                reached.append(f"{who}: {method} {url} -> {r.status_code}")

    after = await snapshot(settings.database_url, b)
    changed = [name for name in before if name not in ORG_STATE and before[name] != after[name]]
    assert not leaks, "\n".join(leaks)
    assert not reached, "\n".join(reached)
    assert not changed, f"the personal project changed: {changed}"

    # A name it uses is free for the organization, and taking it says nothing about it.
    same_name = await client.post("/v1/projects", json={"name": f"Quokka notes {tag}"}, headers=hq)
    assert same_name.status_code == 201 and same_name.json()["slug"] != slug, same_name.text
    # It still works for her, by key and in the console.
    assert (await client.get(f"/v1/memories/{mem['id']}", headers=hk)).status_code == 200
    assert (await client.get(f"/v1/memories/{mem['id']}", headers=mine)).status_code == 200
    health = (await client.get("/v1/health/synthesis", params={"project": slug}, headers=hp)).json()
    assert slug in json.dumps(health), "its owner sees its failed job"

    # She deletes it: the organization's audit log does not name it even then.
    assert (await client.delete(f"/v1/projects/{slug}", params={"confirm": slug}, headers=hp)).status_code == 204
    audit = (await client.get("/v1/audit", params={"limit": 1000}, headers=ho)).text
    assert slug not in audit, [line for line in json.loads(audit) if slug in json.dumps(line)]
    assert "invite.create" in audit, "the organization's own lines are still there"

    async with await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True) as conn:
        await conn.execute("DELETE FROM projects WHERE org_id = %s", (b["org_id"],))
