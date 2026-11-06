"""The briefing carries what the question needs and nothing twice: items within a margin of the best
one (contradiction and supersede partners kept with them), thread pages only when they are the
caller's branch, named, or as relevant as the best item, and source excerpts, for a caller that asks
for them with include.passages, only as evidence of what was kept. A recreation of the measured case: "Which port does the muse production database use?"
used to get both thread pages, the unrelated SvelteKit decision, every excerpt and the deploy rule
three times.

The fake embedder is bag-of-words, so the scenario's wording sets the similarities: the port
question shares four content words with the port fact (relevance about 0.78 with the keyword bonus)
and one ("muse") with the SvelteKit decision and the deploy fact (about 0.22), so the default
margin separates them as it separates BGE-small's 0.82-0.92 from 0.60-0.70 on the real data.
The two thread pages share two words with the question (about 0.54 and 0.48): enough to pass the
thread search and the relevance gate, not enough to be within the margin of the best item."""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from conftest import needs_db

from memvine_server.passages import index_episode
from memvine_server.recall import RELEVANCE_MARGIN, about_outdated, trim_to_margin, unsaid
from memvine_server.threads import item_words

pytestmark = needs_db

QUESTION = "Which port does the muse production database use?"
PORT = "The muse production database listens on port 5433."
OLD_PORT = "The muse production database listens on port 5432."
SVELTE = "The muse frontend uses SvelteKit instead of Next.js."
RULE = "Always run the migrations before a deploy."
DEPLOY = "Muse deploys go through GitHub Actions."

EPISODES = {
    "ep-port-old": ("2026-10-10T09:00:00+00:00", "## user · 2026-10-10T09:00\nThe muse production database listens on port 5432."),
    "ep-svelte": ("2026-10-15T09:00:00+00:00",
                  "## user · 2026-10-15T09:00\nFor the frontend we chose SvelteKit instead of Next.js because the team knows Svelte."),
    "ep-deploy": ("2026-10-18T09:00:00+00:00",
                  "## user · 2026-10-18T09:00\nAlways run the migrations before a deploy. Deploys go through GitHub Actions. "
                  "Next we add a smoke test after each deploy job."),
    "ep-port-new": ("2026-10-20T09:00:00+00:00",
                    "## user · 2026-10-20T09:00\nWe moved the muse production database to port 5433 so it stops clashing with "
                    "the local Postgres."),
}


def _at(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _item(text, date, episode, quote):
    return {"text": text, "date": date, "evidence": [{"episode_id": episode, "quote": quote}]}


async def setup_scenario(client) -> dict:
    """Four memories across two threads plus a superseded value, with their sources and pages."""
    from memvine_server.main import app

    embedder = app.state.embedder
    async with app.state.pool.connection() as conn:
        pid = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
        for eid, (at, content) in EPISODES.items():
            await conn.execute("INSERT INTO episodes (project_id, id, content, content_hash, occurred_at, label) "
                               "VALUES (%s, %s, %s, %s, %s, %s)", (pid, eid, content, eid, _at(at), "Claude Code · muse"))
            await index_episode(conn, embedder, pid, eid)

        async def thread(key, title, summary, sections, aliases=()):
            [vec] = await embedder.embed([f"{title}. {summary}"])
            return (await (await conn.execute(
                "INSERT INTO threads (project_id, kind, key, title, status, summary, sections, aliases, embedding, first_seen_at, "
                "last_activity_at) VALUES (%s, 'topic', %s, %s, 'open', %s, %s, %s, %s, now(), now()) RETURNING id",
                (pid, key, title, summary, json.dumps(sections), list(aliases), vec))).fetchone())["id"]

        frontend = await thread("topic:muse-frontend", "Muse production frontend", "Muse production frontend uses SvelteKit.", [
            {"label": "Decisions", "items": [_item("Use SvelteKit instead of Next.js for the muse frontend", "2026-10-15",
                                                   "ep-svelte", "we chose SvelteKit instead of Next.js")]}])
        deploy = await thread("topic:muse-deploy", "Muse production deploy", "How muse production deploys run.", [
            {"label": "Decisions", "items": [
                _item("Always run the migrations before a deploy", "2026-10-18", "ep-deploy", "Always run the migrations before a deploy"),
                _item("Deploys go through GitHub Actions", "2026-10-18", "ep-deploy", "Deploys go through GitHub Actions")]},
            {"label": "Next steps", "items": [_item("Add a smoke test after each deploy job", "2026-10-18", "ep-deploy",
                                                    "add a smoke test after each deploy job")]}],
            aliases=["branch:deploy-fixes"])

        async def memory(statement, episode, quote, *, category, status="active", standing=False, thread_id=None, kind="semantic"):
            [vec] = await embedder.embed([statement])
            at = _at(EPISODES[episode][0])
            mid = (await (await conn.execute(
                """INSERT INTO memory_items (project_id, kind, statement, valid_from, recorded_at, status, assertion, confidence,
                                             embedding, category, standing, thread_id, scope)
                   VALUES (%s, %s, %s, %s, %s, %s, 'stated', 'high', %s, %s, %s, %s, 'project') RETURNING id""",
                (pid, kind, statement, at, at, status, vec, category, standing, thread_id))).fetchone())["id"]
            await conn.execute("INSERT INTO evidence (memory_id, project_id, episode_id, quote) VALUES (%s, %s, %s, %s)",
                               (mid, pid, episode, quote))
            return mid

        old = await memory(OLD_PORT, "ep-port-old", "The muse production database listens on port 5432", category="fact",
                           status="superseded")
        new = await memory(PORT, "ep-port-new", "moved the muse production database to port 5433", category="fact")
        await conn.execute("UPDATE memory_items SET superseded_by = %s, valid_to = %s WHERE id = %s",
                           (new, _at(EPISODES["ep-port-new"][0]), old))
        await conn.execute("UPDATE memory_items SET supersedes = %s WHERE id = %s", (old, new))
        svelte = await memory(SVELTE, "ep-svelte", "we chose SvelteKit instead of Next.js", category="decision", thread_id=frontend)
        rule = await memory(RULE, "ep-deploy", "Always run the migrations before a deploy", category="rule", standing=True,
                            thread_id=deploy, kind="procedural")
        fact = await memory(DEPLOY, "ep-deploy", "Deploys go through GitHub Actions", category="fact", thread_id=deploy)
    return {"pid": pid, "old": old, "new": new, "svelte": svelte, "rule": rule, "deploy": fact}


async def recall(client, **body) -> dict:
    r = await client.post("/v1/recall", json={"budget": 2000, "include": {"passages": True}, **body})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_the_port_question_gets_the_port_and_nothing_unrelated(client):
    await setup_scenario(client)
    r = await recall(client, query=QUESTION)
    briefing = r["briefing"]
    print(briefing)  # the after-briefing, for the record (pytest -s)
    assert not r["abstained"]
    assert "listens on port 5433 (was: The muse production database listens on port 5432)" in briefing
    assert [i["memory"]["statement"] for i in r["items"]] == [PORT]
    assert "SvelteKit" not in briefing, "the unrelated decision, its page and its excerpt are gone"
    assert r["threads"] == [] and "###" not in briefing
    assert "GitHub Actions" not in briefing
    assert briefing.count("Always run the migrations before a deploy") == 1, "the rule is said once, in the standing section"
    excerpts = [p["content"] for p in r["passages"]]
    assert len(excerpts) == 1 and "port 5433" in excerpts[0], "only the evidence of the kept fact"
    assert "port 5432" not in "".join(excerpts), "no excerpt of the superseded value"
    assert r["tokens"] < 200


@pytest.mark.asyncio
async def test_a_plain_recall_carries_memory_and_no_excerpts(client):
    """Agents get memory, not transcript: excerpts only come to a caller that asks for them."""
    await setup_scenario(client)
    r = (await client.post("/v1/recall", json={"query": QUESTION, "budget": 2000})).json()
    assert "5433" in r["briefing"], "the memory itself is served"
    assert r["passages"] == []
    assert "source excerpts" not in r["briefing"]


@pytest.mark.asyncio
async def test_a_broad_question_still_gets_breadth(client):
    await setup_scenario(client)
    r = await recall(client, query="Give me an overview of everything about the muse production setup")
    statements = {i["memory"]["statement"] for i in r["items"]}
    assert {PORT, SVELTE, DEPLOY} <= statements
    assert len(r["passages"]) >= 2


@pytest.mark.asyncio
async def test_a_history_question_gets_the_superseded_value_and_its_excerpt(client):
    await setup_scenario(client)
    r = await recall(client, query="Which port was the muse production database on before?")
    briefing = r["briefing"]
    statuses = {i["memory"]["statement"]: i["memory"]["status"] for i in r["items"]}
    assert statuses == {PORT: "active", OLD_PORT: "superseded"}
    assert "(was: The muse production database listens on port 5432)" in briefing
    assert any("port 5432" in p["content"] for p in r["passages"]) and any("port 5433" in p["content"] for p in r["passages"])
    assert "SvelteKit" not in briefing


@pytest.mark.asyncio
async def test_the_branch_thread_comes_first_without_repeating_the_rule(client):
    await setup_scenario(client)
    r = await recall(client, query=QUESTION, branch="deploy-fixes")
    assert [t["key"] for t in r["threads"]] == ["topic:muse-deploy"]
    briefing = r["briefing"]
    assert "### Muse production deploy" in briefing
    assert briefing.index("### Muse production deploy") < briefing.index("listens on port 5433")
    assert briefing.count("Always run the migrations before a deploy") == 1, "not again inside the page"
    assert "Add a smoke test after each deploy job" in briefing, "what only the page says stays"


@pytest.mark.asyncio
async def test_a_contradiction_partner_is_kept_with_the_item_it_contradicts(client):
    from memvine_server.main import app

    ids = await setup_scenario(client)
    other = "The replica listens on port 6000."
    async with app.state.pool.connection() as conn:
        [vec] = await app.state.embedder.embed([other])
        oid = (await (await conn.execute(
            """INSERT INTO memory_items (project_id, kind, statement, valid_from, recorded_at, status, assertion, confidence,
                                         embedding, category, scope, conflicts_with)
               VALUES (%s, 'semantic', %s, now(), now(), 'active', 'stated', 'high', %s, 'fact', 'project', %s) RETURNING id""",
            (ids["pid"], other, vec, [ids["new"]]))).fetchone())["id"]
        await conn.execute("UPDATE memory_items SET conflicts_with = %s WHERE id = %s", ([oid], ids["new"]))
    r = await recall(client, query=QUESTION)
    statements = [i["memory"]["statement"] for i in r["items"]]
    assert PORT in statements and other in statements, "a pair is kept together though one side is far below the margin"
    assert "Conflicting statements on record" in r["briefing"]
    assert SVELTE not in statements


def test_margin_keeps_the_top_item_and_pairs():
    a, b, c, d = "a", "b", "c", "d"
    rows = {a: {"conflicts_with": [], "supersedes": None, "superseded_by": None},
            b: {"conflicts_with": [d], "supersedes": None, "superseded_by": None},
            c: {"conflicts_with": [], "supersedes": None, "superseded_by": None},
            d: {"conflicts_with": [b], "supersedes": None, "superseded_by": None}}
    rel = {a: 0.5, b: 0.9, c: 0.85, d: 0.3}
    ranked = [(4.0, a), (3.0, b), (2.0, c), (1.0, d)]
    kept = [m for _, m in trim_to_margin(ranked, rows, rel.get)]
    assert kept == [a, b, c, d], "a ranks first so it stays; d contradicts b so it stays"
    rel[a] = 0.2
    rows[b]["conflicts_with"], rows[d]["conflicts_with"] = [], []
    assert [m for _, m in trim_to_margin(ranked, rows, rel.get)] == [a, b, c]
    assert 0.05 <= RELEVANCE_MARGIN <= 0.2


def test_a_page_already_said_by_the_briefing_has_nothing_left():
    page = {"sections": [{"label": "Decisions", "items": [
        _item("Always run the migrations before a deploy", None, "ep", "Always run the migrations before a deploy"),
        _item("Deploys are run by the CI workflow", None, "ep", "Deploys go through GitHub Actions")]}]}
    said_words = [item_words(RULE)]
    sections, knowledge = unsaid(page, [], said_words, {("ep", "deploys go through github actions")})
    assert sections == [] and knowledge == [], "one by its words, one by its evidence"
    sections, _ = unsaid(page, [], said_words, set())
    assert [i["text"] for s in sections for i in s["items"]] == ["Deploys are run by the CI workflow"]
    sections, _ = unsaid(page, [], [], set(), {"ep"})
    assert sections == [], "a line citing a said episode is said"


# The real-model shape after a port update: a fact superseded by a newer one, and two pages written
# from single statements, one stating the old value (citing the old fact's episode with a different
# quote) and one restating the new value (citing the new fact's episode with a different quote).
PG_OLD = "The muse production database is Postgres 16 on port 5544."
PG_NEW = "The muse production database runs on port 6432 behind PgBouncer, replacing port 5544."
PG_EPISODES = {
    "ep-5544": ("2026-10-20T09:00:00+00:00", "## user · 2026-10-20T09:00\nFor muse: production database is Postgres 16, port 5544."),
    "ep-6432": ("2026-10-25T09:00:00+00:00", "## user · 2026-10-25T09:00\nUpdate for muse: the production database moved behind "
                                             "PgBouncer, connect on port 6432 now, not 5544."),
}


async def setup_port_update(client) -> None:
    from memvine_server.main import app

    embedder = app.state.embedder
    async with app.state.pool.connection() as conn:
        pid = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
        for eid, (at, content) in PG_EPISODES.items():
            await conn.execute("INSERT INTO episodes (project_id, id, content, content_hash, occurred_at, label) "
                               "VALUES (%s, %s, %s, %s, %s, %s)", (pid, eid, content, eid, _at(at), "Claude Code · muse"))
            await index_episode(conn, embedder, pid, eid)

        async def page(key, title, status, summary, label, text, episode, quote):
            [vec] = await embedder.embed([f"{title}. {summary}"])
            await conn.execute(
                "INSERT INTO threads (project_id, kind, key, title, status, summary, sections, embedding, first_seen_at, "
                "last_activity_at) VALUES (%s, 'topic', %s, %s, %s, %s, %s, %s, now(), now())",
                (pid, key, title, status, summary, json.dumps([{"label": label, "items": [
                    _item(text, PG_EPISODES[episode][0][:10], episode, quote)]}]), vec))

        await page("topic:muse-production-database", "Muse production database (Postgres 16, port 5544)", "open",
                   "The muse production database is Postgres 16 running on port 5544. This is a configuration fact recorded "
                   "for the muse web app (SvelteKit frontend, Postgres).",
                   "State", "Production database is Postgres 16 on port 5544.", "ep-5544", "production database is Postgres 16")
        await page("topic:muse-prod-db-pgbouncer", "Muse production DB migration to PgBouncer (port 6432)", "done",
                   "The muse production database was migrated and now runs on port 6432 behind PgBouncer, replacing the old "
                   "port 5544.", "Decisions", "Muse prod DB connections go through PgBouncer on port 6432, not 5544.",
                   "ep-6432", "connect on port 6432 now")

        async def memory(statement, episode, quote, status="active"):
            [vec] = await embedder.embed([statement])
            at = _at(PG_EPISODES[episode][0])
            mid = (await (await conn.execute(
                """INSERT INTO memory_items (project_id, kind, statement, valid_from, recorded_at, status, assertion, confidence,
                                             embedding, category, scope)
                   VALUES (%s, 'semantic', %s, %s, %s, %s, 'stated', 'high', %s, 'fact', 'project') RETURNING id""",
                (pid, statement, at, at, status, vec))).fetchone())["id"]
            await conn.execute("INSERT INTO evidence (memory_id, project_id, episode_id, quote) VALUES (%s, %s, %s, %s)",
                               (mid, pid, episode, quote))
            return mid

        old = await memory(PG_OLD, "ep-5544", "production database is Postgres 16, port 5544", status="superseded")
        new = await memory(PG_NEW, "ep-6432", "the production database moved behind PgBouncer")
        await conn.execute("UPDATE memory_items SET superseded_by = %s, valid_to = %s WHERE id = %s",
                           (new, _at(PG_EPISODES["ep-6432"][0]), old))
        await conn.execute("UPDATE memory_items SET supersedes = %s WHERE id = %s", (old, new))


@pytest.mark.asyncio
async def test_pages_stating_an_outdated_or_already_said_value_are_not_served(client):
    from memvine_server.main import app
    from memvine_server.threads import relevant_threads

    await setup_port_update(client)
    async with app.state.pool.connection() as conn:
        pid = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
        [vec] = await app.state.embedder.embed([QUESTION])
        found = await relevant_threads(conn, pid, QUESTION, vec)
    assert len(found) == 2, "both pages are found"
    [fact_vec] = await app.state.embedder.embed([PG_NEW])
    best = sum(a * b for a, b in zip(vec, fact_vec, strict=True)) + 0.05
    assert all(t["similarity"] >= best - RELEVANCE_MARGIN for t in found), \
        "both are within the margin, so what keeps them out is that they say nothing current and unsaid"

    r = await recall(client, query=QUESTION)
    briefing = r["briefing"]
    print(briefing)
    assert r["threads"] == [] and "###" not in briefing
    assert "port 6432 behind PgBouncer" in briefing
    assert "(was: The muse production database is Postgres 16 on port 5544)" in briefing
    assert [p["episode_id"] for p in r["passages"]] == ["ep-6432"]
    assert "configuration fact" not in briefing


def test_a_page_about_a_replaced_value_is_recognized():
    outdated = [{"statement": PG_OLD, "replacement": PG_NEW}]
    assert about_outdated({"title": "Muse production database (Postgres 16, port 5544)", "summary": ""}, outdated)
    assert not about_outdated({"title": "Muse production DB migration to PgBouncer (port 6432)",
                               "summary": "Now on 6432, replacing 5544."}, outdated)
    assert not about_outdated({"title": "Release 4.2.0", "summary": "Shipping the export fixes."}, outdated)
