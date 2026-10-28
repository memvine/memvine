"""Tenant isolation across every route: callers from organization A (a project key, a signed-in owner,
an organization key) aim every route in the app at organization B's project, memories, sources,
sessions, keys, audiences, identities, categories, members, invites and jobs. Nothing of B may come back, and
nothing of B may change.

The test enumerates the app's routes, so a new route fails it until it is listed below with a body
(or as one that acts only on the caller's own account)."""
from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from conftest import needs_db
from fastapi.routing import APIRoute
from psycopg.rows import dict_row

pytestmark = needs_db

MARKER = "quokka"  # in everything B holds; never in what A sends


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def all_routes(app) -> list[APIRoute]:
    def walk(routes):
        for r in routes:
            if isinstance(r, APIRoute):
                yield r
            elif hasattr(r, "original_router"):  # an included router (accounts)
                yield from walk(r.original_router.routes)
            elif hasattr(r, "routes") and not hasattr(r, "app"):
                yield from walk(r.routes)
    return list(walk(app.routes))


# Bodies that pass validation, so each route really runs against B's ids. {ids} are filled in.
BODIES: dict[tuple[str, str], dict | None] = {
    ("GET", "/v1/health/synthesis"): None,
    ("POST", "/v1/ingest"): {"items": [{"id": "{episode_id}", "content": "Attacker text about invoices.", "timestamp": "2026-10-02T10:00:00Z"}]},
    ("POST", "/v1/recall"): {"query": "quokka invoices launch refunds", "identity": "{identity}", "profiles": True},
    ("POST", "/v1/sessions/{session_id}/events"): {"events": [{"role": "user", "content": "attacker turn"}]},
    ("GET", "/v1/sessions"): None,
    ("DELETE", "/v1/sessions/{session_id}"): None,
    ("GET", "/v1/sessions/{session_id}"): None,
    ("GET", "/v1/sessions/{session_id}/brief"): None,
    ("POST", "/v1/sessions/{session_id}/close"): None,
    ("GET", "/v1/sessions/{session_id}/kept"): None,
    ("GET", "/v1/profiles"): None,
    ("GET", "/v1/profiles/project"): None,
    ("POST", "/v1/profiles/rebuild"): {"subject_kind": "person", "subject": "{identity}"},
    ("PUT", "/v1/identities/{identity}"): {"traits": {"team": "attackers"}},
    ("GET", "/v1/identities"): None,
    ("GET", "/v1/identities/{identity}"): None,
    ("DELETE", "/v1/identities/{identity}"): None,
    ("PUT", "/v1/audiences/{slug}"): {"kind": "fixed", "members": ["{identity}"]},
    ("GET", "/v1/audiences"): None,
    ("DELETE", "/v1/audiences/{slug}"): None,
    ("GET", "/v1/memories"): None,
    ("GET", "/v1/threads"): None,
    ("GET", "/v1/threads/{thread_id}"): None,
    ("POST", "/v1/memories"): {"statement": "Attackers replaced this fact.", "supersedes": "{memory_id}"},
    ("GET", "/v1/memories/{memory_id}"): None,
    ("GET", "/v1/memories/{memory_id}/related"): None,
    ("GET", "/v1/memories/{memory_id}/history"): None,
    ("GET", "/v1/events"): None,
    ("GET", "/v1/graph"): None,
    ("PATCH", "/v1/memories/{memory_id}"): {"status": "retired", "confirm": True, "flag": None},
    ("GET", "/v1/code/anchors"): None,
    ("POST", "/v1/code/check"): {"repo": "northwind/billing", "files": {"src/invoices.py": None}},
    ("POST", "/v1/export"): None,
    ("GET", "/v1/feedback-cases"): None,
    ("DELETE", "/v1/episodes/{episode_id}"): None,
    ("POST", "/v1/sample"): None,
    ("DELETE", "/v1/sample"): None,
    ("GET", "/v1/jobs"): None,
    ("POST", "/v1/jobs/{job_id}/retry"): None,
    ("GET", "/v1/episodes"): None,
    ("GET", "/v1/episodes/{episode_id}"): None,
    ("GET", "/v1/requests"): None,
    ("GET", "/v1/usage"): None,
    ("POST", "/v1/projects"): {"name": "attacker project {rand}"},
    ("GET", "/v1/projects"): None,
    ("PATCH", "/v1/projects/{slug}"): {"name": "hijacked", "processing_paused": True, "monthly_credit_cap": 0,
                                       "memory_instructions": "Keep everything."},
    ("DELETE", "/v1/projects/{slug}"): None,
    ("GET", "/v1/projects/{slug}/categories"): None,
    ("POST", "/v1/projects/{slug}/categories"): {"slug": "atk-{rand}", "name": "attacker category"},
    ("PATCH", "/v1/projects/{slug}/categories/{category}"): {"name": "hijacked", "description": "attacker"},
    ("DELETE", "/v1/projects/{slug}/categories/{category}"): None,
    ("POST", "/v1/projects/{slug}/consolidate"): None,
    ("GET", "/v1/projects/{slug}/consolidation-runs"): None,
    ("GET", "/v1/projects/{slug}/keys"): None,
    ("POST", "/v1/projects/{slug}/keys"): {"name": "attacker key"},
    ("DELETE", "/v1/projects/{slug}/keys/{key_id}"): None,
    ("GET", "/v1/projects/{slug}/keys/{key_id}/connection"): None,
    ("GET", "/v1/org"): None,
    ("POST", "/v1/orgs"): {"name": "attacker org {rand}"},
    ("GET", "/v1/orgs"): None,
    ("GET", "/v1/audit"): None,
    ("GET", "/v1/org/keys"): None,
    ("POST", "/v1/org/keys"): {"name": "attacker org key"},
    ("DELETE", "/v1/org/keys/{key_id}"): None,
    ("GET", "/v1/org/members"): None,
    ("PATCH", "/v1/org/members/{user_id}"): {"role": "member"},
    ("DELETE", "/v1/org/members/{user_id}"): None,
    ("GET", "/v1/org/invites"): None,
    ("POST", "/v1/org/invites"): {"role": "member"},
    ("DELETE", "/v1/org/invites/{invite_id}"): None,
    ("POST", "/v1/auth/invites/describe"): {"invite": "{invite_token}"},
    ("POST", "/v1/auth/accept"): {"invite": "{invite_token}"},
}
# Routes that act only on the caller's own account or are public without tenant data; covered by
# test_accounts and by the switch check below.
OWN_ACCOUNT = {
    ("GET", "/health"), ("GET", "/v1/auth/config"), ("POST", "/v1/auth/signup"), ("POST", "/v1/auth/login"),
    ("POST", "/v1/auth/logout"), ("POST", "/v1/auth/switch"), ("POST", "/v1/auth/password"),
}


def fill(template, ids: dict):
    if template is None:
        return None
    text = json.dumps(template)
    for name, value in ids.items():
        text = text.replace("{" + name + "}", str(value))
    return json.loads(text)


async def snapshot(dsn: str, b: dict) -> dict:
    """Everything of B's that a leak could change (last-used timestamps aside)."""
    queries = {
        "project": "SELECT slug, name, tags, monthly_credit_cap, retention_days, processing_paused, description, recall_min_relevance, "
                   "recall_half_life_days, memory_instructions FROM projects WHERE id = %(pid)s",
        "categories": "SELECT slug, name, description FROM project_categories WHERE project_id = %(pid)s ORDER BY slug",
        "memories": "SELECT id, statement, status, review, assertion, superseded_by, valid_to, flag, retired_reason, merged_into FROM memory_items WHERE project_id = %(pid)s ORDER BY id",
        "episodes": "SELECT id, content, audience FROM episodes WHERE project_id = %(pid)s ORDER BY id",
        "sessions": "SELECT id, status, turns FROM sessions WHERE project_id = %(pid)s ORDER BY id",
        "events": "SELECT count(*) AS n FROM session_events WHERE project_id = %(pid)s",
        "identities": "SELECT identity, traits FROM identities WHERE project_id = %(pid)s ORDER BY identity",
        "audiences": "SELECT slug, kind, members, conditions FROM audiences WHERE project_id = %(pid)s ORDER BY slug",
        "keys": "SELECT id, name, revoked_at FROM api_keys WHERE project_id = %(pid)s ORDER BY id",
        "jobs": "SELECT id, status, attempts FROM jobs WHERE project_id = %(pid)s ORDER BY id",
        "threads": "SELECT id, title, status FROM threads WHERE project_id = %(pid)s ORDER BY id",
        "feedback": "SELECT count(*) AS n FROM feedback_cases WHERE project_id = %(pid)s",
        "consolidation_runs": "SELECT id, finished_at, error FROM consolidation_runs WHERE project_id = %(pid)s ORDER BY id",
        "memory_events": "SELECT id, memory_id, kind, actor FROM memory_events WHERE project_id = %(pid)s ORDER BY id",
        "profiles": "SELECT subject_kind, subject, lines, dirty, built_at FROM profiles WHERE project_id = %(pid)s ORDER BY subject_kind, subject",
        "projects": "SELECT id FROM projects WHERE org_id = %(oid)s ORDER BY id",
        "org": "SELECT slug, name FROM organizations WHERE id = %(oid)s",
        "members": "SELECT user_id, role FROM memberships WHERE org_id = %(oid)s ORDER BY user_id",
        "invites": "SELECT id, accepted_at, revoked_at FROM invites WHERE org_id = %(oid)s ORDER BY id",
        "org_keys": "SELECT id, name, revoked_at FROM org_keys WHERE org_id = %(oid)s ORDER BY id",
        "user_sessions": "SELECT id, revoked_at FROM user_sessions WHERE org_id = %(oid)s ORDER BY id",
        "password": "SELECT password_hash FROM users WHERE id = %(uid)s",
    }
    out = {}
    async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
        for name, sql in queries.items():
            out[name] = await (await conn.execute(sql, {"pid": b["project_id"], "oid": b["org_id"], "uid": b["user_id"]})).fetchall()
    return out


@pytest.mark.asyncio
async def test_no_route_reaches_another_organization(client):
    from memvine_server.config import settings
    from memvine_server.main import app

    tag = uuid.uuid4().hex[:6]
    signup = lambda who, org: {"email": f"{who}-{tag}@example.com", "password": "x" * 12, "name": who, "organization": org}  # noqa: E731

    # Organization B, holding something of every kind, all marked.
    sb = (await client.post("/v1/auth/signup", json=signup("quokka-owner", "Quokka Org"))).json()
    hb = bearer(sb["token"])
    pb = (await client.post("/v1/projects", json={"name": f"Quokka project {tag}", "slug": f"b-{tag}", "description": "quokka billing"}, headers=hb)).json()
    kb = (await client.post(f"/v1/projects/{pb['slug']}/keys", json={"name": "quokka key"}, headers=hb)).json()
    okb = (await client.post("/v1/org/keys", json={"name": "quokka org key"}, headers=hb)).json()
    assert (await client.patch(f"/v1/projects/{pb['slug']}", json={"memory_instructions": "Never keep quokka feeding times."},
                               headers=hb)).status_code == 200
    assert (await client.post(f"/v1/projects/{pb['slug']}/categories", json={"slug": "quokka-notes", "name": "Quokka notes",
                              "description": "quokka husbandry"}, headers=hb)).status_code == 201
    hk = {**bearer(kb["key"]), "User-Agent": "quokka-agent/1.0"}  # stored on B's key
    assert (await client.post("/v1/sample", headers=hk)).status_code == 200
    assert (await client.post("/v1/ingest", json={"items": [{"id": f"ep-{tag}", "content": "The quokka ledger closes on Fridays.", "timestamp": "2026-10-01T10:00:00Z"}]},
                              headers=hk)).status_code == 202
    assert (await client.post(f"/v1/sessions/sess-{tag}/events", json={"events": [{"role": "user", "content": "quokka plans"}]},
                              headers=hk)).status_code == 202
    assert (await client.put(f"/v1/identities/ident-{tag}", json={"traits": {"team": "quokka"}}, headers=hk)).status_code == 200
    assert (await client.put(f"/v1/audiences/aud-{tag}", json={"kind": "fixed", "members": [f"ident-{tag}"]}, headers=hk)).status_code == 200
    app.state.llm.responses["NEW"] = json.dumps({"relation": "different", "reason": "new fact"})
    mem = (await client.post("/v1/memories", json={"statement": "The quokka invoice run starts at 02:00 UTC.", "kind": "semantic"},
                             headers=hk)).json()["memory"]
    invite = (await client.post("/v1/org/invites", json={"role": "admin"}, headers=hb)).json()
    threads = (await client.get("/v1/threads", headers=hk)).json()
    jobs = (await client.get("/v1/jobs", headers=hk)).json()["jobs"]
    members = (await client.get("/v1/org/members", headers=hb)).json()
    async with await psycopg.AsyncConnection.connect(settings.database_url, row_factory=dict_row, autocommit=True) as conn:
        await conn.execute("UPDATE jobs SET status = 'failed' WHERE id = %s", (jobs[0]["id"],))  # retryable
        # Something consolidation flagged and a run of it, for the flag and consolidation routes to reach for.
        await conn.execute("UPDATE memory_items SET flag = 'possible_duplicate', flag_reason = 'quokka' WHERE id = %s", (mem["id"],))
        await conn.execute("INSERT INTO consolidation_runs (project_id, finished_at, stats) "
                           "SELECT id, now(), '{\"merged\": 1}' FROM projects WHERE slug = %s", (pb["slug"],))
        line = json.dumps([{"text": "Runs the quokka ledger.", "memory_ids": [mem["id"]]}])
        await conn.execute("INSERT INTO profiles (project_id, subject_kind, subject, lines, dirty, built_at) "
                           "SELECT id, k, s, %s, false, now() FROM projects, (VALUES ('person', %s), ('project', '')) v(k, s) "
                           "WHERE slug = %s", (line, f"ident-{tag}", pb["slug"]))
        b = await (await conn.execute("SELECT p.id AS project_id, p.org_id, u.id AS user_id FROM projects p, users u "
                                      "WHERE p.slug = %s AND u.email = %s", (pb["slug"], f"quokka-owner-{tag}@example.com"))).fetchone()
    ids = {
        "slug": pb["slug"], "episode_id": f"ep-{tag}", "session_id": f"sess-{tag}", "identity": f"ident-{tag}",
        "memory_id": mem["id"], "thread_id": threads[0]["id"] if threads else uuid.uuid4(), "job_id": jobs[0]["id"],
        "user_id": members[0]["id"], "invite_id": invite["id"], "invite_token": "mv_in_not-a-real-invite", "category": "quokka-notes",
    }
    project_key_id = (await client.get(f"/v1/projects/{pb['slug']}/keys", headers=hb)).json()[0]["id"]
    before = await snapshot(settings.database_url, b)
    assert before["memories"] and before["threads"] and before["identities"] and before["sessions"] and before["categories"]

    # Organization A: an owner, a project, a project key, an organization key.
    sa = (await client.post("/v1/auth/signup", json=signup("a-owner", "Org A"))).json()
    ha = bearer(sa["token"])
    pa = (await client.post("/v1/projects", json={"name": f"A project {tag}"}, headers=ha)).json()
    ka = (await client.post(f"/v1/projects/{pa['slug']}/keys", json={"name": "a key"}, headers=ha)).json()["key"]
    oka = (await client.post("/v1/org/keys", json={"name": "a org key"}, headers=ha)).json()["key"]
    callers = {
        "A project key": bearer(ka),
        "A session -> B project": {**ha, "X-Memvine-Project": pb["slug"]},
        "A session -> own project": {**ha, "X-Memvine-Project": pa["slug"]},
        "A org key -> B project": {**bearer(oka), "X-Memvine-Project": pb["slug"]},
        "A org key -> own project": {**bearer(oka), "X-Memvine-Project": pa["slug"]},
    }

    routes = {(m, r.path): r for r in all_routes(app) for m in r.methods if m != "HEAD"}
    unlisted = set(routes) - set(BODIES) - OWN_ACCOUNT
    assert not unlisted, f"routes not covered by the isolation test: {sorted(unlisted)}"

    leaks, wrong_project = [], []
    for who, headers in callers.items():
        for (method, path), template in BODIES.items():
            route_ids = {**ids, "rand": uuid.uuid4().hex[:6],
                         "key_id": okb["id"] if path.startswith("/v1/org/keys") else project_key_id}
            if path.startswith("/v1/audiences"):
                route_ids["slug"] = f"aud-{tag}"
            url = fill(path, route_ids) if "{" in path else path
            params = {"project": pb["slug"]}
            if path == "/v1/projects/{slug}" and method == "DELETE":
                params["confirm"] = pb["slug"]
            if path == "/v1/code/anchors":
                params["repo"] = "northwind/billing"
            if path == "/v1/profiles":
                params.update(subject_kind="person", subject=ids["identity"])
            if path == "/v1/events":
                params["memory_id"] = ids["memory_id"]
            r = await client.request(method, url, params=params, json=fill(template, route_ids), headers=headers)
            if MARKER in r.text.lower():
                leaks.append(f"{who}: {method} {url} -> {r.status_code}: {r.text[:200]}")
            dep = {d.call.__name__ for d in routes[(method, path)].dependant.dependencies}
            if "require_project" in dep and who.endswith("-> B project") and r.status_code != 404:
                wrong_project.append(f"{who}: {method} {url} -> {r.status_code}")

    # Switching a session into B's organization is refused.
    assert (await client.post("/v1/auth/switch", json={"org": sb["org"]["slug"]}, headers=ha)).status_code == 404

    # MCP: A's project key cannot reach B's memory or sessions.
    mcp_headers = {**bearer(ka), "Accept": "application/json, text/event-stream"}
    for name, arguments in [("memory_recall", {"query": "quokka invoice run"}), ("memory_forget", {"memory_id": mem["id"]}),
                            ("memory_session_brief", {"session_id": f"sess-{tag}"})]:
        r = await client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                             "params": {"name": name, "arguments": arguments}}, headers=mcp_headers)
        assert r.status_code == 200, r.text
        if MARKER in r.text.lower():
            leaks.append(f"MCP {name}: {r.text[:200]}")

    after = await snapshot(settings.database_url, b)
    changed = [name for name in before if before[name] != after[name]]
    assert not leaks, "\n".join(leaks)
    assert not wrong_project, "\n".join(wrong_project)
    assert not changed, f"organization B changed: {changed}"
    # B still works.
    assert (await client.get(f"/v1/memories/{mem['id']}", headers=hk)).status_code == 200

    async with await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True) as conn:
        await conn.execute("DELETE FROM projects WHERE org_id IN (%s, (SELECT org_id FROM projects WHERE slug = %s))",
                           (b["org_id"], pa["slug"]))
