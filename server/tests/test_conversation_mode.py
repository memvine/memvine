"""Conversation mode (migration 0011): a project whose memory keeps every concrete detail of a long
conversation and recalls wide. What the detail prompt's output is accepted as, the project setting,
synthesis without pages or the gate, and recall returning details with their source excerpts.
Whether a model keeps the right details is measured with a real model in benchmark/beam."""
from __future__ import annotations

import json
import secrets

import pytest
from conftest import needs_db

from memvine_server.extraction.extract import details_from, knowledge_from
from memvine_server.extraction.llm import FakeLLM
from memvine_server.extraction.prompts import CONVERSATION_SYSTEM, KNOWLEDGE_SYSTEM, RELEVANCE_SYSTEM, THREADS_SYSTEM

MGMT = {"Authorization": "Bearer mv_mk_test"}
SOURCE = ("## user · 2024-03-12T09:00:00Z\nMy first sprint for the Northwind budget tracker ends on March 29. "
          "As of today I am still waiting for the design review. I finished the login page yesterday and all tests pass.\n\n"
          "## assistant · 2024-03-12T09:01:00Z\nI recommend bcrypt with 12 salt rounds for the passwords.")


def detail(statement, category, quote, **extra):
    return {"statement": statement, "category": category, "subjects": ["budget tracker"], "confidence": "high",
            "standing": False, "quote": quote, **extra}


DETAILS = [
    detail("The user's first sprint for the Northwind budget tracker ends on 29 Mar 2024.", "plan", "ends on March 29",
           valid_from="2024-03-29", valid_until="2024-03-30"),
    detail("As of 12 Mar 2024 the user is still waiting for the design review.", "fact", "still waiting for the design review"),
    detail("The user finished the login page on 11 Mar 2024 and all tests pass.", "event", "I finished the login page yesterday"),
    detail("The assistant recommended bcrypt with 12 salt rounds for the budget tracker's passwords.", "fact",
           "bcrypt with 12 salt rounds"),
]


def test_details_keep_what_the_knowledge_gate_refuses():
    result = details_from(json.dumps([*DETAILS, detail("A mood.", "vibe", "My first sprint"),
                                      detail("Not in the text.", "fact", "this quote is not in the source")]), SOURCE)
    assert [c.category for c in result.items] == ["plan", "fact", "event", "fact"]
    assert [c.kind for c in result.items] == ["prospective", "semantic", "episodic", "semantic"]
    assert result.dropped_invalid == 1 and result.dropped_no_quote == 1 and result.dropped_gate == 0
    # A plan is on record from the day it was said and does not expire; "all tests pass" is a detail here.
    assert all(c.valid_from is None and c.valid_until is None and c.assertion == "stated" for c in result.items)
    # The same statements as coding knowledge: the status snapshot is refused, "event" and "plan" are not categories.
    coding = knowledge_from(json.dumps(DETAILS), SOURCE)
    assert coding.dropped_gate == 1 and coding.dropped_invalid == 2


@needs_db
@pytest.mark.asyncio
async def test_a_conversation_project_keeps_details_and_recalls_wide(client):
    from memvine_server.main import app
    from memvine_server.worker import drain

    slug = client.project_slug
    projects = (await client.get("/v1/projects", headers=MGMT)).json()
    assert next(p for p in projects if p["slug"] == slug)["memory_mode"] == "coding"
    assert (await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"memory_mode": "diary"})).status_code == 422
    r = await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"memory_mode": "conversation"})
    assert r.status_code == 200 and r.json()["memory_mode"] == "conversation"
    # Another setting leaves the mode alone.
    assert (await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"description": "A chat"})).json()["memory_mode"] == "conversation"

    llm = FakeLLM(responses={"My first sprint": json.dumps(DETAILS)})
    llm.fast = llm
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]
    r = await client.post("/v1/ingest", json={"items": [{"id": "ep1", "content": SOURCE, "timestamp": "2024-03-12T09:00:00Z"}]})
    assert r.status_code == 202
    await drain(app.state.pool, llm, app.state.embedder, project_id)
    assert all(j["status"] == "done" for j in (await client.get("/v1/jobs")).json()["jobs"])

    systems = [s for s, _ in llm.calls]
    assert CONVERSATION_SYSTEM in systems and THREADS_SYSTEM not in systems and KNOWLEDGE_SYSTEM not in systems
    memories = (await client.get("/v1/memories")).json()
    assert sorted(m["category"] for m in memories) == ["event", "fact", "fact", "plan"]
    plan = next(m for m in memories if m["category"] == "plan")
    assert plan["valid_to"] is None and plan["valid_from"].startswith("2024-03-12")
    assert (await client.get("/v1/threads")).json() == []

    llm.calls.clear()
    r = (await client.post("/v1/recall", json={"query": "When does my first sprint end?"})).json()
    assert not r["abstained"] and not r["picked_by_model"]
    assert RELEVANCE_SYSTEM not in [s for s, _ in llm.calls], "no model pick in conversation mode"
    assert any("29 Mar 2024" in i["memory"]["statement"] for i in r["items"])
    # The source excerpt comes back without being asked for.
    assert r["passages"] and "ends on March 29" in r["passages"][0]["content"]
    assert "--- source excerpts (the original words) ---" in r["briefing"]

    # Nothing in memory or the sources shares a word with the question: nothing to return.
    r = (await client.post("/v1/recall", json={"query": "zebra migration patterns"})).json()
    assert r["abstained"] and r["briefing"] == ""


@needs_db
@pytest.mark.asyncio
async def test_a_project_can_be_created_in_conversation_mode(client):
    tag = secrets.token_hex(3)  # names are unique within the organization, and the database outlives a test
    r = await client.post("/v1/projects", headers=MGMT, json={"name": f"Northwind chat {tag}", "memory_mode": "conversation"})
    assert r.status_code == 201 and r.json()["memory_mode"] == "conversation"
    r = await client.post("/v1/projects", headers=MGMT, json={"name": f"Northwind code {tag}"})
    assert r.status_code == 201 and r.json()["memory_mode"] == "coding"


def test_an_update_in_a_conversation_has_to_announce_the_change():
    from memvine_server.reconcile import announces_change

    assert announces_change("The user moved the launch deadline from 15 Mar to 1 Apr 2024.")
    assert announces_change("The user is switching the budget tracker's database from SQLite to PostgreSQL.")
    assert announces_change("The first sprint now targets completion by 31 Mar 2024.")
    # Another version of the same subject is another detail, not a replacement.
    assert not announces_change("The /login POST route calls login_user and returns 200 'Logged in successfully'.")
    assert not announces_change("Phase 5 runs February 16 - April 15, 2024.")
