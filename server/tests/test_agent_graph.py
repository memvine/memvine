"""The agent sees the same graph: briefing lines name their edges in the graph's words
("[decision 15 Oct, supersedes 6 Oct] ...", "anchored src/x.py, MAY BE OUTDATED", "inferred,
derived from 2 memories", "contradicts", "extends"), and a session's kept summary ("Memory kept 1
fact and 1 event; 1 fact superseded.") reports what synthesis made of it, with a way to forget."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest
from conftest import needs_db
from test_e2e import EXTRACT_2, SESSION_1, SESSION_2, ingest_and_synthesize, mcp_call

from memvine_server.models import KeptMemory
from memvine_server.recall import render_line
from memvine_server.sessions import kept_summary
from memvine_server.worker import drain

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}


def lines_with(briefing: str, text: str) -> list[str]:
    return [line for line in briefing.splitlines() if text in line and (line.startswith("[") or line.startswith("- ["))]


@pytest.fixture
async def sample(client):
    """A project loaded with the sample, addressed with the management key."""
    slug = f"{client.project_slug}-a"
    r = await client.post("/v1/projects", headers=MGMT, json={"name": slug, "slug": slug})
    assert r.status_code == 201, r.text
    headers = {**MGMT, "X-Memvine-Project": slug}
    assert (await client.post("/v1/sample", headers=headers)).status_code == 200
    items = {m["statement"]: m for m in (await client.get("/v1/memories", headers=headers)).json()}
    try:
        yield headers, items, slug
    finally:
        await client.delete(f"/v1/projects/{slug}", headers=MGMT)


def find(items: dict, prefix: str) -> dict:
    return next(m for s, m in items.items() if s.startswith(prefix))


@pytest.mark.asyncio
async def test_sample_brief_names_supersedes_and_contradicts(client, sample):
    headers, items, _ = sample
    r = (await client.post("/v1/recall", headers=headers, json={"query": "When is the launch?", "budget": 2200})).json()
    [line] = lines_with(r["briefing"], "The public launch moved to")
    assert re.match(r"^\[decision \d{1,2} \w{3}, supersedes \d{1,2} \w{3}\] The public launch moved to \d{1,2} \w{3} because legal "
                    r"pushed back on the refund copy \(was: The public launch is planned for \d{1,2} \w{3}\)$", line), line

    r = (await client.post("/v1/recall", headers=headers, json={"query": "Do weekend escalations page anyone?"})).json()
    wait = lines_with(r["briefing"], "Weekend escalations wait")
    page = lines_with(r["briefing"], "Weekend escalations page")
    assert wait and page
    assert all(", contradicts " in line for line in wait + page), r["briefing"]
    # The pair is quoted once, in the section that asks which is right; the lines only name the edge.
    assert "Conflicting statements on record" in r["briefing"]
    assert r["briefing"].count("Weekend escalations wait in #support-tier2 until Monday") == 2  # pair + its own line


@pytest.mark.asyncio
async def test_sample_brief_names_extends_derived_and_a_stale_anchor(client, sample):
    from memvine_server.main import app

    headers, items, slug = sample
    merges, release = find(items, "Only Priya merges"), find(items, "Release 4.2.0 ships")
    refunds, launch_old, escalations = find(items, "Refunds over $500"), find(items, "The public launch is planned"), find(items, "Escalations go to")
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]
        await conn.execute("INSERT INTO memory_links (project_id, from_id, to_id, kind) VALUES (%s, %s, %s, 'extends')",
                           (project_id, merges["id"], release["id"]))
        await conn.execute("UPDATE memory_items SET assertion = 'inferred' WHERE id = %s", (refunds["id"],))
        for premise in (launch_old, escalations):
            await conn.execute("INSERT INTO memory_links (project_id, from_id, to_id, kind) VALUES (%s, %s, %s, 'derived')",
                               (project_id, refunds["id"], premise["id"]))
        await conn.execute("INSERT INTO memory_anchors (project_id, memory_id, repo, path, blob) VALUES (%s, %s, 'acme/app', %s, 'a1')",
                           (project_id, escalations["id"], "src/escalation/router.ts"))
    checked = await client.post("/v1/code/check", headers=headers,
                                json={"repo": "acme/app", "files": {"src/escalation/router.ts": "b2"}})
    assert checked.json()["stale"] == 1

    r = (await client.post("/v1/recall", headers=headers, json={"query": "Who merges release work?"})).json()
    [line] = lines_with(r["briefing"], "Only Priya merges")
    assert re.search(r", extends \d{1,2} \w{3}\] Only Priya merges", line), line
    # The older fact it adds to follows, clipped: it is context for this line, not a line of its own.
    tail = line.split(" (adds to: ", 1)[1]
    assert tail.startswith("Release 4.2.0 ships PDF invoice export (#429)") and tail.endswith("…)") and len(tail) == 121, line

    r = (await client.post("/v1/recall", headers=headers, json={"query": "Who approves refunds over $500?"})).json()
    [line] = lines_with(r["briefing"], "Refunds over $500")
    assert re.match(r"^\[rule \d{1,2} \w{3}, inferred, derived from 2 memories\] Refunds over \$500", line), line

    r = (await client.post("/v1/recall", headers=headers, json={"query": "Where do escalations go?"})).json()
    [line] = lines_with(r["briefing"], "Escalations go to the #support-tier2")
    assert re.match(r"^\[rule \d{1,2} \w{3}, anchored src/escalation/router.ts, MAY BE OUTDATED\] Escalations go to", line), line
    assert re.search(r"\(src/escalation/router.ts changed since \d{1,2} \w{3} \d{4}; check the code\)$", line), line


def test_render_line_words():
    now = datetime.now(UTC)
    row = {"statement": "Retries cap at 3.", "category": "gotcha", "kind": "semantic", "recorded_at": now, "valid_to": None,
           "assertion": "claimed", "status": "stale", "stale_reason": "src/queue.py changed since 3 Oct 2026"}
    line = render_line(row, None, None, {"anchored": ["src/queue.py", "src/retry.py"]})
    assert line == (f"[gotcha {now.strftime('%-d %b')}, assistant said, unverified, anchored src/queue.py +1, MAY BE OUTDATED] "
                    "Retries cap at 3 (src/queue.py changed since 3 Oct 2026; check the code)")
    old = {**row, "category": None, "kind": "procedural", "assertion": "stated", "status": "superseded", "stale_reason": None,
           "recorded_at": datetime(2024, 3, 1, tzinfo=UTC), "valid_to": datetime(2024, 4, 2, tzinfo=UTC)}
    assert render_line(old, None) == "[procedure 1 Mar 2024, superseded 2 Apr 2024] Retries cap at 3"


def test_kept_summary_words():
    def k(category):
        return KeptMemory(id="00000000-0000-0000-0000-000000000000", category=category, statement="x")
    assert kept_summary([k("gotcha"), k("decision"), k("decision")], 1, 0) == "Memory kept 2 decisions and 1 gotcha; 1 fact superseded."
    assert kept_summary([k("fact"), k("rule"), k("event")], 2, 1) == (
        "Memory kept 1 rule, 1 fact and 1 event; 2 facts superseded, 1 contradiction to settle.")
    assert kept_summary([], 0, 0) == "Memory kept nothing new."


@pytest.mark.asyncio
async def test_session_kept_and_forget_the_last_one(client):
    from memvine_server.main import app

    await ingest_and_synthesize(client, app, [{"id": "s1", "content": SESSION_1, "timestamp": "2026-09-18T10:11:00Z"}])
    assert (await client.get("/v1/sessions/nope/kept")).status_code == 404
    # The session moves the launch and restates the refunds rule: a restatement is evidence for the
    # old memory, not something this session kept.
    restated = {"statement": "Refunds over $500 need a human approver; the bot may only draft them.", "kind": "procedural", "category": "rule",
                "subjects": ["refunds"], "assertion": "stated", "confidence": "high", "quote": "refunds over $500 still need a human"}
    app.state.llm.responses = {
        "moving launch from 29 Oct to 12 Nov": json.dumps([*json.loads(EXTRACT_2), restated]),
        "): Refunds over $500 need a human approver": json.dumps({"match": 1, "relation": "same", "reason": "restated"}),
        **app.state.llm.responses,
    }
    turns = SESSION_2.split("\n\n")
    events = [{"role": "user", "content": turns[0].split("\n", 1)[1], "at": "2026-10-03T14:02:00Z"},
              {"role": "assistant", "content": turns[1].split("\n", 1)[1], "at": "2026-10-03T14:03:00Z"},
              {"role": "user", "content": "And refunds over $500 still need a human approver.", "at": "2026-10-03T14:04:00Z"}]
    assert (await client.post("/v1/sessions/ses_k/events", json={"events": events})).status_code == 202

    live = (await client.get("/v1/sessions/ses_k/kept")).json()
    assert live["tending"] and live["kept"] == []
    await client.post("/v1/sessions/ses_k/close")
    assert (await client.get("/v1/sessions/ses_k/kept")).json()["tending"], "extraction still queued"
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, project_id)

    done = (await client.get("/v1/sessions/ses_k/kept")).json()
    assert not done["tending"]
    statements = [k["statement"] for k in done["kept"]]
    assert statements[0].startswith("Launch date is 12 Nov 2026") and len(statements) == 2, statements
    assert not any(s.startswith("Launch date is 29 Oct") or s.startswith("Refunds") for s in statements), "restated, not kept"
    assert done["kept"][0]["previous"] == "Launch date is 29 Oct 2026."
    assert done["superseded"] == 1
    assert done["summary"] == "Memory kept 2 facts; 1 fact superseded."

    # "Forget the last one": the MCP tool retires it, and the fact it replaced is current again.
    launch = done["kept"][0]
    forgot = await mcp_call(client, "tools/call", {"name": "memory_forget", "arguments": {"memory_id": launch["id"]}})
    assert forgot["result"]["content"][0]["text"].startswith("Forgotten: Launch date is 12 Nov 2026")
    after = (await client.get("/v1/sessions/ses_k/kept")).json()
    assert [k["statement"] for k in after["kept"]] == statements[1:] and after["summary"] == "Memory kept 1 fact."
    old = (await client.get(f"/v1/memories/{launch['supersedes']}")).json()
    assert old["status"] == "active" and old["valid_to"] is None
    r = (await client.post("/v1/recall", json={"query": "When is the launch?"})).json()
    assert r["items"][0]["memory"]["statement"] == "Launch date is 29 Oct 2026."
    again = await mcp_call(client, "tools/call", {"name": "memory_forget", "arguments": {"memory_id": launch["id"]}})
    assert again["result"]["content"][0]["text"].startswith("Not current")
