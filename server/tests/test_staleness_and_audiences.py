"""Two promises, end to end through the HTTP API:

1. A memory about code whose file changes is flagged stale and re-checked, never served as current.
2. A memory sent to a restricted audience is recalled only by identities in that audience; a
   project-wide memory is recalled by every identity.
"""
from __future__ import annotations

import json

import pytest
from conftest import needs_db

from memvine_server.worker import drain

pytestmark = needs_db

REPO = "github.com/acme/app"
PATH = "src/billing/retry.py"
FACT = "retry.py retries failed card charges 4 times with exponential backoff."
MGMT = {"Authorization": "Bearer mv_mk_test"}


async def project_id_of(client, app):
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone()
    return row["id"]


async def synthesize(client, app):
    await drain(app.state.pool, app.state.llm, app.state.embedder, await project_id_of(client, app))
    failed = [j for j in (await client.get("/v1/jobs")).json()["jobs"] if j["status"] == "failed"]
    assert not failed, failed


async def check(client, blob):
    r = await client.post("/v1/code/check", json={"repo": REPO, "files": {PATH: blob}})
    assert r.status_code == 200, r.text
    return r.json()


def lines_naming(briefing: str, text: str) -> list[str]:
    """Briefing lines that state memory naming the text. Source excerpts (the original words,
    dated, under their own header) are quotes, not memory, and are left out."""
    memory = briefing.split("--- source excerpts")[0]
    return [line for line in memory.splitlines() if text in line]


# ---------------------------------------------------------------- staleness


async def remember_fact_from_a_session(client, app, session_id: str, blob: str, *, at: str) -> None:
    """What the plugin does: send a session's turns with the files it touched and their blobs,
    close it, and let synthesis anchor the fact to that file version."""
    app.state.llm.responses = {
        "retries failed card charges": json.dumps([
            {"statement": FACT, "kind": "semantic", "category": "fact", "subjects": ["billing"], "assertion": "stated", "confidence": "high",
             "quote": "retries failed card charges 4 times"},
        ]),
    }
    app.state.llm.default = "[]"
    events = [
        {"role": "user", "content": f"In {PATH} the worker retries failed card charges 4 times with exponential backoff.", "at": at},
        {"role": "assistant", "content": "Understood: four retries, exponential backoff.", "at": at},
    ]
    r = await client.post(f"/v1/sessions/{session_id}/events",
                          json={"events": events, "code": {"repo": REPO, "files": {PATH: blob}}, "live": False})
    assert r.status_code == 202, r.text
    assert (await client.post(f"/v1/sessions/{session_id}/close")).status_code == 200
    await synthesize(client, app)


@pytest.mark.asyncio
async def test_a_memory_whose_code_changed_is_flagged_stale_and_rechecked(client):
    """Code change reported -> memory stale with a reason -> recall marks it (item status, briefing
    line, lower score) everywhere it appears -> the file stays on the re-check list, and the
    next report of the original version makes it current again."""
    from memvine_server.main import app

    await remember_fact_from_a_session(client, app, "stale-1", "blob-v1", at="2026-10-05T09:00:00Z")
    memories = (await client.get("/v1/memories")).json()
    assert [m["statement"] for m in memories] == [FACT]
    fact = memories[0]
    assert fact["status"] == "active"
    assert (await client.get("/v1/code/anchors", params={"repo": REPO})).json()["paths"] == [PATH]

    q = {"query": "How many times are failed card charges retried?"}
    before = (await client.post("/v1/recall", json=q)).json()
    [current] = [i for i in before["items"] if i["memory"]["id"] == fact["id"]]
    assert current["memory"]["status"] == "active"
    assert all("MAY BE OUTDATED" not in line for line in lines_naming(before["briefing"], "retries failed card charges"))

    # The same version again changes nothing; a new one flags the memory.
    assert await check(client, "blob-v1") == {"checked": 1, "stale": 0, "fresh": 0}
    assert await check(client, "blob-v2") == {"checked": 1, "stale": 1, "fresh": 0}
    stale = (await client.get(f"/v1/memories/{fact['id']}")).json()
    assert stale["status"] == "stale"
    assert stale["stale_reason"].startswith(f"{PATH} changed since ")
    assert await check(client, "blob-v2") == {"checked": 1, "stale": 0, "fresh": 0}, "already stale: not flagged twice"

    # Recall still serves it (it may still be true) but never as current.
    after = (await client.post("/v1/recall", json=q)).json()
    [flagged] = [i for i in after["items"] if i["memory"]["id"] == fact["id"]]
    assert flagged["memory"]["status"] == "stale" and flagged["memory"]["stale_reason"] == stale["stale_reason"]
    fact_lines = lines_naming(after["briefing"], "retries failed card charges")
    assert fact_lines, after["briefing"]
    assert all(f"anchored {PATH}, MAY BE OUTDATED]" in line and f"({PATH} changed since" in line and "check the code" in line for line in fact_lines), fact_lines
    assert flagged["score"] == pytest.approx(current["score"] * 0.7, rel=1e-3), "ranked below what it scored while current"

    # Every other read path carries the flag too: MCP recall and a session brief.
    mcp = await client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": "memory_recall", "arguments": q}},
                            headers={**client.headers, "Accept": "application/json, text/event-stream"})
    mcp_text = mcp.json()["result"]["content"][0]["text"]
    assert all("MAY BE OUTDATED" in line for line in lines_naming(mcp_text, "retries failed card charges")), mcp_text

    # The re-check: the anchored file stays on the list a client hashes at each session start, and
    # reporting the original version again (the edit was reverted) makes the memory current.
    assert (await client.get("/v1/code/anchors", params={"repo": REPO})).json()["paths"] == [PATH], \
        "a stale memory's file is still re-checked"
    assert await check(client, "blob-v1") == {"checked": 1, "stale": 0, "fresh": 1}
    assert (await client.get(f"/v1/memories/{fact['id']}")).json()["status"] == "active"

    # A deleted file is a change as well.
    assert (await check(client, None))["stale"] == 1
    assert (await client.get(f"/v1/memories/{fact['id']}")).json()["stale_reason"].startswith(f"{PATH} was deleted since ")


@pytest.mark.asyncio
async def test_restating_a_stale_fact_against_the_new_code_revalidates_it(client):
    """A later session that touches the changed file and says the same thing again confirms the
    memory against the new version: one memory, current again, anchored to the new blob."""
    from memvine_server.main import app

    await remember_fact_from_a_session(client, app, "stale-a", "blob-v1", at="2026-10-05T09:00:00Z")
    [fact] = (await client.get("/v1/memories")).json()
    assert (await check(client, "blob-v2"))["stale"] == 1

    await remember_fact_from_a_session(client, app, "stale-b", "blob-v2", at="2026-10-07T09:00:00Z")
    memories = (await client.get("/v1/memories")).json()
    assert [m["id"] for m in memories] == [fact["id"]], "the restatement merged into the stale memory, no duplicate"
    revalidated = (await client.get(f"/v1/memories/{fact['id']}")).json()
    assert revalidated["status"] == "active" and revalidated["stale_reason"] is None
    assert len(revalidated["evidence"]) == 2
    assert (await check(client, "blob-v2"))["stale"] == 0, "anchored to the version current at the restatement"
    assert (await check(client, "blob-v3"))["stale"] == 1


@pytest.mark.asyncio
async def test_a_changed_value_after_a_code_change_supersedes_the_stale_fact(client):
    """When the new conversation states a different value, the stale memory is superseded by it,
    so recall shows the new value as current and the old one only as history."""
    from memvine_server.main import app

    await remember_fact_from_a_session(client, app, "old", "blob-v1", at="2026-10-05T09:00:00Z")
    [fact] = (await client.get("/v1/memories")).json()
    assert (await check(client, "blob-v2"))["stale"] == 1

    new_fact = "retry.py retries failed card charges 6 times with exponential backoff."
    app.state.llm.responses = {  # first matching key wins: the reconcile prompt also contains the extraction key
        f"NEW (2026-10-07): {new_fact}": json.dumps({"match": 1, "relation": "update", "reason": "retry count raised"}),
        "charges 6 times": json.dumps([{"statement": new_fact, "kind": "semantic", "category": "fact", "subjects": ["billing"], "assertion": "stated",
                                        "confidence": "high", "quote": "retries failed card charges 6 times"}]),
    }
    events = [{"role": "user", "content": f"I raised it: {PATH} now retries failed card charges 6 times.", "at": "2026-10-07T09:00:00Z"}]
    await client.post("/v1/sessions/new/events", json={"events": events, "code": {"repo": REPO, "files": {PATH: "blob-v2"}}, "live": False})
    await client.post("/v1/sessions/new/close")
    await synthesize(client, app)

    old = (await client.get(f"/v1/memories/{fact['id']}")).json()
    assert old["status"] == "superseded", old
    r = (await client.post("/v1/recall", json={"query": "How many times are failed card charges retried?"})).json()
    current = [i for i in r["items"] if i["memory"]["status"] in ("active", "stale")]
    assert [i["memory"]["statement"] for i in current] == [new_fact]
    assert current[0]["memory"]["status"] == "active" and current[0]["previous"] == FACT


@pytest.mark.asyncio
async def test_a_stale_standing_rule_is_not_served_as_a_standing_instruction(client):
    """Standing instructions are printed bare, with no staleness marker; a rule about code that
    changed must not appear there as if current. It can still come back as a flagged fact."""
    from memvine_server.main import app

    rule = f"Always call charge_with_retry() from {PATH} instead of charging cards directly."
    app.state.llm.responses = {"charge_with_retry": json.dumps([
        {"statement": rule, "kind": "procedural", "category": "rule", "subjects": ["billing"], "assertion": "stated", "confidence": "high",
         "standing": True, "quote": "Always call charge_with_retry()"},
    ])}
    r = await client.post("/v1/ingest", json={"items": [{
        "id": "rule", "content": f"## Developer\nAlways call charge_with_retry() from {PATH} instead of charging cards directly.",
        "timestamp": "2026-10-05T09:00:00Z", "code": {"repo": REPO, "files": {PATH: "blob-v1"}}}]})
    assert r.status_code == 202, r.text
    await synthesize(client, app)

    q = {"query": "how should I charge a customer's card?"}
    assert (await client.post("/v1/recall", json=q)).json()["standing"] == [rule]
    assert (await check(client, "blob-v2"))["stale"] == 1
    r = (await client.post("/v1/recall", json=q)).json()
    assert r["standing"] == []
    assert all("MAY BE OUTDATED" in line for line in lines_naming(r["briefing"], "charge_with_retry")), r["briefing"]


# ---------------------------------------------------------------- audiences

SECRET = "The Acme contract renewal price is $48,000 per year."
OPEN = "The Acme contract renews on 1 March 2027."


async def two_audiences(client):
    """'finance' is a fixed list (alice); 'acme' is a rule (company is acme). bob is in neither."""
    assert (await client.put("/v1/audiences/finance", json={"kind": "fixed", "members": ["alice@example.com"]})).status_code == 200
    assert (await client.put("/v1/audiences/acme", json={
        "kind": "rule", "conditions": [{"trait": "company", "op": "is", "value": "acme"}]})).status_code == 200
    assert (await client.put("/v1/identities/carol@acme.com", json={"traits": {"company": "acme"}})).json()["audiences"] == ["project", "acme"]
    assert (await client.put("/v1/identities/bob@example.com", json={"traits": {"company": "globex"}})).json()["audiences"] == ["project"]
    assert (await client.get("/v1/identities/alice@example.com")).json()["audiences"] == ["project", "finance"]


async def recall_as(client, identity: str | None, query: str = "Summarize what we know about the Acme contract renewal.", **extra):
    # A broad question, so every visible Acme memory is served: the fake embedder does not stem, so
    # "renews" scores more than recall's relevance margin below "renewal" and a narrow question drops it.
    body = {"query": query, **extra}
    if identity:
        body["identity"] = identity
    r = await client.post("/v1/recall", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def statements(recalled: dict) -> list[str]:
    return [i["memory"]["statement"] for i in recalled["items"]]


@pytest.mark.asyncio
async def test_a_restricted_memory_reaches_only_its_audience_and_a_project_memory_reaches_all(client):
    from memvine_server.main import app

    app.state.llm.responses = {}
    await two_audiences(client)
    restricted = await client.post("/v1/memories", json={"statement": SECRET, "kind": "semantic", "subjects": ["acme"], "audience": "finance"})
    assert restricted.status_code == 201, restricted.text
    assert restricted.json()["memory"]["audience"] == "finance"
    opened = await client.post("/v1/memories", json={"statement": OPEN, "kind": "semantic", "subjects": ["acme"]})
    assert opened.json()["memory"]["audience"] == "project"

    as_alice = await recall_as(client, "alice@example.com")
    assert SECRET in statements(as_alice) and OPEN in statements(as_alice)

    for outsider in ("bob@example.com", "carol@acme.com", "never-seen@example.com", None):
        r = await recall_as(client, outsider)
        assert OPEN in statements(r), f"{outsider} reads project-wide memory"
        assert SECRET not in statements(r) and "48,000" not in r["briefing"], f"{outsider} is outside 'finance'"

    # The MCP tool honours the same audiences.
    async def mcp_recall(identity):
        r = await client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "memory_recall", "arguments": {"query": "Acme contract renewal", "identity": identity}}},
            headers={**client.headers, "Accept": "application/json, text/event-stream"})
        return r.json()["result"]["content"][0]["text"]
    assert "48,000" in await mcp_recall("alice@example.com")
    bob_text = await mcp_recall("bob@example.com")
    assert "1 March 2027" in bob_text and "48,000" not in bob_text

    # Membership is live: removing alice from the fixed list removes her access at once.
    await client.put("/v1/audiences/finance", json={"kind": "fixed", "members": []})
    assert SECRET not in statements(await recall_as(client, "alice@example.com"))


@pytest.mark.asyncio
async def test_a_restricted_episode_leaks_through_no_recall_channel(client):
    """An episode sent to a rule audience: its facts, its raw passages and its standing rules
    reach identities whose traits match, and none of them reach anyone else, including through
    an identity's session brief. Changing an identity's traits changes what it reads."""
    from memvine_server.main import app

    await two_audiences(client)
    content = ("## Developer · 2026-10-05T09:00\nThe Acme contract renewal price is $48,000 per year, signed off by Dana.\n"
               "Always cc dana@acme.com on any message about the Acme contract renewal.")
    app.state.llm.responses = {"signed off by Dana": json.dumps([
        {"statement": SECRET, "kind": "semantic", "category": "fact", "subjects": ["acme"], "assertion": "stated", "confidence": "high",
         "quote": "The Acme contract renewal price is $48,000 per year"},
        {"statement": "Always cc dana@acme.com on any message about the Acme contract renewal.", "kind": "procedural", "category": "rule",
         "subjects": ["acme"], "assertion": "stated", "confidence": "high", "standing": True,
         "quote": "Always cc dana@acme.com"},
    ])}
    app.state.llm.default = "[]"
    r = await client.post("/v1/ingest", json={"items": [
        {"id": "acme-1", "content": content, "timestamp": "2026-10-05T09:00:00Z", "audience": "acme", "identity": "carol@acme.com"},
    ]})
    assert r.status_code == 202, r.text
    await client.post("/v1/memories", json={"statement": OPEN, "kind": "semantic", "subjects": ["acme"]})
    await synthesize(client, app)

    as_carol = await recall_as(client, "carol@acme.com", budget=4000)
    assert SECRET in statements(as_carol)
    assert any("dana@acme.com" in s for s in as_carol["standing"])
    assert any("48,000" in p["content"] for p in as_carol["passages"]), "the source passage reaches the audience"

    for outsider in ("bob@example.com", "alice@example.com", None):
        r = await recall_as(client, outsider, budget=4000)
        assert OPEN in statements(r)
        assert r["standing"] == [], f"{outsider}: no restricted standing rule"
        assert not r["passages"] or all("48,000" not in p["content"] for p in r["passages"])
        assert "48,000" not in r["briefing"] and "dana@acme.com" not in r["briefing"], f"{outsider} sees nothing of acme"

    # A session brief recalls as the session's identity.
    app.state.llm.responses["Previous state:"] = json.dumps({"goal": "Prepare the Acme contract renewal email", "decisions": [],
                                                             "open_questions": [], "in_play": [], "recent_facts": [], "progress_claims": []})
    for sid, who in (("brief-bob", "bob@example.com"), ("brief-carol", "carol@acme.com")):
        turns = [{"role": "user", "content": "Let's prepare the Acme contract renewal email."},
                 {"role": "assistant", "content": "Drafting it."}] * 2
        await client.post(f"/v1/sessions/{sid}/events", json={"events": turns, "identity": who})
    await synthesize(client, app)
    bob_brief = (await client.get("/v1/sessions/brief-bob/brief?budget=3000")).json()["text"]
    carol_brief = (await client.get("/v1/sessions/brief-carol/brief?budget=3000")).json()["text"]
    assert "1 March 2027" in bob_brief and "48,000" not in bob_brief and "dana@acme.com" not in bob_brief
    assert "48,000" in carol_brief

    # Traits drive rule audiences: bob joins acme when his company changes, and leaves when it changes back.
    await client.put("/v1/identities/bob@example.com", json={"traits": {"company": "acme"}})
    assert SECRET in statements(await recall_as(client, "bob@example.com"))
    await client.put("/v1/identities/bob@example.com", json={"traits": {"company": "globex"}})
    assert SECRET not in statements(await recall_as(client, "bob@example.com"))


@pytest.mark.asyncio
async def test_a_restricted_fact_never_merges_into_or_supersedes_a_project_fact(client):
    """A restricted restatement must not change what everyone else reads: an 'update' judged
    across audiences would supersede the project-wide fact for everybody."""
    from memvine_server.main import app

    await two_audiences(client)
    app.state.llm.responses = {"NEW (": json.dumps({"match": 1, "relation": "update", "reason": "price changed"})}
    shared = (await client.post("/v1/memories", json={"statement": "The Acme contract renewal price is $40,000 per year.",
                                                      "kind": "semantic", "subjects": ["acme"]})).json()
    secret = (await client.post("/v1/memories", json={"statement": SECRET, "kind": "semantic", "subjects": ["acme"],
                                                      "audience": "finance"})).json()
    assert secret["relation"] == "new" and secret["related"] is None, "the finance fact is not reconciled against project memory"
    assert (await client.get(f"/v1/memories/{shared['memory']['id']}")).json()["status"] == "active"
    as_bob = statements(await recall_as(client, "bob@example.com"))
    assert "The Acme contract renewal price is $40,000 per year." in as_bob and SECRET not in as_bob
