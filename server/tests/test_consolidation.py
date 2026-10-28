"""Background consolidation (consolidation.py): duplicates merged, missed updates and contradictions
linked, memories that no longer pass the bar flagged; never across audiences, never a person's memory,
nothing deleted, every action an event, a second run does nothing, and an un-merge restores."""
from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta

import pytest
from conftest import needs_db

from memvine_server import consolidation
from memvine_server.embeddings import DIMENSIONS
from memvine_server.extraction.prompts import CONSOLIDATE_BAR_SYSTEM, CONSOLIDATE_PAIRS_SYSTEM
from memvine_server.worker import drain

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}


def vec(axis: int, cosine: float = 1.0, other: int | None = None) -> list[float]:
    """A unit vector at `cosine` to the axis vector e[axis] (leaning toward e[other])."""
    v = [0.0] * DIMENSIONS
    v[axis] = cosine
    if cosine < 1.0:
        v[other if other is not None else axis + 1] = math.sqrt(1 - cosine * cosine)
    return v


class Judge:
    """A fake fast model for the two consolidation prompts. `pairs` maps (older text, newer text)
    fragments to a relation (default different); `fails` lists statement fragments that fail the bar."""

    def __init__(self, pairs: dict | None = None, fails: list[str] | None = None) -> None:
        self.pairs, self.fails, self.calls = pairs or {}, fails or [], []

    async def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        self.calls.append((system, user))
        if system == CONSOLIDATE_PAIRS_SYSTEM:
            out = []
            for n, older, newer in re.findall(r"PAIR (\d+)\nOLDER \([^)]*\): (.*)\nNEWER \([^)]*\): (.*)", user):
                relation = next((r for (o, w), r in self.pairs.items() if o in older and w in newer), "different")
                out.append({"pair": int(n), "relation": relation, "reason": "test"})
            return json.dumps({"pairs": out})
        if system == CONSOLIDATE_BAR_SYSTEM:
            out = []
            for n, statement in re.findall(r"ITEM (\d+)\nstatement: (.*)", user):
                failing = any(f in statement for f in self.fails)
                out.append({"item": int(n), "passes": not failing, "fails": "not in the code" if failing else None,
                            "reason": "describes what the code does" if failing else "fine"})
            return json.dumps({"items": out})
        return "[]"

    def asked(self, system: str) -> list[str]:
        return [u for s, u in self.calls if s == system]


async def pid_of(app, slug):
    async with app.state.pool.connection() as conn:
        return (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]


_count = 0


async def add(conn, project_id, statement, embedding, *, kind="semantic", category="fact", audience="project",
              review="new", origin="synthesis", standing=False, confidence="medium", subjects=("invoices",),
              days_ago=1, recalled=False, status="active", valid_to=None, forgotten=False):
    global _count
    _count += 1
    at = datetime.now(UTC) - timedelta(days=days_ago, seconds=-_count)
    episode = f"ep-{_count}"
    await conn.execute("INSERT INTO episodes (project_id, id, content, content_hash, occurred_at, audience) "
                       "VALUES (%s, %s, %s, 'h', %s, %s)", (project_id, episode, f"User: {statement}", at, audience))
    row = await (await conn.execute(
        "INSERT INTO memory_items (project_id, kind, statement, subjects, valid_from, recorded_at, assertion, confidence, "
        "embedding, audience, standing, category, origin, review, created_at, last_recalled_at, status, valid_to, forgotten_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'stated', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (project_id, kind, statement, list(subjects), at, at, confidence, embedding, audience, standing, category, origin,
         review, at, at if recalled else None, status, valid_to, at if forgotten else None))).fetchone()
    await conn.execute("INSERT INTO evidence (memory_id, project_id, episode_id, quote) VALUES (%s, %s, %s, %s)",
                       (row["id"], project_id, episode, statement))
    return row["id"]


async def fillers(conn, project_id, n=10, start=200):
    """Unrelated decisions (orthogonal vectors, too new for the bar) so the project is big enough."""
    for i in range(n):
        await add(conn, project_id, f"Filler decision number {i} about topic {i}.", vec(start + i), kind="semantic",
                  category="decision", days_ago=0, subjects=(f"topic{i}",))


async def item(conn, memory_id):
    return await (await conn.execute("SELECT * FROM memory_items WHERE id = %s", (memory_id,))).fetchone()


async def event_kinds(conn, memory_id):
    return [(r["kind"], r["actor"]) for r in await (await conn.execute(
        "SELECT kind, actor FROM memory_events WHERE memory_id = %s ORDER BY id", (memory_id,))).fetchall()]


async def run(app, project_id, judge):
    async with app.state.pool.connection() as conn, conn.transaction():
        return await consolidation.consolidate(conn, judge, project_id)


@pytest.mark.asyncio
async def test_near_identical_restatements_merge_without_the_model(client):
    from memvine_server.main import app

    pid = await pid_of(app, client.project_slug)
    async with app.state.pool.connection() as conn:
        await fillers(conn, pid)
        old = await add(conn, pid, "The Northwind invoice run starts at 02:00 UTC.", vec(1), days_ago=5)
        new = await add(conn, pid, "Northwind's invoice run starts at 02:00 UTC every night.", vec(1, 0.95), days_ago=2)
    judge = Judge()
    stats = await run(app, pid, judge)
    assert stats["merged"] == 1 and not judge.asked(CONSOLIDATE_PAIRS_SYSTEM)
    async with app.state.pool.connection() as conn:
        kept, away = await item(conn, old), await item(conn, new)
        assert kept["status"] == "active" and away["status"] == "retired"
        assert away["retired_reason"] == "merged" and away["merged_into"] == old and away["forgotten_at"] is None
        quotes = {r["quote"] for r in await (await conn.execute("SELECT quote FROM evidence WHERE memory_id = %s", (old,))).fetchall()}
        assert "Northwind's invoice run starts at 02:00 UTC every night." in quotes  # evidence copied onto the kept one
        assert await (await conn.execute("SELECT count(*) AS n FROM evidence WHERE memory_id = %s", (new,))).fetchone() == {"n": 1}
        assert ("merged", "consolidation") in await event_kinds(conn, old)
        assert ("merged", "consolidation") in await event_kinds(conn, new)
        detail = (await (await conn.execute("SELECT detail FROM memory_events WHERE memory_id = %s AND kind = 'merged'", (new,))).fetchone())["detail"]
        assert detail["into"] == str(old)
    # Merged-away memories are not recalled.
    r = (await client.post("/v1/recall", json={"query": "When does the Northwind invoice run start?"})).json()
    assert all(i["memory"]["id"] != str(new) for i in r["items"])


@pytest.mark.asyncio
async def test_changed_value_goes_to_the_model_and_supersedes(client):
    from memvine_server.main import app

    pid = await pid_of(app, client.project_slug)
    async with app.state.pool.connection() as conn:
        await fillers(conn, pid)
        old = await add(conn, pid, "The export worker retries 3 times.", vec(1), days_ago=9, recalled=True)
        new = await add(conn, pid, "The export worker now retries 5 times.", vec(1, 0.95), days_ago=1, recalled=True)
    judge = Judge(pairs={("retries 3", "retries 5"): "update"})
    stats = await run(app, pid, judge)
    assert stats["superseded"] == 1 and stats["linked"] == 1 and stats["merged"] == 0
    async with app.state.pool.connection() as conn:
        o, n = await item(conn, old), await item(conn, new)
        assert o["status"] == "superseded" and o["superseded_by"] == new and o["valid_to"] is not None
        assert n["status"] == "active" and n["supersedes"] == old
        assert ("superseded", "consolidation") in await event_kinds(conn, old)
        assert ("supersedes", "consolidation") in await event_kinds(conn, new)


@pytest.mark.asyncio
async def test_model_judged_pairs_merge_link_or_flag(client):
    from memvine_server.main import app

    pid = await pid_of(app, client.project_slug)
    async with app.state.pool.connection() as conn:
        await fillers(conn, pid)
        same_a = await add(conn, pid, "Refunds over 500 dollars need a finance approver.", vec(1), category="rule", kind="procedural")
        same_b = await add(conn, pid, "A finance person must approve any refund above 500 dollars.", vec(1, 0.90), category="rule", kind="procedural")
        unsure_a = await add(conn, pid, "The staging bucket is northwind-stage.", vec(3))
        unsure_b = await add(conn, pid, "Staging files live in the northwind-staging-eu bucket.", vec(3, 0.89, 4))
        clash_a = await add(conn, pid, "The ledger closes on Fridays.", vec(6), subjects=("ledger",))
        clash_b = await add(conn, pid, "The ledger closes on Mondays.", vec(6, 0.85, 7), subjects=("ledger",))
        lonely_a = await add(conn, pid, "The payments team owns the webhook relay.", vec(9), subjects=("relay",))
        lonely_b = await add(conn, pid, "Webhook relay logs go to the audit store.", vec(9, 0.83, 10), subjects=("logs",))  # no shared subject, < 0.88
    judge = Judge(pairs={("finance approver", "finance person"): "same", ("northwind-stage.", "staging-eu"): "unsure",
                         ("Fridays", "Mondays"): "contradiction"})
    stats = await run(app, pid, judge)
    asked = "\n".join(judge.asked(CONSOLIDATE_PAIRS_SYSTEM))
    assert len(judge.asked(CONSOLIDATE_PAIRS_SYSTEM)) == 1  # one batched call
    assert "webhook relay" not in asked.lower()  # not alike enough without a shared subject
    assert stats["merged"] == 1 and stats["contradictions"] == 1 and stats["flagged"] == 1
    async with app.state.pool.connection() as conn:
        assert (await item(conn, same_b))["merged_into"] == same_a
        flagged = await item(conn, unsure_b)
        assert flagged["flag"] == "possible_duplicate" and flagged["flag_related"] == unsure_a and flagged["status"] == "active"
        assert ("flagged", "consolidation") in await event_kinds(conn, unsure_b)
        assert (await item(conn, clash_a))["conflicts_with"] == [clash_b]
        assert (await item(conn, clash_b))["conflicts_with"] == [clash_a]
        for i in (lonely_a, lonely_b, clash_a, clash_b, same_a):
            assert (await item(conn, i))["status"] == "active"
    flagged = (await client.get("/v1/memories", params={"flag": "possible_duplicate"})).json()
    assert [m["id"] for m in flagged] == [str(unsure_b)] and flagged[0]["flag_reason"]


@pytest.mark.asyncio
async def test_never_across_audiences_nor_on_stale_expired_or_forgotten(client):
    from memvine_server.main import app

    pid = await pid_of(app, client.project_slug)
    assert (await client.put("/v1/audiences/finance", json={"kind": "fixed", "members": ["ana"]})).status_code == 200
    async with app.state.pool.connection() as conn:
        await fillers(conn, pid)
        a = await add(conn, pid, "The tax export runs on the 1st.", vec(1))
        b = await add(conn, pid, "The tax export runs on the 1st.", vec(1, 0.99), audience="finance")
        c = await add(conn, pid, "The FX feed refreshes hourly.", vec(3))
        d = await add(conn, pid, "The FX feed refreshes hourly.", vec(3, 0.99), status="stale")
        e = await add(conn, pid, "Card retries pause at night.", vec(5))
        f = await add(conn, pid, "Card retries pause at night.", vec(5, 0.99), valid_to=datetime.now(UTC) - timedelta(days=1))
        g = await add(conn, pid, "The SFTP drop is checked at 06:00.", vec(7))
        h = await add(conn, pid, "The SFTP drop is checked at 06:00.", vec(7, 0.99), status="retired", forgotten=True)
    judge = Judge()
    stats = await run(app, pid, judge)
    assert stats["merged"] == 0 and stats["flagged"] == 0 and not judge.asked(CONSOLIDATE_PAIRS_SYSTEM)
    async with app.state.pool.connection() as conn:
        for i in (a, b, c, e, g):
            assert (await item(conn, i))["status"] == "active"
        assert (await item(conn, d))["status"] == "stale" and (await item(conn, h))["status"] == "retired"
        assert (await item(conn, f))["merged_into"] is None


@pytest.mark.asyncio
async def test_a_persons_memories_are_only_flagged_and_standing_rules_survive(client):
    from memvine_server.main import app

    pid = await pid_of(app, client.project_slug)
    async with app.state.pool.connection() as conn:
        await fillers(conn, pid)
        kept_a = await add(conn, pid, "Deploys go out on Tuesdays.", vec(1), review="kept", category="rule", kind="procedural")
        kept_b = await add(conn, pid, "Deploys go out on Tuesdays only.", vec(1, 0.97), origin="person", review="kept",
                           category="rule", kind="procedural")
        weak = await add(conn, pid, "Always run the invoice dry-run first.", vec(3), category="rule", kind="procedural", confidence="high")
        rule = await add(conn, pid, "Always run the invoice dry-run first, every time.", vec(3, 0.96), category="rule",
                         kind="procedural", standing=True)
        new_one = await add(conn, pid, "Use the eu-west replica for reports.", vec(5), days_ago=3)
        kept_one = await add(conn, pid, "Reports use the eu-west replica.", vec(5, 0.96), review="kept", days_ago=1)
        outdated = await add(conn, pid, "The SLA is 4 hours.", vec(7), review="kept", days_ago=9)
        newer = await add(conn, pid, "The SLA is now 2 hours.", vec(7, 0.95), days_ago=1)
        # The bar never re-checks a kept or person-made memory, however old and unused.
        old_kept = await add(conn, pid, "The billing module calls the tax service.", vec(9), review="kept", days_ago=30)
        old_person = await add(conn, pid, "The tax module has three classes.", vec(11), origin="person", days_ago=30)
    judge = Judge(pairs={("SLA is 4", "SLA is now 2"): "update"}, fails=["module"])
    await run(app, pid, judge)
    async with app.state.pool.connection() as conn:
        a, b = await item(conn, kept_a), await item(conn, kept_b)
        assert a["status"] == b["status"] == "active" and b["flag"] == "possible_duplicate" and b["flag_related"] == kept_a
        assert (await item(conn, weak))["merged_into"] == rule  # the standing rule is kept, however confident the other
        assert (await item(conn, rule))["status"] == "active"
        assert (await item(conn, new_one))["merged_into"] == kept_one  # the kept one survives, even though newer
        o = await item(conn, outdated)
        assert o["status"] == "active" and o["flag"] == "possible_outdated" and o["flag_related"] == newer
        for i in (old_kept, old_person):
            assert (await item(conn, i))["flag"] is None
    assert not any("module" in u for u in judge.asked(CONSOLIDATE_BAR_SYSTEM))


@pytest.mark.asyncio
async def test_the_bar_flags_what_no_longer_passes(client):
    from memvine_server.main import app

    pid = await pid_of(app, client.project_slug)
    assert (await client.patch(f"/v1/projects/{client.project_slug}", headers=MGMT,
                               json={"memory_instructions": "Never keep customer names."})).status_code == 200
    assert (await client.post(f"/v1/projects/{client.project_slug}/categories", headers=MGMT,
                              json={"slug": "runbook", "name": "Runbook"})).status_code == 201
    async with app.state.pool.connection() as conn:
        await fillers(conn, pid)
        code = await add(conn, pid, "The InvoiceBuilder class in billing/builder.py assembles line items.", vec(1), days_ago=0)
        fact = await add(conn, pid, "The tax provider sandbox resets every Sunday.", vec(3), days_ago=0)
        runbook = await add(conn, pid, "To rotate the SFTP key, the module reload step comes last.", vec(5), category="runbook", days_ago=0)
        old_unused = await add(conn, pid, "Old decision about the module layout.", vec(7), category="decision", days_ago=10)
        old_used = await add(conn, pid, "Old decision about the module naming.", vec(9), category="decision", days_ago=10, recalled=True)
    judge = Judge(fails=["InvoiceBuilder", "module"])
    stats = await run(app, pid, judge)
    asked = judge.asked(CONSOLIDATE_BAR_SYSTEM)
    assert len(asked) == 1 and "Never keep customer names." in asked[0]  # the project's memory instructions go along
    assert "module naming" not in asked[0] and "Filler" not in asked[0]  # recalled, and too new, decisions are not re-checked
    assert stats["checked_items"] == 4 and stats["flags"] == {"no_longer_passes": 3}
    async with app.state.pool.connection() as conn:
        c = await item(conn, code)
        assert c["flag"] == "no_longer_passes" and c["status"] == "active" and c["forgotten_at"] is None
        assert "describes what the code does" in c["flag_reason"] and c["flag_reason"].startswith("Not in the code")
        assert (await item(conn, fact))["flag"] is None
        assert (await item(conn, runbook))["flag"] == "no_longer_passes"  # a project's own category is re-checked like a fact
        assert (await item(conn, old_unused))["flag"] == "no_longer_passes"
        assert (await item(conn, old_used))["flag"] is None

    # Review: the flag is listed with its reason; Keep clears it, Dismiss clears it, Forget forgets.
    listed = {m["id"]: m for m in (await client.get("/v1/memories", params={"flag": "any"})).json()}
    assert set(listed) == {str(code), str(runbook), str(old_unused)}
    project = next(p for p in (await client.get("/v1/projects", headers=MGMT)).json() if p["slug"] == client.project_slug)
    assert project["flagged_count"] == 3
    kept = (await client.patch(f"/v1/memories/{code}", json={"confirm": True})).json()
    assert kept["flag"] is None and kept["review"] == "kept"
    dismissed = (await client.patch(f"/v1/memories/{runbook}", json={"flag": None})).json()
    assert dismissed["flag"] is None and dismissed["review"] == "new" and dismissed["status"] == "active"
    forgotten = (await client.patch(f"/v1/memories/{old_unused}", json={"status": "retired"})).json()
    assert forgotten["status"] == "retired" and forgotten["retired_reason"] == "forgotten" and forgotten["flag"] is None
    kinds = [e["kind"] for e in (await client.get(f"/v1/memories/{runbook}/history")).json()]
    assert kinds[-2:] == ["flagged", "unflagged"]
    assert (await client.get("/v1/memories", params={"flag": "any"})).json() == []
    # Dismissed and kept stay so: a later run does not ask about them again.
    judge2 = Judge(fails=["InvoiceBuilder", "module"])
    await run(app, pid, judge2)
    assert not judge2.calls


@pytest.mark.asyncio
async def test_bounds_per_run(client, monkeypatch):
    from memvine_server.main import app

    monkeypatch.setattr(consolidation, "MAX_JUDGED_PAIRS", 3)
    monkeypatch.setattr(consolidation, "MAX_BAR_ITEMS", 2)
    monkeypatch.setattr(consolidation, "MAX_RULE_MERGES", 2)
    pid = await pid_of(app, client.project_slug)
    async with app.state.pool.connection() as conn:
        await fillers(conn, pid)
        for i in range(5):  # five pairs the model must judge
            await add(conn, pid, f"Region {i} uses {i} replicas.", vec(10 + 2 * i), recalled=True, category="decision", days_ago=0)
            await add(conn, pid, f"Region {i} now runs {i + 1} replicas.", vec(10 + 2 * i, 0.9), recalled=True, category="decision", days_ago=0)
        for i in range(4):  # four pairs that merge by rule
            await add(conn, pid, f"Queue {i} is called q{i}.", vec(40 + 2 * i), category="decision", days_ago=0)
            await add(conn, pid, f"The queue {i} is called q{i}.", vec(40 + 2 * i, 0.97), category="decision", days_ago=0)
        for i in range(5):  # five facts for the bar
            await add(conn, pid, f"Fact number {i} about the mailer.", vec(60 + i), days_ago=0)
    judge = Judge()
    stats = await run(app, pid, judge)
    assert stats["merged"] == 2 and stats["judged_pairs"] == 3 and stats["checked_items"] == 2
    asked = judge.asked(CONSOLIDATE_PAIRS_SYSTEM)[0]
    assert asked.count("PAIR ") == 3
    assert judge.asked(CONSOLIDATE_BAR_SYSTEM)[0].count("ITEM ") == 2
    # The next run takes the rest.
    judge2 = Judge()
    stats2 = await run(app, pid, judge2)
    assert stats2["merged"] == 2 and stats2["judged_pairs"] == 2 and stats2["checked_items"] == 2


@pytest.mark.asyncio
async def test_second_run_does_nothing_and_unmerge_restores(client):
    from memvine_server.main import app

    pid = await pid_of(app, client.project_slug)
    async with app.state.pool.connection() as conn:
        await fillers(conn, pid)
        old = await add(conn, pid, "Dunning emails go out at 09:00 local time.", vec(1), category="decision")
        new = await add(conn, pid, "Dunning emails are sent at 09:00 local time.", vec(1, 0.96), category="decision")
        await add(conn, pid, "The VAT rate table lives in the tax service.", vec(3))
        await add(conn, pid, "The VAT rates are kept by the tax service.", vec(3, 0.9))
        before = await (await conn.execute("SELECT count(*) AS n FROM evidence WHERE memory_id = %s", (old,))).fetchone()
    judge = Judge(fails=["VAT rates"])
    first = await run(app, pid, judge)
    assert first["merged"] == 1 and first["judged_pairs"] == 1 and first["flagged"] == 1

    async def state():
        async with app.state.pool.connection() as conn:
            return (await (await conn.execute(
                "SELECT id, status, flag, merged_into, superseded_by, conflicts_with FROM memory_items WHERE project_id = %s ORDER BY id",
                (pid,))).fetchall(),
                (await (await conn.execute("SELECT count(*) AS n FROM memory_events WHERE project_id = %s", (pid,))).fetchone())["n"],
                (await (await conn.execute("SELECT count(*) AS n FROM evidence WHERE project_id = %s", (pid,))).fetchone())["n"])

    snapshot = await state()
    judge2 = Judge(fails=["VAT"])
    second = await run(app, pid, judge2)
    assert not judge2.calls and second["merged"] == second["linked"] == second["flagged"] == 0
    assert await state() == snapshot

    # Un-merge: the merged-away memory is current again and the copied evidence leaves the kept one.
    r = await client.patch(f"/v1/memories/{new}", json={"status": "active"})
    assert r.status_code == 200 and r.json()["status"] == "active" and r.json()["merged_into"] is None
    async with app.state.pool.connection() as conn:
        assert await (await conn.execute("SELECT count(*) AS n FROM evidence WHERE memory_id = %s", (old,))).fetchone() == before
        assert ("unmerged", "key:test") in await event_kinds(conn, new)
        assert ("unmerged", "key:test") in await event_kinds(conn, old)
    # ... and is not merged again.
    third = await run(app, pid, Judge())
    assert third["merged"] == 0
    async with app.state.pool.connection() as conn:
        assert (await item(conn, new))["status"] == "active"


@pytest.mark.asyncio
async def test_small_projects_are_skipped(client):
    from memvine_server.main import app

    pid = await pid_of(app, client.project_slug)
    async with app.state.pool.connection() as conn:
        await fillers(conn, pid, n=4)
        await add(conn, pid, "A fact.", vec(1))
        await add(conn, pid, "A fact.", vec(1, 0.99))
    judge = Judge()
    stats = await run(app, pid, judge)
    assert stats["skipped"] and stats["merged"] == 0 and not judge.calls


@pytest.mark.asyncio
async def test_scheduling_routes_and_the_worker_job(client):
    from memvine_server.main import app

    slug, pid = client.project_slug, await pid_of(app, client.project_slug)
    async with app.state.pool.connection() as conn:
        await fillers(conn, pid, n=9)
        assert pid not in await consolidation.schedule_due(conn)  # 9 active memories: too small
        await add(conn, pid, "The Northwind invoice run starts at 02:00 UTC.", vec(1), days_ago=5)
        await add(conn, pid, "Northwind's invoice run starts at 02:00 UTC every night.", vec(1, 0.95), days_ago=2)
        await conn.execute("UPDATE projects SET processing_paused = true WHERE id = %s", (pid,))
        assert pid not in await consolidation.schedule_due(conn)  # paused
    assert (await client.post(f"/v1/projects/{slug}/consolidate", headers=MGMT)).status_code == 409
    async with app.state.pool.connection() as conn:
        await conn.execute("UPDATE projects SET processing_paused = false WHERE id = %s", (pid,))
        assert pid in await consolidation.schedule_due(conn)  # never consolidated: due
        assert pid not in await consolidation.schedule_due(conn)  # one at a time
    r = await client.post(f"/v1/projects/{slug}/consolidate", headers=MGMT)
    assert r.status_code == 202 and r.json()["queued"] is False  # already queued
    # A project key may not queue one (owners and admins only).
    assert (await client.post(f"/v1/projects/{slug}/consolidate")).status_code in (401, 403)

    app.state.llm.responses = {}
    await drain(app.state.pool, app.state.llm, app.state.embedder, pid)
    runs = (await client.get(f"/v1/projects/{slug}/consolidation-runs", headers=MGMT)).json()
    assert len(runs) == 1 and runs[0]["error"] is None and runs[0]["stats"]["merged"] == 1
    project = next(p for p in (await client.get("/v1/projects", headers=MGMT)).json() if p["slug"] == slug)
    assert project["consolidated_at"] and project["last_consolidation"]["merged"] == 1
    feed = (await client.get("/v1/events", params={"kind": "merged"})).json()["events"]
    assert len(feed) == 2 and {e["actor"] for e in feed} == {"consolidation"}
    async with app.state.pool.connection() as conn:
        assert pid not in await consolidation.schedule_due(conn)  # just consolidated, nothing new
        await conn.execute("UPDATE projects SET consolidated_at = now() - interval '25 hours' WHERE id = %s", (pid,))
        assert pid in await consolidation.schedule_due(conn)  # a day later: due again
        await conn.execute("DELETE FROM jobs WHERE project_id = %s AND kind = 'consolidate' AND status = 'queued'", (pid,))
        await conn.execute("UPDATE projects SET consolidated_at = now() WHERE id = %s", (pid,))
        for i in range(consolidation.DUE_NEW_MEMORIES):
            await add(conn, pid, f"New decision {i}.", vec(100 + i), category="decision", days_ago=0)
        await conn.execute("UPDATE memory_items SET created_at = now() WHERE project_id = %s", (pid,))
        assert pid in await consolidation.schedule_due(conn)  # enough new memories: due again
    exported = (await client.post("/v1/export", headers={**MGMT, "X-Memvine-Project": slug})).json()
    assert len(exported["consolidation_runs"]) == 1


@pytest.mark.asyncio
async def test_a_failed_run_is_recorded_and_rolled_back(client):
    from memvine_server.main import app

    class Broken(Judge):
        async def complete(self, system, user, *, max_tokens=4096):
            raise RuntimeError("model fell over")

    pid = await pid_of(app, client.project_slug)
    async with app.state.pool.connection() as conn:
        await fillers(conn, pid)
        old = await add(conn, pid, "Late fees accrue daily.", vec(1))
        new = await add(conn, pid, "Late fees accrue every day.", vec(1, 0.96))
        await add(conn, pid, "Late fees are 2%.", vec(3))
        await add(conn, pid, "Late fees are now 3%.", vec(3, 0.9))
        with pytest.raises(RuntimeError):
            await consolidation.run_job(conn, Broken(), pid)
        assert (await item(conn, new))["status"] == "active"  # the rule merge before the failure rolled back
        row = await (await conn.execute("SELECT error, finished_at FROM consolidation_runs WHERE project_id = %s", (pid,))).fetchone()
        assert "model fell over" in row["error"] and row["finished_at"]
        assert (await item(conn, old))["status"] == "active"


@pytest.mark.asyncio
async def test_merges_and_unmerges_mark_profiles_dirty(client):
    from memvine_server import profiles
    from memvine_server.main import app

    pid = await pid_of(app, client.project_slug)
    async with app.state.pool.connection() as conn:
        await fillers(conn, pid)
        old = await add(conn, pid, "Dunning emails go out at 09:00 local time.", vec(1), category="decision")
        new = await add(conn, pid, "Dunning emails are sent at 09:00 local time.", vec(1, 0.96), category="decision")
        through = (await (await conn.execute("SELECT max(id) AS id FROM memory_events")).fetchone())["id"] or 0
        await conn.execute("INSERT INTO profiles (project_id, subject_kind, subject, lines, dirty, built_at, built_from_event_id) "
                           "VALUES (%s, 'project', '', %s, false, now(), %s)",
                           (pid, json.dumps([{"text": "Dunning at 09:00.", "memory_ids": [str(new)]}]), through))
    await run(app, pid, Judge())
    async with app.state.pool.connection() as conn:
        await profiles.sweep(conn, force=True)
        dirty = await (await conn.execute("SELECT dirty FROM profiles WHERE project_id = %s", (pid,))).fetchone()
        assert dirty["dirty"]  # it cited the merged-away memory
        assert "unmerged" in profiles.RELEVANT_KINDS and "merged" in profiles.RELEVANT_KINDS
        assert (await item(conn, new))["merged_into"] == old
