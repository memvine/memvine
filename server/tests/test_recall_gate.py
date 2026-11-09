"""Inside a project everything looks similar to everything, so the model picks what a question needs:
what it does not pick is not served, what it picks is, and without its answer recall still works."""
from __future__ import annotations

import asyncio
import json

import pytest
from conftest import needs_db

from memvine_server.config import settings
from memvine_server.extraction.prompts import RELEVANCE_SYSTEM
from memvine_server.main import app
from memvine_server.worker import drain

pytestmark = needs_db

MGMT = {"Authorization": "Bearer mv_mk_test"}
SOURCE = "## Developer · 2026-10-20T09:00\nNorthwind billing notes: invoices retry and invoices round."
EXTRACTED = json.dumps([
    {"statement": "Northwind invoices retry three times before failing.", "kind": "semantic", "category": "fact",
     "subjects": ["invoices"], "assertion": "stated", "confidence": "high", "quote": "invoices retry"},
    {"statement": "Northwind invoices round to the nearest cent.", "kind": "semantic", "category": "fact",
     "subjects": ["invoices"], "assertion": "stated", "confidence": "high", "quote": "invoices round"},
])
QUESTION = "Northwind invoices"


async def seed(client):
    app.state.llm.responses = {"invoices retry and invoices round": EXTRACTED}
    app.state.llm.default = "[]"
    r = await client.post("/v1/ingest", json={"items": [{"id": "n-1", "content": SOURCE, "timestamp": "2026-10-20T09:00:00Z"}]})
    assert r.status_code == 202, r.text
    async with app.state.pool.connection() as conn:
        pid = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, pid)
    assert len((await client.get("/v1/memories")).json()) == 2


def gate_answers(answer: str):
    """The fake model answers `answer` to recall's question and nothing else changes."""
    llm, original = app.state.llm, app.state.llm.complete

    async def complete(system, user, *, max_tokens=4096):
        if system == RELEVANCE_SYSTEM:
            llm.calls.append((system, user))
            return answer
        return await original(system, user, max_tokens=max_tokens)

    llm.complete = complete


def order() -> list[str]:
    """The candidates as the model saw them, numbered from 1."""
    user = next(u for s, u in reversed(app.state.llm.calls) if s == RELEVANCE_SYSTEM)
    return [line.split(". ", 1)[1] for line in user.split("<memories>\n")[1].split("\n</memories>")[0].splitlines()]


async def recall(client, query=QUESTION, **extra):
    r = await client.post("/v1/recall", json={"query": query, **extra})
    assert r.status_code == 200
    return r.json()


@pytest.mark.asyncio
async def test_only_what_the_model_picks_is_served(client):
    await seed(client)
    gate_answers('{"use": [1]}')
    r = await recall(client)
    assert r["picked_by_model"] is True
    assert [i["memory"]["statement"] for i in r["items"]] == [order()[0]]
    assert order()[1] not in r["briefing"]


@pytest.mark.asyncio
async def test_nothing_picked_abstains(client):
    await seed(client)
    gate_answers('{"use": []}')
    r = await recall(client)
    assert r["abstained"] and r["abstain_reason"] == "not_needed" and r["briefing"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["no idea", "[]", '{"use": "all"}', '{"pick": [1]}'])
async def test_an_unusable_answer_falls_back_to_similarity(client, answer):
    await seed(client)
    gate_answers(answer)
    r = await recall(client)
    assert r["picked_by_model"] is False and not r["abstained"] and r["items"]


@pytest.mark.asyncio
async def test_a_late_model_falls_back_to_similarity(client, monkeypatch):
    await seed(client)
    original = app.state.llm.complete

    async def slow(system, user, *, max_tokens=4096):
        if system == RELEVANCE_SYSTEM:
            await asyncio.sleep(5)
        return await original(system, user, max_tokens=max_tokens)

    app.state.llm.complete = slow
    monkeypatch.setattr(settings, "recall_gate_timeout", 0.05)
    r = await recall(client)
    assert r["picked_by_model"] is False and r["items"]


@pytest.mark.asyncio
async def test_numbers_outside_the_list_are_ignored(client):
    await seed(client)
    gate_answers('{"use": ["1", 99, 0]}')
    r = await recall(client)
    assert len(r["items"]) == 1


@pytest.mark.asyncio
async def test_unrelated_questions_never_reach_the_model(client):
    await seed(client)
    gate_answers('{"use": [1]}')
    app.state.llm.calls.clear()
    r = await recall(client, "Weather forecast for Paris tomorrow?")
    assert r["abstained"] and r["abstain_reason"] == "below_threshold"
    assert not [c for c in app.state.llm.calls if c[0] == RELEVANCE_SYSTEM]


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"profiles": True}, {"query": "Northwind invoices overview"}])
async def test_briefs_and_broad_questions_keep_breadth(client, extra):
    await seed(client)
    gate_answers('{"use": []}')
    app.state.llm.calls.clear()
    r = await recall(client, **{"query": QUESTION, **extra})
    assert r["picked_by_model"] is False and len(r["items"]) == 2
    assert not [c for c in app.state.llm.calls if c[0] == RELEVANCE_SYSTEM]


@pytest.mark.asyncio
async def test_a_long_task_that_says_summary_is_not_broad(client):
    await seed(client)
    gate_answers('{"use": [1]}')
    r = await recall(client, "## Summary\n" + "Northwind invoices need a new retry path. " * 10)
    assert r["picked_by_model"] is True and len(r["items"]) == 1


@pytest.mark.asyncio
async def test_a_picked_standing_rule_is_served_though_no_item_was(client):
    app.state.llm.responses = {"retries": json.dumps([
        {"statement": "Always ship Northwind invoices with make ship.", "kind": "procedural", "category": "rule", "standing": True,
         "subjects": ["invoices"], "assertion": "stated", "confidence": "high", "quote": "retries"}])}
    app.state.llm.default = "[]"
    await client.post("/v1/ingest", json={"items": [{"id": "n-2", "content": "## Developer · 2026-10-20T09:00\nretries", "timestamp": "2026-10-20T09:00:00Z"}]})
    async with app.state.pool.connection() as conn:
        pid = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, pid)
    gate_answers('{"use": [1]}')
    r = await recall(client, "How do I ship Northwind invoices?")
    assert not r["abstained"] and not r["items"] and "make ship" in r["briefing"]


@pytest.mark.asyncio
async def test_off_switch(client, monkeypatch):
    await seed(client)
    gate_answers('{"use": []}')
    monkeypatch.setattr(settings, "recall_gate", False)
    r = await recall(client)
    assert r["picked_by_model"] is False and r["items"]
