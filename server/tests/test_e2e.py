"""Ingest two sessions, synthesize them, and recall: the launch date must come
back as 12 Nov with 29 Oct as the previous value, and an agent's "all tests
pass" must rank below everything else."""
from __future__ import annotations

import json
import uuid

import pytest
from conftest import needs_db

from memvine_server.worker import drain

pytestmark = needs_db

SESSION_1 = (
    "## Developer · 2026-09-18T10:11\nTarget launch is 29 Oct. Refunds over $500 always need a human approver, "
    "the bot can only draft them.\n\n## Claude · 2026-09-18T10:12\nNoted: launch 29 Oct, and refunds above $500 route to a human approver."
)
SESSION_2 = (
    "## Developer · 2026-10-03T14:02\nLegal pushed back on the refund copy, so we're moving launch from 29 Oct to 12 Nov.\n\n"
    "## Claude · 2026-10-03T14:03\nUnderstood. I'll update the release checklist to 12 Nov. Implementation complete, all tests pass."
)

EXTRACT_1 = json.dumps([
    {"statement": "Launch date is 29 Oct 2026.", "kind": "semantic", "category": "fact", "subjects": ["launch"], "assertion": "stated",
     "confidence": "high", "valid_from": "2026-09-18", "quote": "Target launch is 29 Oct."},
    {"statement": "Refunds over $500 need a human approver; the bot may only draft them.", "kind": "procedural", "category": "rule",
     "subjects": ["refunds"], "assertion": "stated", "confidence": "high", "quote": "Refunds over $500 always need a human approver"},
])
EXTRACT_2 = json.dumps([
    {"statement": "Launch date is 12 Nov 2026, moved from 29 Oct because legal pushed back on the refund copy.",
     "kind": "semantic", "category": "fact", "subjects": ["launch", "legal"], "assertion": "stated", "confidence": "high",
     "valid_from": "2026-10-03", "quote": "moving launch from 29 Oct to 12 Nov"},
    {"statement": "The release checklist update is complete and all tests pass.", "kind": "episodic", "category": "fact",
     "subjects": ["launch"], "assertion": "claimed", "confidence": "medium", "quote": "Implementation complete, all tests pass."},
])


def wire_fake_llm(app):
    """Route the fake LLM: extraction by episode content, reconciliation by statement pair."""
    llm = app.state.llm
    llm.responses = {
        "Target launch is 29 Oct": EXTRACT_1,
        "moving launch from 29 Oct to 12 Nov": EXTRACT_2,
        # Reconciliation prompts contain both statements; key on the new one.
        "NEW (2026-10-03): Launch date is 12 Nov 2026": json.dumps({"match": 1, "relation": "update", "reason": "date moved"}),
        "NEW (2026-10-03): The release checklist": json.dumps({"relation": "different", "reason": "progress note"}),
        "Subject: launch": "Launch is set for 12 Nov 2026 (was 29 Oct until 3 Oct 2026); legal pushed back on the refund copy.\n- 18 Sep 2026: target 29 Oct\n- 3 Oct 2026: moved to 12 Nov",
    }
    llm.default = "[]"


async def ingest_and_synthesize(client, app, episodes):
    wire_fake_llm(app)
    r = await client.post("/v1/ingest", json={"items": episodes})
    assert r.status_code == 202, r.text
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    ran = await drain(app.state.pool, app.state.llm, app.state.embedder, project_id)
    jobs = (await client.get("/v1/jobs")).json()["jobs"]
    failed = [j for j in jobs if j["status"] == "failed"]
    assert not failed, failed
    return ran, jobs


@pytest.mark.asyncio
async def test_ingest_synthesize_recall(client):
    from memvine_server.main import app

    ran, jobs = await ingest_and_synthesize(client, app, [
        {"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z", "identity": "priya@example.com", "label": "Claude Code · main"},
        {"id": "s2", "content": SESSION_2, "timestamp": "2026-10-03T14:02:00Z", "identity": "priya@example.com", "label": "Claude Code · main"},
    ])
    assert ran == 2, "one synthesis job per source"
    relations = [j["result"]["relations"] for j in jobs if "relations" in j["result"]]
    assert {"update": 1, "different": 1} in relations or {"update": 1, "new": 1} in relations

    memories = (await client.get("/v1/memories")).json()
    by_statement = {m["statement"][:20]: m for m in memories}
    old = by_statement["Launch date is 29 Oc"]
    new = by_statement["Launch date is 12 No"]
    assert old["status"] == "superseded" and old["superseded_by"] == new["id"]
    assert new["supersedes"] == old["id"] and old["valid_to"] is not None
    assert by_statement["The release checklis"]["assertion"] == "claimed"
    assert all(m["evidence"] for m in memories), "every memory carries its quote"

    # Current question: only the current value, with the previous one inline.
    r = (await client.post("/v1/recall", json={"query": "When is the launch?"})).json()
    assert not r["abstained"]
    assert r["briefing"].startswith("=== memvine")
    first = r["items"][0]
    assert first["memory"]["statement"].startswith("Launch date is 12 Nov")
    assert first["previous"].startswith("Launch date is 29 Oct")
    assert "supersedes 18 Sep] Launch date is 12 Nov 2026" in r["briefing"]
    assert "(was: Launch date is 29 Oct 2026)" in r["briefing"]
    assert all(i["memory"]["status"] != "superseded" for i in r["items"])
    # The agent's claim ranks below the stated facts.
    statements = [i["memory"]["statement"] for i in r["items"]]
    claim_pos = next((i for i, s in enumerate(statements) if "all tests pass" in s), None)
    assert claim_pos is None or claim_pos == len(statements) - 1

    # History question: the superseded value comes back too, marked.
    r = (await client.post("/v1/recall", json={"query": "What was the launch date before it changed?"})).json()
    assert any(i["memory"]["status"] == "superseded" for i in r["items"])
    # Said once, as the replaced value on the current line, not again on a line of its own.
    assert r["briefing"].count("Launch date is 29 Oct 2026") == 1 and "(was: Launch date is 29 Oct 2026)" in r["briefing"]

    # Point-in-time question.
    r = (await client.post("/v1/recall", json={"query": "launch date", "as_of": "2026-09-25T00:00:00Z"})).json()
    assert [i["memory"]["statement"][:20] for i in r["items"]] == ["Launch date is 29 Oc"]
    # At that moment it was the current value: the later replacement does not show on it.
    then = r["items"][0]["memory"]
    assert then["status"] == "active" and then["valid_to"] is None and "superseded" not in r["briefing"]

    # Budget and exclusion are honoured; nothing relevant means abstain.
    r = (await client.post("/v1/recall", json={"query": "When is the launch?", "exclude": [new["id"]]})).json()
    assert new["id"] not in [i["memory"]["id"] for i in r["items"]]
    r = (await client.post("/v1/recall", json={"query": "quantum giraffe weather"})).json()
    assert r["abstained"] and r["briefing"] == ""


@pytest.mark.asyncio
async def test_resend_replaces_and_delete_restores(client):
    from memvine_server.main import app

    await ingest_and_synthesize(client, app, [
        {"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"},
        {"id": "s2", "content": SESSION_2, "timestamp": "2026-10-03T14:02:00Z"},
    ])
    # Same content again: nothing happens.
    r = (await client.post("/v1/ingest", json={"items": [{"id": "s2", "content": SESSION_2, "timestamp": "2026-10-03T14:02:00Z"}]})).json()
    assert r["items"][0]["outcome"] == "unchanged" and r["queued"] == 0

    # Deleting the second episode removes what it taught and restores the old launch date.
    assert (await client.delete("/v1/episodes/s2")).status_code == 204
    memories = (await client.get("/v1/memories")).json()
    statements = {m["statement"][:20]: m for m in memories}
    assert "Launch date is 12 No" not in statements
    assert statements["Launch date is 29 Oc"]["status"] == "active"
    assert statements["Launch date is 29 Oc"]["superseded_by"] is None


@pytest.mark.asyncio
async def test_memory_correction_and_auth(client):
    from memvine_server.main import app

    await ingest_and_synthesize(client, app, [{"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}])
    mem = (await client.get("/v1/memories")).json()[0]
    patched = (await client.patch(f"/v1/memories/{mem['id']}", json={"status": "retired"})).json()
    assert patched["status"] == "retired" and patched["valid_to"]
    r = (await client.post("/v1/recall", json={"query": mem["statement"]})).json()
    assert mem["id"] not in [i["memory"]["id"] for i in r["items"]]

    bad = await client.get("/v1/memories", headers={"Authorization": "Bearer mv_sk_nope"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "unknown-api-key"
    none = await client.get("/v1/memories", headers={"Authorization": ""})
    assert none.status_code == 401


@pytest.mark.asyncio
async def test_console_routes_with_management_key(client):
    """The console acts on a project with the management key plus a project header."""
    from memvine_server.main import app

    await ingest_and_synthesize(client, app, [{"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}])
    slug = client.project_slug
    mgmt = {"Authorization": "Bearer mv_mk_test", "X-Memvine-Project": slug}

    keys = (await client.get(f"/v1/projects/{slug}/keys", headers=mgmt)).json()
    assert [k["name"] for k in keys] == ["test"] and keys[0]["key"] is None

    episodes = (await client.get("/v1/episodes", headers=mgmt)).json()
    assert episodes[0]["id"] == "s1" and episodes[0]["job_status"] == "done" and episodes[0]["memory_count"] == 2
    assert (await client.get("/v1/episodes/s1", headers=mgmt)).json()["content"] == SESSION_1

    requests = (await client.get("/v1/requests", headers=mgmt)).json()
    assert any(r["path"] == "/v1/ingest" and r["key_name"] == "test" for r in requests)
    usage = (await client.get("/v1/usage?days=7", headers=mgmt)).json()
    assert len(usage) == 7 and usage[-1]["ingest"] >= 1

    memories = (await client.get("/v1/memories", headers=mgmt)).json()
    assert len(memories) == 2

    missing = await client.get("/v1/memories", headers={"Authorization": "Bearer mv_mk_test"})
    assert missing.status_code == 400 and missing.json()["error"]["code"] == "project-required"
    wrong = await client.get("/v1/memories", headers={"Authorization": "Bearer mv_mk_wrong", "X-Memvine-Project": slug})
    assert wrong.status_code == 403
    no_retry = await client.post("/v1/jobs/999999/retry", headers=mgmt)
    assert no_retry.status_code == 404


@pytest.mark.asyncio
async def test_one_job_per_project_at_a_time(client):
    """claim_job never hands out a second job of a project that already has one running,
    but does hand out another project's job."""
    from memvine_server.main import app
    from memvine_server.worker import claim_job

    wire_fake_llm(app)
    await client.post("/v1/ingest", json={"items": [
        {"id": "a", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"},
        {"id": "b", "content": SESSION_2, "timestamp": "2026-10-03T14:02:00Z"},
    ]})
    other = f"o-{uuid.uuid4().hex[:8]}"
    mgmt = {"Authorization": "Bearer mv_mk_test"}
    await client.post("/v1/projects", json={"name": other, "slug": other}, headers=mgmt)
    other_key = (await client.post(f"/v1/projects/{other}/keys", json={"name": "k"}, headers=mgmt)).json()["key"]
    await client.post("/v1/ingest", json={"items": [{"id": "c", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}]},
                      headers={"Authorization": f"Bearer {other_key}"})
    try:
        async with app.state.pool.connection() as conn:
            ids = {r["slug"]: r["id"] for r in await (await conn.execute(
                "SELECT slug, id FROM projects WHERE slug = ANY(%s)", ([client.project_slug, other],))).fetchall()}
            mine, theirs = ids[client.project_slug], ids[other]
            first = await claim_job(conn, mine)
            second = await claim_job(conn, theirs)
            third = await claim_job(conn, mine)
            assert first["episode_id"] == "a"
            assert second["episode_id"] == "c", "the other project's job runs alongside"
            assert third is None, "project with a running job yields nothing more"
            await conn.execute("UPDATE jobs SET status = 'done' WHERE id = %s", (first["id"],))
            assert (await claim_job(conn, mine))["episode_id"] == "b"
    finally:
        async with app.state.pool.connection() as conn:
            await conn.execute("DELETE FROM projects WHERE slug = %s", (other,))


@pytest.mark.asyncio
async def test_session_memory_round_trip(client):
    """Turns stream in, the state refreshes after a few turns, a brief returns state plus relevant
    history, and closing the session turns its turns into episodes for extraction."""
    from memvine_server.main import app

    # Long-term memory to recall into the brief. (ingest_and_synthesize wires the fake LLM.)
    await ingest_and_synthesize(client, app, [{"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}])
    app.state.llm.responses["Previous state:"] = json.dumps({
        "goal": "Route tier-2 escalations to Slack", "decisions": ["Use #support-tier2"],
        "open_questions": ["Page on-call at weekends?", "When is the launch?"], "in_play": ["src/escalation/router.ts"],
        "recent_facts": ["Zendesk webhook retries 3 times"], "progress_claims": ["Implementation complete"],
    })

    turns = [
        {"role": "user", "content": "Let's route tier-2 escalations to Slack instead of email."},
        {"role": "assistant", "content": "I'll change src/escalation/router.ts to post to #support-tier2."},
        {"role": "user", "content": "Should weekend escalations page on-call? Also launch is coming up."},
        {"role": "assistant", "content": "Implementation complete. I'll leave weekend paging as an open question."},
    ]
    r = await client.post("/v1/sessions/ses_1/events", json={"events": turns[:3], "agent": "Claude Code", "identity": "sam@example.com"})
    assert r.status_code == 202 and r.json()["turns"] == 3 and r.json()["state_through"] == 0
    r = await client.post("/v1/sessions/ses_1/events", json={"events": turns[3:]})
    assert r.json()["turns"] == 4
    jobs = (await client.get("/v1/jobs")).json()["jobs"]
    assert any(j["status"] == "queued" for j in jobs), "the fourth turn queued a state refresh"
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, project_id)

    s = (await client.get("/v1/sessions/ses_1")).json()
    assert s["status"] == "live" and s["state_through"] == 4
    assert s["state"]["goal"].startswith("Route tier-2") and s["state"]["progress_claims"] == ["Implementation complete"]

    b = (await client.get("/v1/sessions/ses_1/brief?budget=2000")).json()
    assert b["text"].startswith("=== memvine · this session's working state ===")
    assert "Assistant's unverified progress claims:\n- Implementation complete" in b["text"]
    assert "=== memvine · recalled memory" in b["text"], "relevant long-term memory follows the state"
    assert b["tokens"] <= 2000

    closed = (await client.post("/v1/sessions/ses_1/close")).json()
    assert closed["sealed"] == [[1, 4]]
    s = (await client.get("/v1/sessions/ses_1")).json()
    assert s["status"] == "synthesized" and s["sealed_through"] == 4
    episodes = (await client.get("/v1/episodes", headers={"Authorization": "Bearer mv_mk_test", "X-Memvine-Project": client.project_slug})).json()
    assert any(e["id"] == "session:ses_1:00001-00004" for e in episodes)

    # New turns after close reopen the session; closing again seals only the new range.
    await client.post("/v1/sessions/ses_1/events", json={"events": [{"role": "user", "content": "One more thing: ship it Monday."}]})
    assert (await client.get("/v1/sessions/ses_1")).json()["status"] == "live"
    closed = (await client.post("/v1/sessions/ses_1/close")).json()
    assert closed["sealed"] == [[5, 5]], "the sealed range 1-4 is never re-cut or re-extracted"


async def mcp_call(client, method: str, params: dict | None = None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    r = await client.post("/mcp/", json=body, headers={**client.headers, "Accept": "application/json, text/event-stream"})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_mcp_tools_recall_and_remember(client):
    """The MCP endpoint lists the memory tools, refuses without a key, and recall/remember work
    against the same store as the REST routes."""
    from memvine_server.main import app

    await ingest_and_synthesize(client, app, [
        {"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"},
        {"id": "s2", "content": SESSION_2, "timestamp": "2026-10-03T14:02:00Z"},
    ])
    tools = await mcp_call(client, "tools/list")
    assert {t["name"] for t in tools["result"]["tools"]} == {"memory_recall", "memory_remember", "memory_session_brief", "memory_forget"}

    recalled = await mcp_call(client, "tools/call", {"name": "memory_recall", "arguments": {"query": "When is the launch?"}})
    text = recalled["result"]["content"][0]["text"]
    assert text.startswith("=== memvine") and "12 Nov" in text and "(was: Launch date is 29 Oct 2026" in text
    assert "how these facts connect" not in text
    recalled = await mcp_call(client, "tools/call", {"name": "memory_recall", "arguments": {
        "query": "When is the launch?", "include": {"related": True}}})
    with_edges = recalled["result"]["content"][0]["text"]
    assert with_edges.startswith(text) and "--- how these facts connect ---" in with_edges
    assert "    supersedes: Launch date is 29 Oct 2026" in with_edges

    app.state.llm.responses["NEW"] = json.dumps({"relation": "different", "reason": "new fact"})
    stored = await mcp_call(client, "tools/call", {"name": "memory_remember", "arguments": {
        "statement": "Deploys happen on Tuesdays only.", "kind": "procedural", "subjects": ["Deploys"],
    }})
    outcome = json.loads(stored["result"]["content"][0]["text"])
    assert outcome["relation"] in ("new", "different")
    memories = (await client.get("/v1/memories?q=Tuesdays")).json()
    assert len(memories) == 1 and memories[0]["subjects"] == ["deploys"] and memories[0]["evidence"][0]["episode_id"].startswith("mcp:")

    unauth = await client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                               headers={"Authorization": "Bearer mv_sk_nope", "Accept": "application/json, text/event-stream"})
    assert unauth.status_code == 401


@pytest.mark.asyncio
async def test_rate_limited_job_is_requeued_without_spending_an_attempt(client):
    from memvine_server.extraction.llm import LLMRateLimited
    from memvine_server.main import app
    from memvine_server.worker import RATE_LIMIT_PAUSE_SECONDS, run_one

    class Limited:
        async def complete(self, system, user, *, max_tokens=4096):
            raise LLMRateLimited("usage limit reached")

    await client.post("/v1/ingest", json={"items": [{"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}]})
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    assert await run_one(app.state.pool, Limited(), app.state.embedder, project_id)
    job = (await client.get("/v1/jobs")).json()["jobs"][0]
    assert job["status"] == "queued" and job["attempts"] == 0 and "usage limit" in job["error"]
    async with app.state.pool.connection() as conn:
        wait = (await (await conn.execute("SELECT extract(epoch FROM run_after - now()) AS s FROM jobs WHERE id = %s", (job["id"],))).fetchone())["s"]
    assert RATE_LIMIT_PAUSE_SECONDS - 10 < wait <= RATE_LIMIT_PAUSE_SECONDS


@pytest.mark.asyncio
async def test_audiences_scope_what_each_identity_recalls(client):
    """An episode sent to an audience is recalled only by identities in it (fixed list or trait
    rule), never merges with another audience's facts, and stays out of entity pages."""
    from memvine_server.main import app

    secret = ("## Developer · 2026-10-05T09:00\nAcme's renewal price is $48,000 a year, signed off by their CFO Dana.\n\n"
              "## Claude · 2026-10-05T09:01\nNoted the Acme renewal at $48,000.")
    r = await client.post("/v1/ingest", json={"items": [{"id": "acme", "content": secret, "timestamp": "2026-10-05T09:00:00Z", "audience": "acme"}]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "audience-unknown"

    assert (await client.put("/v1/audiences/project", json={"kind": "fixed"})).status_code == 400
    assert (await client.put("/v1/audiences/acme", json={"kind": "rule"})).status_code == 400, "a rule needs a condition"
    a = await client.put("/v1/audiences/acme", json={"kind": "rule", "conditions": [{"trait": "company", "op": "is", "value": "acme"}]})
    assert a.status_code == 200, a.text
    await client.put("/v1/audiences/sales", json={"kind": "fixed", "members": ["sam@example.com"]})
    alice = (await client.put("/v1/identities/alice@acme.com", json={"traits": {"company": "acme"}})).json()
    assert alice["audiences"] == ["project", "acme"]
    await client.put("/v1/identities/bob@globex.com", json={"traits": {"company": "globex"}})

    app.state.llm.responses = {
        "Target launch is 29 Oct": EXTRACT_1,
        "renewal price": json.dumps([{"statement": "Acme's renewal price is $48,000 a year, approved by their CFO Dana.",
                                      "kind": "semantic", "category": "fact", "subjects": ["acme", "launch"], "assertion": "stated",
                                      "quote": "Acme's renewal price is $48,000 a year"}]),
    }
    r = await client.post("/v1/ingest", json={"items": [
        {"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"},
        {"id": "acme", "content": secret, "timestamp": "2026-10-05T09:00:00Z", "audience": "acme", "identity": "alice@acme.com"},
    ]})
    assert r.status_code == 202
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, project_id)

    q = {"query": "What is Acme's renewal price?"}
    as_alice = (await client.post("/v1/recall", json={**q, "identity": "alice@acme.com"})).json()
    as_bob = (await client.post("/v1/recall", json={**q, "identity": "bob@globex.com"})).json()
    anonymous = (await client.post("/v1/recall", json=q)).json()
    assert any("$48,000" in i["memory"]["statement"] for i in as_alice["items"])
    assert all("$48,000" not in i["memory"]["statement"] for i in as_bob["items"]), "bob is not in acme"
    assert all("$48,000" not in i["memory"]["statement"] for i in anonymous["items"]), "no identity reads project memory only"
    mgmt = {"Authorization": "Bearer mv_mk_test", "X-Memvine-Project": client.project_slug}
    as_console = (await client.post("/v1/recall", json=q, headers=mgmt)).json()
    assert any("$48,000" in i["memory"]["statement"] for i in as_console["items"]), "the console reads every audience"

    # The acme fact shares the 'launch' subject but no project page may include it.
    page_prompts = [u for _, u in app.state.llm.calls if u.startswith("Subject: ")]
    assert all("48,000" not in u for u in page_prompts)

    listed = (await client.get("/v1/audiences")).json()
    assert {a["slug"]: a["memory_count"] for a in listed} == {"acme": 1, "sales": 0}
    assert (await client.delete("/v1/audiences/acme")).status_code == 409, "episodes still use it"
    assert (await client.delete("/v1/audiences/sales")).status_code == 204
    assert (await client.get("/v1/identities/alice@acme.com")).json()["audiences"] == ["project", "acme"]



@pytest.mark.asyncio
async def test_contradictions_stay_side_by_side(client):
    """Incompatible claims with no change over time keep both items active, linked, and recall
    shows them together, which is what a contradiction question needs."""
    from memvine_server.main import app

    s1 = "## Developer · 2026-09-01T10:00\nI have never written any Flask routes or handled HTTP requests in this project."
    s2 = "## Developer · 2026-09-20T10:00\nI implemented a basic homepage route with Flask today."
    app.state.llm.responses = {
        # First: the fake model answers with the first key found, and this prompt also quotes the statements.
        "NEW (2026-09-20)": json.dumps({"match": 1, "relation": "contradiction", "reason": "never vs did"}),
        "never written any Flask routes": json.dumps([{"statement": "The user has never written Flask routes or handled HTTP requests in this project.",
            "kind": "semantic", "category": "fact", "subjects": ["flask"], "assertion": "stated", "quote": "I have never written any Flask routes"}]),
        "basic homepage route": json.dumps([{"statement": "The user has written Flask routes and handled HTTP requests in this project, starting with a basic homepage route.",
            "kind": "episodic", "category": "fact", "subjects": ["flask"], "assertion": "stated", "quote": "I implemented a basic homepage route with Flask"}]),
    }
    await client.post("/v1/ingest", json={"items": [
        {"id": "a", "content": s1, "timestamp": "2026-09-01T10:00:00Z"},
        {"id": "b", "content": s2, "timestamp": "2026-09-20T10:00:00Z"},
    ]})
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, project_id)

    memories = (await client.get("/v1/memories")).json()
    assert len(memories) == 2 and all(m["status"] == "active" for m in memories)
    never, did = sorted(memories, key=lambda m: m["recorded_at"])
    assert never["conflicts_with"] == [did["id"]] and did["conflicts_with"] == [never["id"]]

    r = (await client.post("/v1/recall", json={"query": "Have I worked with Flask routes in this project?"})).json()
    first = r["items"][0]
    assert first["conflicts"], "the conflicting claim travels with the item"
    # Its line names the edge; the pair below quotes both sides once.
    assert any(", contradicts " in line for line in r["briefing"].splitlines() if line.startswith("[")), r["briefing"]
    # The pair also leads the briefing, before pages and the ranked facts.
    body = r["briefing"].split("\n", 1)[1]
    assert body.startswith("Conflicting statements on record") or "Standing instructions" in body.split("Conflicting statements on record")[0]
    assert " vs " in body.split("Conflicting statements on record", 1)[1].split("\n")[1]


@pytest.mark.asyncio
async def test_abandoned_running_job_is_requeued(client):
    from memvine_server.main import app
    from memvine_server.worker import claim_job, requeue_abandoned

    await client.post("/v1/ingest", json={"items": [{"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}]})
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
        job = await claim_job(conn, project_id)
        assert await claim_job(conn, project_id) is None, "the running job blocks its project"
        assert await requeue_abandoned(conn) == 0, "a fresh claim is within its lease"
        await conn.execute("UPDATE jobs SET claimed_at = now() - interval '20 minutes' WHERE id = %s", (job["id"],))
        assert await requeue_abandoned(conn) >= 1
        again = await claim_job(conn, project_id)
        assert again["id"] == job["id"]


@pytest.mark.asyncio
async def test_recall_fills_the_budget_with_source_passages(client):
    """Asked for them, recall adds the original passages behind the facts, in conversation order,
    never past as_of, within the budget."""
    from memvine_server.main import app

    await ingest_and_synthesize(client, app, [
        {"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"},
        {"id": "s2", "content": SESSION_2, "timestamp": "2026-10-03T14:02:00Z"},
    ])
    r = (await client.post("/v1/recall", json={"query": "When is the launch and why did it move?", "budget": 2000,
                                                     "include": {"passages": True}})).json()
    assert r["passages"], "source excerpts fill the remaining budget"
    assert "--- source excerpts (the original words) ---" in r["briefing"]
    assert any("Legal pushed back on the refund copy" in p["content"] for p in r["passages"])
    times = [p["occurred_at"] for p in r["passages"]]
    assert times == sorted(times), "excerpts come in conversation order"
    assert r["tokens"] <= 2000

    early = (await client.post("/v1/recall", json={"query": "launch date", "as_of": "2026-09-25T00:00:00Z",
                                                         "include": {"passages": True}})).json()
    assert all(p["episode_id"] == "s1" for p in early["passages"]), "nothing from after the moment asked"


@pytest.mark.asyncio
async def test_standing_instructions_reach_every_recall(client):
    """A lasting rule is recalled for any question, even one that does not resemble it."""
    from memvine_server.main import app

    app.state.llm.responses = {"syntax highlighting": json.dumps([
        {"statement": "Craig prefers that all code snippets be formatted with syntax highlighting.", "kind": "preference", "category": "preference",
         "subjects": ["code style"], "assertion": "stated", "standing": True, "quote": "Always format all code snippets with syntax highlighting"},
    ])}
    await client.post("/v1/ingest", json={"items": [
        {"id": "rule", "content": "## Craig\\nAlways format all code snippets with syntax highlighting.", "timestamp": "2026-09-01T09:00:00Z"},
    ]})
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, project_id)

    # Unrelated question: nothing matches, so recall abstains, but the rule still comes back in `standing`.
    r = (await client.post("/v1/recall", json={"query": "How do I implement a login feature?"})).json()
    assert r["standing"] == ["Craig prefers that all code snippets be formatted with syntax highlighting."]
    assert r["abstained"] and r["briefing"] == "" and r["tokens"] == 0 and r["items"] == []
    # A question the rule answers: served, in its own section ahead of the recalled memory.
    r = (await client.post("/v1/recall", json={"query": "How should code snippets be formatted?"})).json()
    assert not r["abstained"]
    lines = r["briefing"].splitlines()
    assert lines[0].startswith("=== memvine · standing instructions") and lines[1].startswith("- Craig prefers")
    assert lines[2].startswith("=== memvine · recalled memory")
    assert not any(i["memory"]["standing"] for i in r["items"]), "not repeated among the items"
    before = (await client.post("/v1/recall", json={"query": "login", "as_of": "2026-08-01T00:00:00Z"})).json()
    assert before["standing"] == [], "a rule given later does not apply earlier"


@pytest.mark.asyncio
async def test_project_management(client):
    """Create with tags, list with counts, rename and retag, delete only with the slug repeated."""
    mgmt = {"Authorization": "Bearer mv_mk_test"}
    created = await client.post("/v1/projects", json={"name": "My App (staging)", "tags": ["staging", "team-a", "staging"]}, headers=mgmt)
    assert created.status_code == 201, created.text
    p = created.json()
    assert p["slug"] == "my-app-staging" and p["tags"] == ["staging", "team-a"] and p["memory_count"] == 0
    assert (await client.post("/v1/projects", json={"name": "My App staging"}, headers=mgmt)).status_code == 409
    assert (await client.post("/v1/projects", json={"name": "x", "tags": ["Bad Tag"]}, headers=mgmt)).status_code == 422

    listed = {r["slug"]: r for r in (await client.get("/v1/projects", headers=mgmt)).json()}
    mine = listed[client.project_slug]
    assert mine["key_count"] == 1 and "source_count" in mine

    patched = (await client.patch("/v1/projects/my-app-staging", json={"name": "My App", "tags": ["prod"]}, headers=mgmt)).json()
    assert patched["name"] == "My App" and patched["tags"] == ["prod"] and patched["slug"] == "my-app-staging"

    refused = await client.delete("/v1/projects/my-app-staging", headers=mgmt)
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "confirm-required"
    assert (await client.delete("/v1/projects/my-app-staging?confirm=my-app-staging", headers=mgmt)).status_code == 204
    assert "my-app-staging" not in {r["slug"] for r in (await client.get("/v1/projects", headers=mgmt)).json()}
    assert (await client.delete("/v1/projects/nope?confirm=nope", headers=mgmt)).status_code == 404


@pytest.mark.asyncio
async def test_rolling_checkpoints_seal_live_sessions_with_boundary_context(client, monkeypatch):
    """A live session's completed stretches become episodes while it runs; extraction of a later
    range sees the end of the earlier one and the memories it resembles; the processed checkpoint
    moves only when memory writes commit; a retry does not duplicate; other sessions see what is
    not yet extracted."""
    from memvine_server.config import settings
    from memvine_server.main import app

    monkeypatch.setattr(settings, "session_soft_chars", 150)
    pad = " This matters for the background jobs we run every night."
    redis = "Let's use Redis for the job queue." + pad
    postgres = "Actually, Postgres is enough for the queue. We'll use that." + pad
    app.state.llm.responses = {
        "Previous state:": json.dumps({"goal": "Pick a job queue"}),
        "<excerpt>\n## user · 2026": "[]",  # replaced per range below
    }
    app.state.llm.responses = {
        "Previous state:": json.dumps({"goal": "Pick a job queue"}),
        "NEW (": json.dumps({"match": 1, "relation": "update", "reason": "switched"}),
        "Postgres is enough": json.dumps([{"statement": "The job queue uses Postgres, not Redis.", "kind": "semantic", "category": "fact",
            "subjects": ["job queue"], "assertion": "stated", "quote": "Postgres is enough for the queue"}]),
        "use Redis for the job queue": json.dumps([{"statement": "The job queue uses Redis.", "kind": "semantic", "category": "fact",
            "subjects": ["job queue"], "assertion": "stated", "quote": "use Redis for the job queue"}]),
    }

    def turn(role, content):
        return {"role": role, "content": content}

    r = await client.post("/v1/sessions/live1/events", json={"events": [turn("user", redis), turn("assistant", "Noted, Redis." + pad)]})
    assert r.json()["sealed_through"] == 0, "the second message completes nothing yet: no following user message"
    r = await client.post("/v1/sessions/live1/events", json={"events": [turn("user", postgres)]})
    s = r.json()
    assert s["status"] == "live" and s["sealed_through"] == 2, "exchange 1 sealed once the next message arrived"
    await client.post("/v1/sessions/live1/events", json={"events": [turn("assistant", "Switching to Postgres." + pad),
                                                                     turn("user", "Good, thanks.")]})
    assert (await client.get("/v1/sessions/live1")).json()["sealed_through"] == 4

    eps = {e["id"] for e in (await client.get("/v1/episodes", headers={"Authorization": "Bearer mv_mk_test", "X-Memvine-Project": client.project_slug})).json()}
    assert {"session:live1:00001-00002", "session:live1:00003-00004"} <= eps

    # Before extraction, another session sees the unprocessed messages; the session itself does not.
    other = (await client.post("/v1/recall", json={"query": "which queue backend do we use?", "session_id": "other"})).json()
    assert any("Postgres is enough" in m["content"] for m in other["pending"])
    assert "not yet in memory" in other["briefing"]
    own = (await client.post("/v1/recall", json={"query": "which queue backend do we use?", "session_id": "live1"})).json()
    assert own["pending"] == []

    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, project_id)
    s = (await client.get("/v1/sessions/live1")).json()
    assert s["processed_through"] == 4, "checkpoint advanced with the committed extractions"
    after = (await client.post("/v1/recall", json={"query": "which queue backend do we use?", "session_id": "other"})).json()
    assert [m["seq"] for m in after["pending"]] == [5], "extracted messages leave pending; only the open tail stays"

    # The second range was extracted with the end of the first as context, and the Redis memory.
    from memvine_server.extraction.prompts import KNOWLEDGE_SYSTEM

    second = next(u for system, u in app.state.llm.calls
                  if system == KNOWLEDGE_SYSTEM and "Postgres is enough" in u.split("<excerpt>")[1])
    context = second.split("<excerpt>")[0]
    assert "Earlier in this conversation" in context and "Redis" in context
    assert "What memory already knows" in context and "The job queue uses Redis." in context

    memories = (await client.get("/v1/memories")).json()
    assert {m["statement"]: m["status"] for m in memories} == {
        "The job queue uses Redis.": "superseded", "The job queue uses Postgres, not Redis.": "active"}

    # Retrying the second episode replaces its own records: no duplicates.
    async with app.state.pool.connection() as conn:
        await conn.execute("UPDATE jobs SET status = 'queued', run_after = now() WHERE project_id = %s AND episode_id = %s",
                           (project_id, "session:live1:00003-00004"))
    await drain(app.state.pool, app.state.llm, app.state.embedder, project_id)
    again = (await client.get("/v1/memories")).json()
    assert len(again) == 2 and sum(m["status"] == "active" for m in again) == 1


@pytest.mark.asyncio
async def test_a_cancelled_job_returns_to_the_queue(client):
    """Shutdown cancels a long-running job: nothing it wrote survives and the job is queued again."""
    import asyncio

    from memvine_server.main import app
    from memvine_server.worker import run_one

    class Slow:
        async def complete(self, system, user, *, max_tokens=4096):
            await asyncio.sleep(30)
            return "[]"

    await client.post("/v1/ingest", json={"items": [{"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}]})
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    task = asyncio.create_task(run_one(app.state.pool, Slow(), app.state.embedder, project_id))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    job = (await client.get("/v1/jobs")).json()["jobs"][0]
    assert job["status"] == "queued" and job["attempts"] == 0
    episodes = (await client.get("/v1/episodes", headers={"Authorization": "Bearer mv_mk_test", "X-Memvine-Project": client.project_slug})).json()
    assert episodes[0]["memory_count"] == 0


@pytest.mark.asyncio
async def test_credits_caps_and_retention(client):
    """Ingest costs a credit per 1,000 characters (re-sends free), recall 0.1; a reached monthly cap
    refuses ingestion but never recall; retention drops old raw text and keeps the memories."""
    from memvine_server.main import app

    mgmt = {"Authorization": "Bearer mv_mk_test"}
    slug = client.project_slug
    project = lambda: next(p for p in client_projects if p["slug"] == slug)  # noqa: E731
    client_projects = (await client.get("/v1/projects", headers=mgmt)).json()
    assert float(project()["month_credits"]) == 0 and project()["monthly_credit_cap"] is None

    await ingest_and_synthesize(client, app, [{"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}])
    await client.post("/v1/ingest", json={"items": [{"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}]})
    await client.post("/v1/recall", json={"query": "launch date"})
    await client.post("/v1/recall", json={"query": "launch date"}, headers={**mgmt, "X-Memvine-Project": slug})  # console: free
    client_projects = (await client.get("/v1/projects", headers=mgmt)).json()
    assert float(project()["month_credits"]) == 1.1, "1 credit for ~200 chars once, 0.1 for the one key recall"
    usage = (await client.get("/v1/usage?days=1")).json()[-1]
    assert usage["ingest"] == 1 and usage["recall"] == 1 and float(usage["ingest_credits"]) == 1.0

    capped = (await client.patch(f"/v1/projects/{slug}", json={"monthly_credit_cap": 1}, headers=mgmt)).json()
    assert float(capped["monthly_credit_cap"]) == 1.0
    refused = await client.post("/v1/ingest", json={"items": [{"id": "s2", "content": SESSION_2, "timestamp": "2026-10-03T14:02:00Z"}]})
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "project-cap-reached"
    live = await client.post("/v1/sessions/x/events", json={"events": [{"role": "user", "content": "hello there"}]})
    assert live.status_code == 429
    assert (await client.post("/v1/recall", json={"query": "launch date"})).status_code == 200, "recall is never refused"
    uncapped = (await client.patch(f"/v1/projects/{slug}", json={"monthly_credit_cap": None}, headers=mgmt)).json()
    assert uncapped["monthly_credit_cap"] is None
    assert (await client.post("/v1/ingest", json={"items": [{"id": "s2", "content": SESSION_2, "timestamp": "2026-10-03T14:02:00Z"}]})).status_code == 202

    # Retention: s1 (Sep) is older than 30 days, s2 is queued, so only s1's raw text goes.
    await client.patch(f"/v1/projects/{slug}", json={"retention_days": 30}, headers=mgmt)
    from memvine_server.billing import apply_retention
    async with app.state.pool.connection() as conn:
        assert await apply_retention(conn) >= 1
    s1 = (await client.get("/v1/episodes/s1")).json()
    assert s1["content"] == "[expired by retention]"
    memories = (await client.get("/v1/memories")).json()
    assert any(m["statement"].startswith("Launch date is 29 Oct") and m["evidence"] for m in memories), "memories and quotes stay"


@pytest.mark.asyncio
async def test_organizations_isolate_their_projects(client):
    """An org key administers only its own org's projects; another org's projects read as missing;
    only the operator key creates organizations; the last org key cannot be revoked."""
    op = {"Authorization": "Bearer mv_mk_test"}
    suffix = uuid.uuid4().hex[:6]
    a = (await client.post("/v1/orgs", json={"name": f"Acme {suffix}"}, headers=op)).json()
    b = (await client.post("/v1/orgs", json={"name": f"Globex {suffix}"}, headers=op)).json()
    ka, kb = {"Authorization": f"Bearer {a['key']}"}, {"Authorization": f"Bearer {b['key']}"}
    assert (await client.post("/v1/orgs", json={"name": "Nope"}, headers=ka)).status_code == 403
    assert (await client.get("/v1/org", headers=ka)).json()["org"]["slug"] == a["slug"]

    pa = (await client.post("/v1/projects", json={"name": f"acme app {suffix}"}, headers=ka)).json()
    assert [p["slug"] for p in (await client.get("/v1/projects", headers=ka)).json()] == [pa["slug"]]
    assert (await client.get("/v1/projects", headers=kb)).json() == []
    assert pa["slug"] in {p["slug"] for p in (await client.get("/v1/projects", headers=op)).json()}

    # Globex cannot read, change, key, or delete Acme's project, nor read its memory as the console does.
    assert (await client.patch(f"/v1/projects/{pa['slug']}", json={"name": "x"}, headers=kb)).status_code == 404
    assert (await client.post(f"/v1/projects/{pa['slug']}/keys", json={"name": "x"}, headers=kb)).status_code == 404
    assert (await client.delete(f"/v1/projects/{pa['slug']}?confirm={pa['slug']}", headers=kb)).status_code == 404
    assert (await client.get("/v1/memories", headers={**kb, "X-Memvine-Project": pa["slug"]})).status_code == 404
    assert (await client.get("/v1/memories", headers={**ka, "X-Memvine-Project": pa["slug"]})).status_code == 200

    # Org keys: add one, revoke the old, never the last.
    second = (await client.post("/v1/org/keys", json={"name": "ci"}, headers=ka)).json()
    keys = (await client.get("/v1/org/keys", headers=ka)).json()
    first_id = next(k["id"] for k in keys if k["name"] == "default")
    assert (await client.delete(f"/v1/org/keys/{first_id}", headers=ka)).status_code == 204
    assert (await client.get("/v1/org", headers=ka)).status_code == 403, "the revoked key stops working"
    k2 = {"Authorization": f"Bearer {second['key']}"}
    assert (await client.delete(f"/v1/org/keys/{second['id']}", headers=k2)).status_code == 409

    await client.delete(f"/v1/projects/{pa['slug']}?confirm={pa['slug']}", headers=k2)

    # The audit log records each change for its own organization only; failed attempts are not changes.
    trail = (await client.get("/v1/audit", headers=k2)).json()
    assert [e["action"] for e in trail] == ["project.delete", "org_key.revoke", "org_key.create", "project.create"]
    assert trail[-1]["target"] == pa["slug"] and trail[-1]["actor"] == "org key default" and trail[0]["actor"] == "org key ci"
    assert (await client.get("/v1/audit", headers=kb)).json() == []
    assert any(e["action"] == "org.create" and e["target"] == a["slug"] for e in (await client.get("/v1/audit", headers=op)).json())


@pytest.mark.asyncio
async def test_broad_questions_get_diverse_facts_in_time_order(client):
    """A summary question selects across topics (not only the closest cluster) and lists facts
    chronologically; diversity also runs on ordinary questions without breaking them."""
    from memvine_server.main import app

    facts = [
        ("2026-09-01", "Auth: passwords are hashed with werkzeug generate_password_hash.", "auth"),
        ("2026-09-02", "Auth: passwords are hashed with werkzeug and salted per user.", "auth"),
        ("2026-09-03", "Auth: password hashing uses werkzeug with pbkdf2.", "auth"),
        ("2026-09-10", "Database: a UNIQUE constraint error on transactions.id was fixed with uuid4 ids.", "database"),
        ("2026-09-20", "Forms: CSRF token errors were fixed by enabling Flask-WTF CSRFProtect.", "forms"),
    ]
    items = []
    for i, (day, statement, subject) in enumerate(facts):
        content = f"## Developer · {day}\n{statement}"
        # Keyed on the episode's own header: extraction prompts also carry related memories as context.
        app.state.llm.responses[f"Developer · {day}"] = json.dumps([{"statement": statement, "kind": "episodic", "category": "fact", "subjects": [subject],
                                                                 "assertion": "stated", "quote": statement[:40]}])
        items.append({"id": f"f{i}", "content": content, "timestamp": f"{day}T10:00:00Z"})
    app.state.llm.responses["NEW ("] = json.dumps({"match": None, "relation": "different", "reason": "distinct"})
    await client.post("/v1/ingest", json={"items": items})
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, project_id)

    r = (await client.post("/v1/recall", json={"query": "Give me a comprehensive summary of the password, database and forms work", "budget": 4000})).json()
    statements = [i["memory"]["statement"] for i in r["items"]]
    assert len(statements) == 5 and any("UNIQUE constraint" in s for s in statements) and any("CSRF" in s for s in statements)
    days = [i["memory"]["valid_from"] for i in r["items"]]
    assert days == sorted(days), "a broad answer is told in time order"
    plain = (await client.post("/v1/recall", json={"query": "How are passwords hashed?"})).json()
    assert plain["items"] and "werkzeug" in plain["items"][0]["memory"]["statement"]


@pytest.mark.asyncio
async def test_create_memory_directly(client):
    """POST /v1/memories stores a fact with its own source, flags standing rules, and reconciles."""
    from memvine_server.main import app

    app.state.llm.responses = {"NEW (": json.dumps({"match": 1, "relation": "update", "reason": "moved"})}
    first = await client.post("/v1/memories", json={"statement": "Deploys happen on Tuesdays.", "kind": "procedural", "subjects": ["deploys"]})
    assert first.status_code == 201, first.text
    m1 = first.json()
    assert m1["relation"] == "new" and m1["memory"]["evidence"][0]["episode_id"].startswith("api:")
    second = (await client.post("/v1/memories", json={"statement": "Deploys happen on Thursdays now.", "kind": "procedural", "subjects": ["deploys"]})).json()
    assert second["relation"] == "update" and second["related"] == m1["memory"]["id"]
    rule = (await client.post("/v1/memories", json={"statement": "Always run the linter before committing.", "kind": "preference"})).json()
    assert rule["memory"]["standing"] is True
    assert (await client.post("/v1/memories", json={"statement": "x y z", "audience": "nope"})).status_code == 400


@pytest.mark.asyncio
async def test_paused_project_keeps_jobs_queued(client):
    """A paused project stores what is sent but runs no synthesis until it is resumed."""
    from memvine_server.main import app
    from memvine_server.worker import drain

    wire_fake_llm(app)
    mgmt = {"Authorization": "Bearer mv_mk_test"}
    assert (await client.patch(f"/v1/projects/{client.project_slug}", json={"processing_paused": True}, headers=mgmt)).json()["processing_paused"]
    await client.post("/v1/ingest", json={"items": [{"id": "p1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}]})
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    assert await drain(app.state.pool, app.state.llm, app.state.embedder, project_id) == 0
    assert (await client.get("/v1/jobs")).json()["jobs"][0]["status"] == "queued"
    await client.patch(f"/v1/projects/{client.project_slug}", json={"processing_paused": False}, headers=mgmt)
    assert await drain(app.state.pool, app.state.llm, app.state.embedder, project_id) >= 1


@pytest.mark.asyncio
async def test_imported_session_keeps_its_times_and_skips_live_state(client):
    """A past session sent with live=false (backfill) keeps its own times, queues no working-state
    refresh, and becomes memory when closed."""
    turns = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}: we chose Postgres for the queue", "at": f"2026-03-0{1 + i // 4}T10:0{i % 4}:00Z"}
             for i in range(8)]
    r = await client.post("/v1/sessions/old_1/events", json={"events": turns, "agent": "claude-code", "live": False})
    assert r.status_code == 202
    from memvine_server.main import app
    async with app.state.pool.connection() as conn:
        kinds = await (await conn.execute(
            "SELECT j.kind FROM jobs j JOIN projects p ON p.id = j.project_id WHERE p.slug = %s", (client.project_slug,))).fetchall()
    assert not any(k["kind"] == "session_state" for k in kinds)
    session = next(s for s in (await client.get("/v1/sessions")).json() if s["id"] == "old_1")
    assert session["started_at"].startswith("2026-03-01")
    assert (await client.post("/v1/sessions/old_1/close")).status_code == 200
    episodes = (await client.get("/v1/episodes", headers={"Authorization": "Bearer mv_mk_test", "X-Memvine-Project": client.project_slug})).json()
    assert any(e["id"].startswith("session:old_1:") for e in episodes)


@pytest.mark.asyncio
async def test_row_level_security_confines_a_project_connection(client):
    """Beneath the queries' own filters: a connection confined to one project sees none of
    another's rows, even with a query that forgets to filter, and cannot write into it."""
    import psycopg

    from memvine_server.db import request_project
    from memvine_server.main import app

    mgmt = {"Authorization": "Bearer mv_mk_test"}
    other = (await client.post("/v1/projects", json={"name": f"rls other {uuid.uuid4().hex[:6]}"}, headers=mgmt)).json()
    await client.post("/v1/ingest", json={"items": [{"id": "rls-mine", "content": "mine", "timestamp": "2026-10-01T10:00:00Z"}]})
    await client.post("/v1/ingest", json={"items": [{"id": "rls-theirs", "content": "theirs", "timestamp": "2026-10-01T10:00:00Z"}]},
                      headers={**mgmt, "X-Memvine-Project": other["slug"]})
    pool = app.state.pool
    async with pool.connection() as conn:
        assert (await (await conn.execute("SELECT current_user AS u")).fetchone())["u"] == "memvine_app"
        mine = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
        theirs = other["id"]
        assert (await (await conn.execute("SELECT count(*) AS n FROM projects")).fetchone())["n"] >= 2, "unconfined: sees all"

    token = request_project.set(str(mine))
    try:
        async with pool.connection() as conn:
            assert [r["id"] for r in await (await conn.execute("SELECT id FROM projects")).fetchall()] == [mine]
            ids = {r["id"] for r in await (await conn.execute("SELECT id FROM episodes")).fetchall()}
            assert "rls-mine" in ids and "rls-theirs" not in ids
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute("INSERT INTO identities (project_id, identity) VALUES (%s, 'intruder')", (theirs,))
    finally:
        request_project.reset(token)
    await client.delete(f"/v1/projects/{other['slug']}?confirm={other['slug']}", headers=mgmt)


@pytest.mark.asyncio
async def test_memories_about_code_go_stale_when_the_code_changes(client):
    """A fact naming a file the conversation touched is anchored to that file's version; a newer
    version marks it stale (and recall says so), the old one again makes it fresh, and a person
    confirming it re-anchors it to the current version. History never goes stale this way."""
    from memvine_server.main import app
    from memvine_server.worker import drain

    repo = "github.com/acme/app"
    content = "## Developer · 2026-10-05T09:00\nThe queue worker in src/queue/worker.py retries failed jobs 3 times with a 30 second backoff."
    app.state.llm.responses = {
        "retries failed jobs 3 times": json.dumps([
            {"statement": "worker.py retries failed jobs 3 times with a 30 second backoff.", "kind": "semantic", "category": "fact", "subjects": ["queue"],
             "assertion": "stated", "confidence": "high", "quote": "retries failed jobs 3 times with a 30 second backoff"},
        ]),
    }
    app.state.llm.default = "[]"
    r = await client.post("/v1/ingest", json={"items": [{"id": "code-1", "content": content, "timestamp": "2026-10-05T09:00:00Z",
                                                         "code": {"repo": repo, "files": {"src/queue/worker.py": "blob-a", "README.md": "blob-r"}}}]})
    assert r.status_code == 202, r.text
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, project_id)

    assert (await client.get("/v1/code/anchors", params={"repo": repo})).json()["paths"] == ["src/queue/worker.py"]
    memories = (await client.get("/v1/memories")).json()
    [fact] = memories

    async def check(blob):
        return (await client.post("/v1/code/check", json={"repo": repo, "files": {"src/queue/worker.py": blob}})).json()

    assert (await check("blob-a"))["stale"] == 0
    assert (await check("blob-b"))["stale"] == 1
    stale = (await client.get(f"/v1/memories/{fact['id']}")).json()
    assert stale["status"] == "stale" and stale["stale_reason"].startswith("src/queue/worker.py changed since")
    briefing = (await client.post("/v1/recall", json={"query": "how many times does the queue worker retry failed jobs"})).json()["briefing"]
    assert "anchored src/queue/worker.py, MAY BE OUTDATED]" in briefing
    assert "(src/queue/worker.py changed since" in briefing

    assert (await check("blob-a"))["fresh"] == 1, "the edit was undone"
    assert (await client.get(f"/v1/memories/{fact['id']}")).json()["status"] == "active"

    assert (await check(None))["stale"] == 1
    assert "was deleted" in (await client.get(f"/v1/memories/{fact['id']}")).json()["stale_reason"]
    await check("blob-c")
    mgmt = {"Authorization": "Bearer mv_mk_test", "X-Memvine-Project": client.project_slug}
    confirmed = (await client.patch(f"/v1/memories/{fact['id']}", json={"confirm": True}, headers=mgmt)).json()
    assert confirmed["status"] == "active" and confirmed["stale_reason"] is None
    assert (await check("blob-c"))["stale"] == 0, "confirmed against the current version"


@pytest.mark.asyncio
async def test_session_code_context_anchors_facts(client):
    """The plugin sends the files a session touched with its turns; facts from that session anchor to them."""
    from memvine_server.main import app
    from memvine_server.worker import drain

    repo = "github.com/acme/app"
    app.state.llm.responses = {
        "pool size to 16": json.dumps([
            {"statement": "db.py sets the connection pool size to 16.", "kind": "semantic", "category": "fact", "subjects": ["database"],
             "assertion": "stated", "confidence": "high", "quote": "set the pool size to 16"},
        ]),
    }
    app.state.llm.default = "[]"
    events = [{"role": "user", "content": "In server/db.py we set the pool size to 16 for the worker loops.", "at": "2026-10-06T10:00:00Z"},
              {"role": "assistant", "content": "Noted, 16 connections.", "at": "2026-10-06T10:00:05Z"}]
    r = await client.post("/v1/sessions/code-s/events", json={"events": events, "code": {"repo": repo, "files": {"server/db.py": "blob-1"}}})
    assert r.status_code == 202, r.text
    await client.post("/v1/sessions/code-s/close")
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, project_id)
    assert "server/db.py" in (await client.get("/v1/code/anchors", params={"repo": repo})).json()["paths"]
    assert (await client.post("/v1/code/check", json={"repo": repo, "files": {"server/db.py": "blob-2"}})).json()["stale"] == 1


@pytest.mark.asyncio
async def test_export_and_key_last_used_address(client):
    """An admin exports the whole project as JSON (audited); members may not; project keys record
    where they were last used from."""
    mgmt = {"Authorization": "Bearer mv_mk_test", "X-Memvine-Project": client.project_slug}
    await client.post("/v1/ingest", json={"items": [{"id": "exp-1", "content": "Export me.", "timestamp": "2026-10-01T10:00:00Z"}]},
                      headers={"X-Forwarded-For": "203.0.113.7"})
    await client.post("/v1/sessions/exp-s/events", json={"events": [{"role": "user", "content": "hello there"}]})
    r = await client.post("/v1/export", headers=mgmt)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    doc = r.json()
    assert doc["format"] == "memvine-export/1" and doc["project"]["slug"] == client.project_slug
    assert any(e["id"] == "exp-1" for e in doc["episodes"])
    assert next(s for s in doc["sessions"] if s["id"] == "exp-s")["events"][0]["content"] == "hello there"
    audit = (await client.get("/v1/audit", headers={"Authorization": "Bearer mv_mk_test"})).json()
    assert any(e["action"] == "project.export" and e["target"] == client.project_slug for e in audit)

    owner = (await client.post("/v1/auth/signup", json={"email": f"exp-{uuid.uuid4().hex[:6]}@example.com", "password": "x" * 12})).json()
    o = {"Authorization": f"Bearer {owner['token']}"}
    slug = (await client.post("/v1/projects", json={"name": f"exp {uuid.uuid4().hex[:4]}"}, headers=o)).json()["slug"]
    invite = (await client.post("/v1/org/invites", json={}, headers=o)).json()
    member = (await client.post("/v1/auth/signup", json={"email": f"expm-{uuid.uuid4().hex[:6]}@example.com", "password": "x" * 12, "invite": invite["token"]})).json()
    assert (await client.post("/v1/export", headers={"Authorization": f"Bearer {member['token']}", "X-Memvine-Project": slug})).status_code == 403

    await client.get("/v1/memories", headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
    keys = (await client.get(f"/v1/projects/{client.project_slug}/keys", headers={"Authorization": "Bearer mv_mk_test"})).json()
    assert any(k["last_used_ip"] == "203.0.113.9" for k in keys)


@pytest.mark.asyncio
async def test_live_work_goes_before_imported_history(client):
    """A backfilled session's jobs are background work: a live session's episode queued after them
    is synthesized first."""
    from memvine_server.main import app
    from memvine_server.worker import claim_job

    old = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"old turn {i} about the billing service", "at": f"2026-09-0{1 + i // 2}T10:00:00Z"}
           for i in range(6)]
    await client.post("/v1/sessions/hist-1/events", json={"events": old, "live": False})
    await client.post("/v1/sessions/hist-1/close")
    await client.post("/v1/ingest", json={"items": [{"id": "bulk-1", "content": "bulk history", "timestamp": "2026-09-01T10:00:00Z", "background": True}]})
    await client.post("/v1/sessions/now-1/events", json={"events": [{"role": "user", "content": "we are moving the queue to Postgres today"},
                                                                    {"role": "assistant", "content": "ok"}]})
    await client.post("/v1/sessions/now-1/close")
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
        queued = await (await conn.execute("SELECT episode_id, background FROM jobs WHERE project_id = %s AND kind = 'extract' ORDER BY id",
                                           (project_id,))).fetchall()
        assert [q["background"] for q in queued if q["episode_id"].startswith("session:hist-1")] and all(
            q["background"] for q in queued if q["episode_id"].startswith("session:hist-1") or q["episode_id"] == "bulk-1")
        first = await claim_job(conn, project_id)
    assert first["episode_id"].startswith("session:now-1"), first


@pytest.mark.asyncio
async def test_delete_session_removes_what_it_produced(client):
    """Deleting a session removes its messages, sealed sources and the memories only they support."""
    from memvine_server.main import app
    from memvine_server.worker import drain

    app.state.llm.responses = {
        "we deploy the lead service with helm": json.dumps([
            {"statement": "The lead service is deployed with helm.", "kind": "procedural", "category": "rule", "subjects": ["lead"],
             "assertion": "stated", "confidence": "high", "quote": "we deploy the lead service with helm"}]),
    }
    app.state.llm.default = "[]"
    await client.post("/v1/sessions/wrong-folder/events", json={"events": [
        {"role": "user", "content": "we deploy the lead service with helm"}, {"role": "assistant", "content": "noted"}]})
    await client.post("/v1/sessions/wrong-folder/close")
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, project_id)
    assert any("helm" in m["statement"] for m in (await client.get("/v1/memories")).json())

    mgmt = {"Authorization": "Bearer mv_mk_test", "X-Memvine-Project": client.project_slug}
    r = await client.delete("/v1/sessions/wrong-folder", headers=mgmt)
    assert r.status_code == 200 and r.json()["messages"] == 2 and r.json()["sources"] >= 1 and r.json()["memories"] >= 1
    assert not any("helm" in m["statement"] for m in (await client.get("/v1/memories")).json())
    assert (await client.get("/v1/sessions/wrong-folder")).status_code == 404
    assert (await client.delete("/v1/sessions/wrong-folder", headers=mgmt)).status_code == 404


@pytest.mark.asyncio
async def test_a_changed_value_supersedes_across_differently_worded_subjects(client):
    """The extractor tags the same fact with drifting subjects; a near-identical statement is still
    reconciled against it, so the new value supersedes the old instead of standing beside it."""
    from memvine_server.main import app

    app.state.llm.responses = {"NEW (": json.dumps({"match": 1, "relation": "update", "reason": "moved"})}
    old = (await client.post("/v1/memories", json={"statement": "The muse production database runs on port 5544.",
                                                    "subjects": ["muse", "production database"]})).json()
    new = (await client.post("/v1/memories", json={"statement": "The muse production database runs on port 6432.",
                                                    "subjects": ["muse prod db"]})).json()
    assert new["relation"] == "update" and new["related"] == old["memory"]["id"]
    unrelated = (await client.post("/v1/memories", json={"statement": "Release notes are written in Markdown files.",
                                                          "subjects": ["release notes"]})).json()
    assert unrelated["relation"] == "new", "an unrelated statement with no shared subject is not a candidate"
