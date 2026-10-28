"""Memory history and the activity feed (events.py): every write path records the right event, a
memory's history reads in order with its evidence and the statements it points to, the feed pages
newest first, and the backfill gives memories from before the events log a history."""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from conftest import TEST_DSN, needs_db
from test_review import EPISODE_1, EPISODE_3, ep, synthesize
from test_staleness_and_audiences import PATH, REPO, check, remember_fact_from_a_session

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}
SUBJECTS = ["invoices"]


@contextmanager
def judge(app, relation: str | None):
    """The reconcile judge's next verdict: `relation` against the first related item (None: different)."""
    llm = app.state.llm
    saved = dict(llm.responses)
    llm.responses = {"NEW (": json.dumps({"match": 1 if relation else None, "relation": relation or "different", "reason": "test"})}
    try:
        yield
    finally:
        llm.responses = saved


async def remember(client, app, statement: str, relation: str | None = None, headers: dict | None = None, **extra):
    with judge(app, relation):
        r = await client.post("/v1/memories", json={"statement": statement, "kind": "semantic", "subjects": SUBJECTS, **extra},
                              headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def history(client, memory_id, headers=None) -> list[dict]:
    r = await client.get(f"/v1/memories/{memory_id}/history", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def kinds(events) -> list[str]:
    return [e["kind"] for e in events]


@pytest.mark.asyncio
async def test_reconcile_outcomes_are_recorded_on_both_items(client):
    from memvine_server.main import app

    first = await remember(client, app, "The Northwind invoice run starts at 02:00 UTC.")
    assert first["relation"] == "new"
    mid = first["memory"]["id"]
    [created] = await history(client, mid)
    assert created["kind"] == "created" and created["actor"] == "key:test"
    assert created["quotes"] == ["The Northwind invoice run starts at 02:00 UTC."], "the evidence that source gave"
    assert created["detail"]["relation"] == "new"

    # The same words again: evidence added to the same memory.
    again = await remember(client, app, "The Northwind invoice run starts at 02:00 UTC.")
    assert again["relation"] == "same" and again["memory"]["id"] == mid
    events = await history(client, mid)
    assert kinds(events) == ["created", "restated"]
    assert events[1]["episode_id"] != events[0]["episode_id"] and events[1]["quotes"]

    # A changed value: the old one is superseded by the new, and each names the other.
    update = await remember(client, app, "The Northwind invoice run now starts at 03:00 UTC.", "update")
    assert update["relation"] == "update"
    new_id = update["memory"]["id"]
    old_events = await history(client, mid)
    assert kinds(old_events) == ["created", "restated", "superseded"]
    assert old_events[-1]["detail"]["by"] == new_id
    assert old_events[-1]["related"] == [{"role": "by", "id": new_id, "status": "active",
                                          "statement": "The Northwind invoice run now starts at 03:00 UTC."}]
    new_events = await history(client, new_id)
    assert kinds(new_events) == ["created", "supersedes"]
    assert new_events[1]["related"][0]["id"] == mid and new_events[1]["related"][0]["status"] == "superseded"

    # A contradiction marks both; an extension marks the item it adds to.
    contra = await remember(client, app, "The Northwind invoice run starts at 05:00 UTC on weekends.", "contradiction")
    assert contra["relation"] == "contradiction"
    assert kinds(await history(client, contra["memory"]["id"])) == ["created", "contradicted"]
    assert kinds(await history(client, contra["related"]))[-1] == "contradicted"
    ext = await remember(client, app, "The Northwind invoice run emails a summary to finance when it starts.", "extends")
    assert ext["relation"] == "extends"
    target = await history(client, ext["related"])
    assert target[-1]["kind"] == "extended" and target[-1]["detail"]["by"] == ext["memory"]["id"]

    # Strictly ordered: by time, then by the order they were written.
    for events in (old_events, new_events, target):
        assert [(e["at"], e["id"]) for e in events] == sorted((e["at"], e["id"]) for e in events)


@pytest.mark.asyncio
async def test_review_and_corrections_are_recorded_with_who_did_them(client):
    from memvine_server.main import app

    tag = uuid.uuid4().hex[:6]
    email = f"reviewer-{tag}@example.com"
    signup = (await client.post("/v1/auth/signup", json={"email": email, "password": "x" * 12, "name": "Reviewer",
                                                         "organization": f"Review org {tag}"})).json()
    slug = f"review-{tag}"
    person = {"Authorization": f"Bearer {signup['token']}", "X-Memvine-Project": slug}
    assert (await client.post("/v1/projects", json={"name": slug, "slug": slug}, headers=person)).status_code == 201
    try:
        mem = (await remember(client, app, "Northwind refunds over 500 EUR need a second approver.", headers=person))["memory"]
        assert (await history(client, mem["id"], person))[0]["actor"] == f"person:{email}"

        assert (await client.patch(f"/v1/memories/{mem['id']}", json={"confirm": True}, headers=person)).status_code == 200
        assert (await client.patch(f"/v1/memories/{mem['id']}", json={"status": "retired"}, headers=person)).status_code == 200
        assert (await client.patch(f"/v1/memories/{mem['id']}", json={"status": "active"}, headers=person)).status_code == 200
        events = await history(client, mem["id"], person)
        assert kinds(events) == ["created", "confirmed", "forgotten", "restored"]
        assert {e["actor"] for e in events} == {f"person:{email}"}

        # Wrong: a correction supersedes it; forgetting the correction brings the old fact back.
        r = await client.post("/v1/memories", headers=person, json={
            "statement": "Northwind refunds over 1000 EUR need a second approver.", "supersedes": mem["id"]})
        assert r.status_code == 201, r.text
        fix = r.json()["memory"]
        events = await history(client, mem["id"], person)
        assert events[-1]["kind"] == "corrected" and events[-1]["detail"]["by"] == fix["id"]
        fix_events = await history(client, fix["id"], person)
        assert kinds(fix_events) == ["created", "supersedes"] and fix_events[1]["detail"]["correction"] is True
        await client.patch(f"/v1/memories/{fix['id']}", json={"status": "retired"}, headers=person)
        back = (await history(client, mem["id"], person))[-1]
        assert back["kind"] == "restored" and back["detail"]["reason"] == "replacement retired"
        assert back["related"][0]["id"] == fix["id"]

        # The operator key reads as itself.
        operator = {**MGMT, "X-Memvine-Project": slug}
        await client.patch(f"/v1/memories/{mem['id']}", json={"confirm": True}, headers=operator)
        assert (await history(client, mem["id"], operator))[-1]["actor"] == "key:operator"
    finally:
        await client.delete(f"/v1/projects/{slug}", headers=MGMT, params={"confirm": slug})


@pytest.mark.asyncio
async def test_mcp_forget_records_the_key(client):
    from memvine_server.main import app

    mem = (await remember(client, app, "Northwind invoices are numbered per legal entity."))["memory"]
    r = await client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": "memory_forget", "arguments": {"memory_id": mem["id"]}}},
                          headers={**client.headers, "Accept": "application/json, text/event-stream"})
    assert r.status_code == 200 and "Forgotten" in r.text
    last = (await history(client, mem["id"]))[-1]
    assert last["kind"] == "forgotten" and last["actor"] == "key:test"


@pytest.mark.asyncio
async def test_synthesis_code_staleness_and_revalidation(client):
    from memvine_server.main import app

    await remember_fact_from_a_session(client, app, "ev-1", "blob-v1", at="2026-10-05T09:00:00Z")
    [fact] = (await client.get("/v1/memories")).json()
    events = await history(client, fact["id"])
    assert kinds(events) == ["created", "anchored"]
    assert {e["actor"] for e in events} == {"synthesis"}
    assert events[1]["detail"] == {"repo": REPO, "files": [PATH]}

    assert (await check(client, "blob-v2"))["stale"] == 1
    stale = (await history(client, fact["id"]))[-1]
    assert stale["kind"] == "stale" and stale["detail"]["reason"].startswith(f"{PATH} changed since") and stale["actor"] == "key:test"
    assert (await check(client, "blob-v1"))["fresh"] == 1
    assert (await history(client, fact["id"]))[-1]["kind"] == "revalidated"

    # Stale again, then a later session restates it against the new code.
    assert (await check(client, "blob-v2"))["stale"] == 1
    await remember_fact_from_a_session(client, app, "ev-2", "blob-v2", at="2026-10-07T09:00:00Z")
    tail = kinds(await history(client, fact["id"]))[-4:]
    assert tail == ["stale", "restated", "anchored", "revalidated"], tail

    # Confirming a stale memory revalidates it.
    assert (await check(client, "blob-v3"))["stale"] == 1
    await client.patch(f"/v1/memories/{fact['id']}", json={"confirm": True})
    assert kinds(await history(client, fact["id"]))[-2:] == ["confirmed", "revalidated"]


@pytest.mark.asyncio
async def test_synthesis_refuses_a_forgotten_memory_and_says_so(client):
    from memvine_server.main import app

    await synthesize(client, app, [ep("e1", EPISODE_1)])
    [rule] = (await client.get("/v1/memories")).json()
    await client.patch(f"/v1/memories/{rule['id']}", json={"status": "retired"})
    await synthesize(client, app, [ep("e3", EPISODE_3, day="2026-10-21")])
    last = (await history(client, rule["id"]))[-1]
    assert last["kind"] == "refused" and last["actor"] == "synthesis" and last["episode_id"] == "e3"
    assert last["detail"] == {"reason": "forgotten"}


@pytest.mark.asyncio
async def test_deleting_a_source_deletes_the_history_and_leaves_a_feed_entry(client):
    from memvine_server.main import app

    old = (await remember(client, app, "The Northwind dunning emails go out on day 7."))["memory"]
    new = (await remember(client, app, "The Northwind dunning emails now go out on day 10.", "update"))["memory"]
    source = new["evidence"][0]["episode_id"]
    assert (await client.delete(f"/v1/episodes/{source}")).status_code == 204
    assert (await client.get(f"/v1/memories/{new['id']}/history")).status_code == 404
    back = (await history(client, old["id"]))[-1]
    assert back["kind"] == "restored" and back["detail"]["reason"] == "replacement deleted with its source"
    feed = (await client.get("/v1/events", params={"kind": "deleted"})).json()["events"]
    assert [e["detail"]["memory_id"] for e in feed] == [new["id"]]
    assert feed[0]["memory_id"] is None and feed[0]["statement"] is None and feed[0]["episode_id"] == source


@pytest.mark.asyncio
async def test_feed_pages_newest_first_with_filters(client):
    from memvine_server.main import app

    ids = [(await remember(client, app, f"Northwind ledger {n} closes on day {n}."))["memory"]["id"] for n in range(5)]
    await client.patch(f"/v1/memories/{ids[0]}", json={"confirm": True})

    seen, cursor, pages = [], None, 0
    while True:
        r = await client.get("/v1/events", params={"limit": 2, **({"cursor": cursor} if cursor else {})})
        assert r.status_code == 200, r.text
        body = r.json()
        seen += body["events"]
        pages += 1
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert pages == 3 and len(seen) == 6
    assert len({e["id"] for e in seen}) == 6
    assert [(e["at"], e["id"]) for e in seen] == sorted(((e["at"], e["id"]) for e in seen), reverse=True)
    assert seen[0]["kind"] == "confirmed" and seen[0]["statement"] == "Northwind ledger 0 closes on day 0."

    created = (await client.get("/v1/events", params={"kind": "created"})).json()["events"]
    assert [e["memory_id"] for e in created] == ids[::-1]
    both = (await client.get("/v1/events", params=[("kind", "created"), ("kind", "confirmed")])).json()["events"]
    assert len(both) == 6
    one = (await client.get("/v1/events", params={"memory_id": ids[0]})).json()["events"]
    assert kinds(one) == ["confirmed", "created"]
    assert (await client.get("/v1/events", params={"since": "2999-01-01T00:00:00Z"})).json() == {"events": [], "next_cursor": None}
    assert (await client.get("/v1/events", params={"kind": "exploded"})).status_code == 400
    assert (await client.get("/v1/events", params={"cursor": "not-a-cursor"})).status_code == 400

    export = (await client.post("/v1/export")).json()
    assert len(export["memory_events"]) == 6


@pytest.mark.asyncio
async def test_expired_is_computed_on_read(client):
    from memvine_server.main import app

    mem = (await remember(client, app, "The Northwind scratch org is up until 1 Oct."))["memory"]
    async with app.state.pool.connection() as conn:
        await conn.execute("UPDATE memory_items SET valid_to = now() WHERE id = %s", (mem["id"],))
    events = await history(client, mem["id"])
    assert kinds(events) == ["created", "expired"] and events[1]["id"] is None


@pytest.mark.asyncio
async def test_backfill_gives_old_memories_a_history_once(client):
    """The migration's backfill, run over the sample with its events removed (as on a database from
    before the events log), and run again: the same rows."""
    slug = f"{client.project_slug}-bf"
    assert (await client.post("/v1/projects", headers=MGMT, json={"name": slug, "slug": slug, "sample": True})).status_code == 201
    headers = {**MGMT, "X-Memvine-Project": slug}
    try:
        sql = (Path(__file__).parents[1] / "memvine_server/migrations/0006_memory_events.sql").read_text()
        backfill = sql[sql.index("-- Backfill"):]
        async with await psycopg.AsyncConnection.connect(TEST_DSN, autocommit=True) as conn:
            pid = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())[0]
            await conn.execute("DELETE FROM memory_events WHERE project_id = %s", (pid,))
            await conn.execute(backfill)
            count = "SELECT kind, count(*) FROM memory_events WHERE project_id = %s GROUP BY kind ORDER BY kind"
            first = await (await conn.execute(count, (pid,))).fetchall()
            await conn.execute(backfill)
            assert await (await conn.execute(count, (pid,))).fetchall() == first, "idempotent"
            memories = (await (await conn.execute("SELECT count(*) FROM memory_items WHERE project_id = %s", (pid,))).fetchone())[0]
        by_kind = dict(first)
        assert by_kind["created"] == memories and by_kind["superseded"] == by_kind["supersedes"] >= 1
        assert by_kind["contradicted"] >= 2
        superseded = [m for m in (await client.get("/v1/memories", headers=headers, params={"status": "superseded"})).json()]
        events = await history(client, superseded[0]["id"], headers)
        assert kinds(events) == ["created", "superseded"] and events[0]["detail"]["backfill"] is True
        assert events[0]["quotes"], "the evidence of its first source"
        assert events[1]["related"][0]["id"] == superseded[0]["superseded_by"]
    finally:
        await client.delete(f"/v1/projects/{slug}", headers=MGMT, params={"confirm": slug})
