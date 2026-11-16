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


PAGE_SOURCE = "## user · 2026-10-21T09:00\nThe Northwind invoice retry rework is half done. Next is the queue change."
PAGE = json.dumps({"threads": [{
    "key": "topic:retry-rework", "kind": "task", "title": "Invoice retry rework", "status": "open",
    "summary": "The Northwind invoice retry rework is half done; the queue change is next.",
    "sections": [{"label": "Next steps", "items": [{"text": "Make the queue change", "date": "2026-10-21",
                                                    "evidence": [{"episode_id": None, "quote": "Next is the queue change"}]}]}],
    "aliases": [], "related": []}]})
WORK_QUESTION = "Where does the Northwind invoice retry rework stand?"


async def seed_page(client):
    """The two facts, and from a later source the page of a piece of work in progress; the question is
    close to all three and names none."""
    from test_threads import Router

    await seed(client)
    router = Router()
    router.threads.responses = {"Next is the queue change": PAGE}
    r = await client.post("/v1/ingest", json={"items": [{"id": "n-3", "content": PAGE_SOURCE, "timestamp": "2026-10-21T09:00:00Z"}]})
    assert r.status_code == 202, r.text
    async with app.state.pool.connection() as conn:
        pid = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, router, app.state.embedder, pid)
    assert len((await client.get("/v1/memories")).json()) == 2 and len((await client.get("/v1/threads")).json()) == 1


def number_of(start: str) -> int:
    return next(n for n, line in enumerate(order(), 1) if line.startswith(start))


@pytest.mark.asyncio
async def test_a_page_the_model_picks_is_served_though_no_memory_was(client):
    """A question about a piece of work gets its page; the model reads the page's title and summary."""
    await seed_page(client)
    gate_answers('{"use": []}')
    r = await recall(client, WORK_QUESTION)
    assert r["abstained"] and r["abstain_reason"] == "not_needed" and r["threads"] == []
    assert "Work page: Invoice retry rework. The Northwind invoice retry rework is half done" in order()[-1]

    gate_answers(json.dumps({"use": [number_of("Work page:")]}))
    r = await recall(client, WORK_QUESTION)
    assert r["picked_by_model"] is True and not r["abstained"] and r["items"] == []
    assert [t["key"] for t in r["threads"]] == ["topic:retry-rework"] and "Make the queue change" in r["briefing"]


@pytest.mark.asyncio
async def test_a_page_the_model_does_not_pick_is_not_served_beside_a_picked_memory(client):
    await seed_page(client)
    gate_answers('{"use": []}')
    await recall(client, WORK_QUESTION)
    gate_answers(json.dumps({"use": [number_of("Northwind invoices retry")]}))
    r = await recall(client, WORK_QUESTION)
    assert len(r["items"]) == 1 and r["threads"] == [] and "queue change" not in r["briefing"]


@pytest.mark.asyncio
async def test_without_the_models_answer_a_similar_page_still_comes_with_a_hit(client):
    await seed_page(client)
    gate_answers("no idea")
    r = await recall(client, WORK_QUESTION)
    assert r["picked_by_model"] is False and r["items"] and [t["key"] for t in r["threads"]] == ["topic:retry-rework"]


@pytest.mark.asyncio
async def test_off_switch(client, monkeypatch):
    await seed(client)
    gate_answers('{"use": []}')
    monkeypatch.setattr(settings, "recall_gate", False)
    r = await recall(client)
    assert r["picked_by_model"] is False and r["items"]


async def seed_page_only(client):
    """A project whose only knowledge of a piece of work is its page: nothing of it is in memory."""
    from test_threads import Router

    app.state.llm.default = "[]"
    router = Router()
    router.threads.responses = {"Next is the queue change": PAGE}
    r = await client.post("/v1/ingest", json={"items": [{"id": "n-3", "content": PAGE_SOURCE, "timestamp": "2026-10-21T09:00:00Z"}]})
    assert r.status_code == 202, r.text
    async with app.state.pool.connection() as conn:
        pid = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, router, app.state.embedder, pid)
    assert (await client.get("/v1/memories")).json() == [] and len((await client.get("/v1/threads")).json()) == 1


@pytest.mark.asyncio
async def test_a_page_answers_when_memory_holds_nothing_relevant(client):
    """The date of a launch, or where a rework stands, is often on the work's page and in no memory.
    A question about it used to be turned away before the model saw the page."""
    await seed_page_only(client)
    gate_answers(json.dumps({"use": [1]}))
    r = await recall(client, WORK_QUESTION)
    assert order() == ["Work page: Invoice retry rework. The Northwind invoice retry rework is half done; the queue change is next"], order()
    assert r["picked_by_model"] is True and not r["abstained"] and r["items"] == []
    assert [t["key"] for t in r["threads"]] == ["topic:retry-rework"] and "Make the queue change" in r["briefing"]

    # The model reads the page and says the question does not need it: nothing is served.
    gate_answers('{"use": []}')
    r = await recall(client, WORK_QUESTION)
    assert r["abstained"] and r["abstain_reason"] == "not_needed" and r["threads"] == [] and r["briefing"] == ""


@pytest.mark.asyncio
async def test_a_page_alone_is_not_served_without_the_models_pick(client, monkeypatch):
    await seed_page_only(client)
    gate_answers("no idea")  # the model gave no usable answer
    r = await recall(client, WORK_QUESTION)
    assert r["abstained"] and r["abstain_reason"] == "empty_store" and r["threads"] == []
    monkeypatch.setattr(settings, "recall_gate", False)
    r = await recall(client, WORK_QUESTION)
    assert r["abstained"] and r["abstain_reason"] == "empty_store"


@pytest.mark.asyncio
async def test_an_unrelated_question_never_reaches_the_model_because_of_a_page(client):
    await seed_page_only(client)
    gate_answers(json.dumps({"use": [1]}))
    asked = len(app.state.llm.calls)
    r = await recall(client, "Which font does the marketing site use for headings?")
    assert r["abstained"] and r["abstain_reason"] == "empty_store"
    assert len(app.state.llm.calls) == asked, "the model was not asked"


@pytest.mark.asyncio
async def test_a_page_does_not_bring_back_a_memory_that_was_not_relevant_enough(client):
    """With memories in the store but none relevant enough, the model is shown the page only."""
    await seed_page(client)
    monkeypatch_threshold = 0.99  # no memory can pass; the page is measured against the same bar
    await client.patch(f"/v1/projects/{client.project_slug}", json={"recall_min_relevance": monkeypatch_threshold},
                       headers={"Authorization": "Bearer mv_mk_test"})
    gate_answers(json.dumps({"use": [1]}))
    asked = len(app.state.llm.calls)
    r = await recall(client, WORK_QUESTION)
    assert r["abstained"] and r["abstain_reason"] == "below_threshold" and len(app.state.llm.calls) == asked
