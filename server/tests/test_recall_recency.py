"""Recency in recall's ranking: among comparably relevant memories, a fact or gotcha said recently ranks
above one last said long ago; decisions, rules and preferences never decay; a project's half-life of 0
turns it off; a history question gets none; the relevance gate and the trim ignore it. A memory a
person kept ranks a little higher than an equal one nobody looked at.

The fake embedder is bag-of-words, so wording sets the similarities: OLD_FACT says "invoice" and
"region" twice, so it is a little more relevant than NEW_FACT (cosine 0.775 against 0.73, well within
RELEVANCE_MARGIN) and first in both the vector and the keyword list: without recency it ranks first."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import needs_db

from memvine_server.extraction.extract import Candidate
from memvine_server.recall import RECENCY_FLOOR, decays, recency_factor
from memvine_server.store import add_evidence, insert_item

pytestmark = needs_db

MGMT = {"Authorization": "Bearer mv_mk_test"}
QUESTION = "Which region does the northwind invoice export run in?"
OLD_FACT = "The northwind invoice export runs in the east region, our invoice region."
NEW_FACT = "The northwind invoice export runs in the west region."
UNRELATED = "Weather forecast for Paris tomorrow?"


async def add(client, statement: str, *, days_ago: float, category: str = "fact", kind: str = "semantic",
              review: str = "new", standing: bool = False) -> str:
    """One memory recorded `days_ago`, with its source episode at that moment."""
    from memvine_server.main import app

    at = datetime.now(UTC) - timedelta(days=days_ago)
    async with app.state.pool.connection() as conn:
        pid = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
        episode = f"ep-{abs(hash(statement))}"
        await conn.execute("INSERT INTO episodes (project_id, id, content, content_hash, occurred_at) VALUES (%s, %s, %s, %s, %s)",
                           (pid, episode, statement, episode, at))
        [vec] = await app.state.embedder.embed([statement])
        cand = Candidate(statement=statement, kind=kind, subjects=["northwind"], assertion="stated", confidence="high",
                         valid_from=None, quote=statement, category=category, standing=standing)
        return str(await insert_item(conn, pid, cand, vec, recorded_at=at, episode_id=episode, review=review))


async def restate(client, memory_id: str, *, days_ago: float) -> None:
    """A later conversation said it again: new evidence from a recent episode."""
    from memvine_server.main import app

    async with app.state.pool.connection() as conn:
        pid = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
        await conn.execute("INSERT INTO episodes (project_id, id, content, content_hash, occurred_at) VALUES (%s, 'ep-again', 'again', 'again', %s)",
                           (pid, datetime.now(UTC) - timedelta(days=days_ago)))
        await add_evidence(conn, pid, memory_id, "ep-again", "again")


async def recall(client, query: str = QUESTION, **extra) -> dict:
    r = await client.post("/v1/recall", json={"query": query, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def order(r: dict) -> list[str]:
    return [i["memory"]["statement"] for i in r["items"]]


def scores(r: dict) -> dict[str, float]:
    return {i["memory"]["statement"]: i["score"] for i in r["items"]}


async def half_life(client, days: float | None) -> None:
    r = await client.patch(f"/v1/projects/{client.project_slug}", headers=MGMT, json={"recall_half_life_days": days})
    assert r.status_code == 200, r.text
    assert r.json()["recall_half_life_days"] == days


def test_factor_and_what_decays():
    assert recency_factor(0, 90) == pytest.approx(1.0)
    assert recency_factor(90, 90) == pytest.approx(RECENCY_FLOOR + (1 - RECENCY_FLOOR) / 2)
    assert recency_factor(10_000, 90) == pytest.approx(RECENCY_FLOOR, abs=1e-6)
    assert recency_factor(500, 0) == 1.0
    assert decays({"category": "fact", "kind": "semantic"}) and decays({"category": "gotcha", "kind": "procedural"})
    assert decays({"category": None, "kind": "episodic"}) and decays({"category": None, "kind": "semantic"})
    for category in ("decision", "rule", "preference"):
        assert not decays({"category": category, "kind": "semantic"})
    assert not decays({"category": None, "kind": "procedural"})
    assert not decays({"category": "fact", "kind": "semantic", "standing": True})


@pytest.mark.asyncio
async def test_equally_relevant_facts_newer_first(client):
    await add(client, "The northwind invoice export runs in the east region.", days_ago=400)
    await add(client, "The northwind invoice export runs in the west region.", days_ago=2)
    r = await recall(client)
    assert order(r) == ["The northwind invoice export runs in the west region.",
                        "The northwind invoice export runs in the east region."]


@pytest.mark.asyncio
async def test_newer_fact_outranks_a_slightly_more_relevant_old_one_and_half_life_zero_disables(client):
    await add(client, OLD_FACT, days_ago=400)
    await add(client, NEW_FACT, days_ago=2)
    r = await recall(client)
    assert order(r) == [NEW_FACT, OLD_FACT], "both are kept (within the margin); the recent one leads"

    await half_life(client, 0)
    off = await recall(client)
    assert order(off) == [OLD_FACT, NEW_FACT], "without recency the more relevant one leads"
    assert off["top_relevance"] == r["top_relevance"]

    await half_life(client, None)  # back to the server default
    assert order(await recall(client)) == [NEW_FACT, OLD_FACT]


@pytest.mark.asyncio
async def test_restatement_counts_as_recent(client):
    old = await add(client, OLD_FACT, days_ago=400)
    await add(client, NEW_FACT, days_ago=30)
    assert order(await recall(client)) == [NEW_FACT, OLD_FACT]
    await restate(client, old, days_ago=1)
    assert order(await recall(client)) == [OLD_FACT, NEW_FACT]


@pytest.mark.asyncio
async def test_decisions_and_rules_do_not_decay(client):
    # Distinct places in both the vector and the keyword list, so scores compare across recalls.
    decision = "The northwind invoice export must use the east region."
    rule = "The northwind invoice export must use the east region, the export region."
    await add(client, decision, days_ago=700, category="decision")
    await add(client, rule, days_ago=500, category="rule", kind="procedural")
    await add(client, NEW_FACT, days_ago=1)
    on = await recall(client)
    await half_life(client, 0)
    off = await recall(client)
    assert set(order(on)) == set(order(off)) == {decision, rule, NEW_FACT}
    assert scores(on)[decision] == scores(off)[decision]
    assert scores(on)[rule] == scores(off)[rule]
    assert scores(on)[NEW_FACT] < scores(off)[NEW_FACT], "even a day-old fact has decayed a little"


@pytest.mark.asyncio
async def test_history_question_gets_no_recency(client):
    await add(client, OLD_FACT, days_ago=400)
    await add(client, NEW_FACT, days_ago=2)
    history = await recall(client, include_history=True)
    worded = await recall(client, "Which region did the northwind invoice export previously run in?")
    await half_life(client, 0)
    assert order(history) == order(await recall(client, include_history=True)) == [OLD_FACT, NEW_FACT]
    assert scores(history) == scores(await recall(client, include_history=True))
    assert order(worded)[0] == OLD_FACT


@pytest.mark.asyncio
async def test_abstention_and_gate_unchanged(client):
    # One very old fact, the only match: still served, and the gate sees its raw relevance.
    await add(client, OLD_FACT, days_ago=3000)
    on = await recall(client)
    assert not on["abstained"] and order(on) == [OLD_FACT]
    unrelated = await recall(client, UNRELATED)
    assert unrelated["abstained"] and unrelated["abstain_reason"] == "below_threshold"
    await half_life(client, 0)
    off = await recall(client)
    assert off["top_relevance"] == on["top_relevance"] and off["min_relevance"] == on["min_relevance"]
    assert (await recall(client, UNRELATED))["top_relevance"] == unrelated["top_relevance"]


@pytest.mark.asyncio
async def test_kept_memory_ranks_a_little_higher(client):
    await add(client, "The northwind invoice export runs in the east region.", days_ago=3, review="kept")
    await add(client, "The northwind invoice export runs in the west region.", days_ago=2)
    assert order(await recall(client))[0] == "The northwind invoice export runs in the east region."


@pytest.mark.asyncio
async def test_half_life_is_validated(client):
    slug = client.project_slug
    assert (await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"recall_half_life_days": -1})).status_code == 422
    await half_life(client, 30)
    listed = (await client.get("/v1/projects", headers=MGMT)).json()
    assert next(p for p in listed if p["slug"] == slug)["recall_half_life_days"] == 30
