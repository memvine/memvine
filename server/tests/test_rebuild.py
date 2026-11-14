"""Rebuilding a project's memory (rebuild.py, `memvine-server rebuild-memory`): every stored episode is
queued for synthesis again, oldest first, and the worker replaces what each one produced before. What
older extraction rules kept is gone afterwards, nothing is kept twice, and what a person said, kept,
forgot or corrected stays as they left it."""
from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from conftest import TEST_DSN, needs_db

from memvine_server.extraction.llm import FakeLLM
from memvine_server.extraction.prompts import DIGEST_SYSTEM, ROLLUP_SYSTEM
from memvine_server.rebuild import rebuild_memory
from memvine_server.worker import drain

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}

# A conversation about the invented Northwind billing service. The fake model answers by the time in
# an excerpt's first line, which no statement repeats.
FEES = ("## user · 2024-03-12T09:00:00Z\nThe office was cold during the invoice run today. Northwind charges "
        "a late fee of 2 percent once an invoice is 30 days overdue.\n\n## assistant · 2024-03-12T09:01:00Z\nNoted.")
ROUNDING = ("## user · 2024-03-14T09:00:00Z\nNorthwind rounds every invoice total to the nearest cent, half up."
            "\n\n## assistant · 2024-03-14T09:01:00Z\nNoted.")
ROUNDING_AGAIN = ("## user · 2024-03-20T09:00:00Z\nAs I said before, Northwind rounds every invoice total to the "
                  "nearest cent, half up.\n\n## assistant · 2024-03-20T09:01:00Z\nYes.")

COLD = "The office was cold during the invoice run on 12 Mar 2024."
LATE_FEE = "Northwind charges a late fee of 2 percent once an invoice is 30 days overdue."
HALF_UP = "Northwind rounds every invoice total to the nearest cent, half up."


def detail(statement, quote, category="fact"):
    return {"statement": statement, "category": category, "subjects": ["northwind billing"], "confidence": "high",
            "standing": False, "quote": quote}


class Model(FakeLLM):
    """The fake model, with a digest for an excerpt (its canned details are JSON, which is no digest)."""

    async def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        if system in (DIGEST_SYSTEM, ROLLUP_SYSTEM):
            self.calls.append((system, user))
            return "The user described how the Northwind billing service charges late fees and rounds invoice totals."
        return await super().complete(system, user, max_tokens=max_tokens)


ROUNDING_RULES = {"2024-03-14T09:00": json.dumps([detail(HALF_UP, "rounds every invoice total")]),
                  "2024-03-20T09:00": json.dumps([detail(HALF_UP, "rounds every invoice total")])}
# The older rules kept a sentence about the office; the current ones keep the late fee instead.
OLDER_RULES = {"2024-03-12T09:00": json.dumps([detail(COLD, "The office was cold", "event")]), **ROUNDING_RULES}
CURRENT_RULES = {"2024-03-12T09:00": json.dumps([detail(LATE_FEE, "a late fee of 2 percent")]), **ROUNDING_RULES}


async def a_conversation(client, app):
    """A conversation-mode project with three excerpts synthesized under the older rules."""
    slug = client.project_slug
    assert (await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"memory_mode": "conversation"})).status_code == 200
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]
    llm = Model(responses=dict(OLDER_RULES))
    llm.fast = llm
    # Sent newest first: a rebuild goes by when an excerpt was said, not by when it arrived.
    items = [{"id": "rounding-again", "content": ROUNDING_AGAIN, "timestamp": "2024-03-20T09:00:00Z"},
             {"id": "rounding", "content": ROUNDING, "timestamp": "2024-03-14T09:00:00Z"},
             {"id": "fees", "content": FEES, "timestamp": "2024-03-12T09:00:00Z"}]
    assert (await client.post("/v1/ingest", json={"items": items})).status_code == 202
    await drain(app.state.pool, llm, app.state.embedder, project_id)
    assert all(j["status"] == "done" for j in (await client.get("/v1/jobs")).json()["jobs"])
    return project_id, llm


async def snapshot(conn, project_id):
    """Everything a rebuild may touch: memory items with their evidence, digests and jobs."""
    memories = await (await conn.execute(
        "SELECT m.id, m.statement, m.status, array_agg(v.episode_id ORDER BY v.episode_id) AS sources FROM memory_items m "
        "JOIN evidence v ON v.memory_id = m.id WHERE m.project_id = %s GROUP BY m.id ORDER BY m.statement", (project_id,))).fetchall()
    digests = await (await conn.execute(
        "SELECT id, episode_id FROM digests WHERE project_id = %s AND level = 1 ORDER BY episode_id", (project_id,))).fetchall()
    jobs = await (await conn.execute(
        "SELECT id, episode_id, status, background FROM jobs WHERE project_id = %s AND kind = 'extract' ORDER BY id",
        (project_id,))).fetchall()
    return memories, digests, jobs


@pytest.mark.asyncio
async def test_a_rebuild_replaces_what_older_rules_kept(client):
    from memvine_server.main import app

    project_id, llm = await a_conversation(client, app)
    async with app.state.pool.connection() as conn:
        memories, digests, _ = await snapshot(conn, project_id)
    assert [(m["statement"], m["sources"]) for m in memories] == [(HALF_UP, ["rounding", "rounding-again"]), (COLD, ["fees"])]
    assert [d["episode_id"] for d in digests] == ["fees", "rounding", "rounding-again"]

    llm.responses = dict(CURRENT_RULES)
    async with app.state.pool.connection() as conn:
        result = await rebuild_memory(conn, project_id)
    assert (result.episodes, result.queued, result.waiting, result.held, result.expired) == (3, 3, 0, 0, 0)
    assert result.memories == {"active": 2}
    async with app.state.pool.connection() as conn:
        memories, _, jobs = await snapshot(conn, project_id)
        queued = [j for j in jobs if j["status"] == "queued"]
        # Oldest excerpt first, behind anything live; nothing has been deleted yet.
        assert [j["episode_id"] for j in queued] == ["fees", "rounding", "rounding-again"] and all(j["background"] for j in queued)
        assert [m["statement"] for m in memories] == [HALF_UP, COLD]

    assert await drain(app.state.pool, llm, app.state.embedder, project_id) == 3
    async with app.state.pool.connection() as conn:
        memories, digests, jobs = await snapshot(conn, project_id)
    assert all(j["status"] == "done" for j in jobs)
    # The office sentence is gone and the late fee is there; the rounding rule, said in two excerpts, is
    # one item backed by both, as before.
    assert [(m["statement"], m["status"], m["sources"]) for m in memories] == [
        (LATE_FEE, "active", ["fees"]), (HALF_UP, "active", ["rounding", "rounding-again"])]
    # One digest per excerpt: each was replaced, not added to.
    assert [d["episode_id"] for d in digests] == ["fees", "rounding", "rounding-again"]


@pytest.mark.asyncio
async def test_a_dry_run_counts_and_changes_nothing(client):
    from memvine_server.main import app

    project_id, _ = await a_conversation(client, app)
    async with app.state.pool.connection() as conn:
        before = await snapshot(conn, project_id)
        result = await rebuild_memory(conn, project_id, dry_run=True)
    assert (result.episodes, result.queued, result.waiting, result.held, result.expired) == (3, 3, 0, 0, 0)
    assert result.memories == {"active": 2}
    async with app.state.pool.connection() as conn:
        assert await snapshot(conn, project_id) == before


@pytest.mark.asyncio
async def test_a_second_rebuild_before_the_worker_ran_queues_nothing(client):
    from memvine_server.main import app

    project_id, llm = await a_conversation(client, app)
    async with app.state.pool.connection() as conn:
        assert (await rebuild_memory(conn, project_id)).queued == 3
    async with app.state.pool.connection() as conn:
        again = await rebuild_memory(conn, project_id)
        assert (again.queued, again.waiting) == (0, 3)
        assert (await rebuild_memory(conn, project_id, dry_run=True)).queued == 0
        _, _, jobs = await snapshot(conn, project_id)
    assert [j["episode_id"] for j in jobs if j["status"] == "queued"] == ["fees", "rounding", "rounding-again"]
    # Once the worker has been through them, the episodes can be rebuilt again.
    assert await drain(app.state.pool, llm, app.state.embedder, project_id) == 3
    async with app.state.pool.connection() as conn:
        assert (await rebuild_memory(conn, project_id, dry_run=True)).queued == 3


# A coding project: what a person said, kept, forgot or corrected.
DUNNING = "User: for Northwind, dunning emails only ever go out on weekdays, remember that. Assistant: Understood."
CURRENCY = "User: we settled it, Northwind invoices are issued in euros only. Assistant: Noted."
TAX = "User: one more thing, the Northwind tax codes come from the finance sheet, not from the code. Assistant: Noted."
RETRY = "User: by the way, Northwind retries a failed card payment three times before giving up. Assistant: Noted."
STATEMENTS = "User: for the record, Northwind emails statements on the first of each month. Assistant: Noted."

DUNNING_RULE = "Northwind dunning emails are sent on weekdays only."
EUROS = "Northwind invoices are issued in euros only."
TAX_CODES = "Northwind tax codes are maintained in the finance sheet, not in the code."
RETRIES = "Northwind retries a failed card payment three times."
MONTHLY = "Northwind emails customer statements on the first of each month."
OWN = "Refunds over 500 euros at Northwind need a second approver."
CORRECTION = "Northwind invoices are issued in euros and pounds."


def item(statement, quote, category="rule"):
    return {"statement": statement, "kind": "procedural" if category == "rule" else "semantic", "category": category,
            "subjects": ["northwind billing"], "assertion": "stated", "confidence": "high", "quote": quote}


@pytest.mark.asyncio
async def test_what_a_person_said_kept_forgot_or_corrected_is_left_alone(client):
    from memvine_server.main import app

    llm = app.state.llm
    llm.responses = {
        "remember that": json.dumps([item(DUNNING_RULE, "dunning emails only ever go out on weekdays")]),
        "we settled it": json.dumps([item(EUROS, "invoices are issued in euros only", "decision")]),
        "one more thing": json.dumps([item(TAX_CODES, "tax codes come from the finance sheet", "fact")]),
        "by the way": json.dumps([item(RETRIES, "retries a failed card payment three times", "fact")]),
        "for the record": json.dumps([item(MONTHLY, "emails statements on the first of each month", "fact")]),
        "NEW (": json.dumps({"match": None, "relation": "different", "reason": "distinct"}),
    }
    llm.default = "[]"
    episodes = [{"id": f"e{i}", "content": content, "timestamp": f"2026-10-2{i}T10:00:00Z", "label": "Claude Code · northwind"}
                for i, content in enumerate([DUNNING, CURRENCY, TAX, RETRY, STATEMENTS], start=1)]
    assert (await client.post("/v1/ingest", json={"items": episodes})).status_code == 202
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, llm, app.state.embedder, project_id)
    made = {m["statement"]: m["id"] for m in (await client.get("/v1/memories")).json()}
    assert set(made) == {DUNNING_RULE, EUROS, TAX_CODES, RETRIES, MONTHLY}

    # A person forgets the dunning rule, corrects the currency, keeps the retries, says something of
    # their own, and retention drops the text of the tax excerpt.
    assert (await client.patch(f"/v1/memories/{made[DUNNING_RULE]}", json={"status": "retired"})).json()["retired_reason"] == "forgotten"
    assert (await client.post("/v1/memories", json={"statement": CORRECTION, "supersedes": made[EUROS]})).status_code == 201
    assert (await client.patch(f"/v1/memories/{made[RETRIES]}", json={"confirm": True})).json()["review"] == "kept"
    assert (await client.post("/v1/memories", json={"statement": OWN, "category": "rule"})).status_code == 201
    async with app.state.pool.connection() as conn:
        await conn.execute("UPDATE episodes SET content = '[expired by retention]', expired_at = now() WHERE project_id = %s AND id = 'e3'",
                           (project_id,))

    async def memories(conn):
        return await (await conn.execute(
            "SELECT id, statement, status, origin, review, forgotten_at IS NOT NULL AS forgotten FROM memory_items "
            "WHERE project_id = %s AND statement <> %s ORDER BY statement", (project_id, MONTHLY))).fetchall()

    async with app.state.pool.connection() as conn:
        before = await memories(conn)
        result = await rebuild_memory(conn, project_id)
        # Five excerpts and the two statements the person made: only the untouched excerpt is rebuilt.
        assert (result.episodes, result.queued, result.held, result.expired, result.waiting) == (7, 1, 5, 1, 0)
        assert result.memories == {"active": 5, "retired": 1, "superseded": 1}
        queued = await (await conn.execute(
            "SELECT episode_id FROM jobs WHERE project_id = %s AND status = 'queued'", (project_id,))).fetchall()
        assert [j["episode_id"] for j in queued] == ["e5"]

    assert await drain(app.state.pool, llm, app.state.embedder, project_id) == 1
    async with app.state.pool.connection() as conn:
        assert await memories(conn) == before
    assert {(m["statement"], m["forgotten"]) for m in before if m["status"] == "retired"} == {(DUNNING_RULE, True)}
    active = sorted(m["statement"] for m in (await client.get("/v1/memories", params={"status": "active"})).json())
    assert active == sorted([CORRECTION, TAX_CODES, RETRIES, MONTHLY, OWN])
    assert len((await client.get("/v1/feedback-cases", headers={**MGMT, "X-Memvine-Project": client.project_slug})).json()["cases"]) == 2


def test_rebuild_memory_cli():
    from typer.testing import CliRunner

    from memvine_server.cli import app as cli

    slug = f"t-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        # The schema exists: an async test (or the app) applied it.
        pid = conn.execute("INSERT INTO projects (slug, name) VALUES (%s, %s) RETURNING id", (slug, slug)).fetchone()[0]
        for day in (2, 1):
            conn.execute("INSERT INTO episodes (project_id, id, content, content_hash, occurred_at) VALUES (%s, %s, %s, %s, %s)",
                         (pid, f"day{day}", "User: Northwind closes its books on the last working day.", "x", f"2026-10-0{day}T10:00:00Z"))
    try:
        result = CliRunner().invoke(cli, ["rebuild-memory", slug, "--dry-run"])
        assert result.exit_code == 0, result.output
        assert f"{slug}: 2 episodes; 0 memory items now" in result.output and "would queue 2 episodes" in result.output
        assert "nothing was changed" in result.output
        with psycopg.connect(TEST_DSN) as conn:
            assert conn.execute("SELECT count(*) FROM jobs WHERE project_id = %s", (pid,)).fetchone()[0] == 0

        result = CliRunner().invoke(cli, ["rebuild-memory", slug])
        assert result.exit_code == 0, result.output
        assert "queued 2 episodes" in result.output and "memvine-server worker" in result.output
        with psycopg.connect(TEST_DSN) as conn:
            jobs = conn.execute("SELECT episode_id, kind, status FROM jobs WHERE project_id = %s ORDER BY id", (pid,)).fetchall()
        assert jobs == [("day1", "extract", "queued"), ("day2", "extract", "queued")]

        result = CliRunner().invoke(cli, ["rebuild-memory", slug])
        assert "queued 0 episodes" in result.output and "2 already queued or running" in result.output
        assert CliRunner().invoke(cli, ["rebuild-memory", "no-such-project"]).exit_code != 0
    finally:
        with psycopg.connect(TEST_DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM projects WHERE slug = %s", (slug,))

