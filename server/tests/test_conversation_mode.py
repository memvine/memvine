"""Conversation mode (migrations 0011, 0013): a project whose memory is a small profile of one person,
each item the value of a named slot, beside the conversation itself, which recall searches word for
word. What the profile prompt's output is accepted as, the project setting, synthesis without pages or
the gate, a slot's value replaced or contradicted, and recall leading with the profile.
Whether a model keeps the right things is measured with a real model in benchmark/beam."""
from __future__ import annotations

import json
import secrets

import pytest
from conftest import needs_db

from memvine_server.extraction.extract import knowledge_from, person_turns, profile_from, slot_key
from memvine_server.extraction.llm import FakeLLM
from memvine_server.extraction.prompts import CONVERSATION_SYSTEM, KNOWLEDGE_SYSTEM, RELEVANCE_SYSTEM, THREADS_SYSTEM
from memvine_server.recall import PROFILE_HEADER

MGMT = {"Authorization": "Bearer mv_mk_test"}
# A person planning the invented Northwind billing team's offsite. The fake model answers by the time
# in an excerpt's first line, which no statement repeats.
FIRST = ("## Dana · 2024-03-12T09:00:00Z\nI can spend $45 a month on books for the Northwind reading group. "
         "I have never been to Lisbon. From now on, always give prices in euros.\n\n"
         "## Assistant · 2024-03-12T09:01:00Z\nA good review has a summary, your own thoughts and a rating out of 5 stars.\n"
         "## Tips\nRead twenty pages a day.")
RAISED = ("## Dana · 2024-04-02T09:00:00Z\nGood news, my book budget went up to $60 a month.\n\n"
          "## Assistant · 2024-04-02T09:01:00Z\nThat covers two more paperbacks.")
LISBON = ("## Dana · 2024-04-20T09:00:00Z\nThe hotel from my week in Lisbon last year sent me a voucher.\n\n"
          "## Assistant · 2024-04-20T09:01:00Z\nNice.")

BUDGET, BUDGET_RAISED = "Dana's monthly budget for books is $45.", "Dana's monthly budget for books is $60, raised from $45."
NEVER, WEEK = "Dana says she has never been to Lisbon.", "Dana spent a week in Lisbon in 2023."
EUROS = "Always give Dana prices in euros."


def item(slot, statement, quote, relation="new", **extra):
    return {"slot": slot, "statement": statement, "relation": relation, "subjects": ["dana"], "confidence": "high",
            "standing": False, "quote": quote, **extra}


KEPT_FIRST = [
    item("Constraint / Monthly book budget", BUDGET, "I can spend $45 a month on books"),
    item("history/visited-lisbon", NEVER, "I have never been to Lisbon"),
    item("instruction/prices-in-euros", EUROS, "always give prices in euros"),
    # The assistant's advice restated: its only source is the assistant's turn.
    item("preference/review-format", "A review should have a summary, thoughts and a rating out of 5 stars.",
         "a rating out of 5 stars"),
    item("preference/reading-pace", "Dana reads twenty pages a day.", "Read twenty pages a day"),
]


def test_a_slot_has_a_known_type_and_a_plain_name():
    assert slot_key("Constraint / Monthly book budget") == "constraint/monthly-book-budget"
    assert slot_key("person/Dana's sister") == "person/dana-s-sister"
    assert slot_key("mood/today") is None and slot_key("plan/") is None and slot_key(None) is None


def test_only_the_persons_turns_are_theirs():
    theirs = person_turns(FIRST)
    assert "I can spend $45" in theirs and "rating out of 5 stars" not in theirs
    # A heading inside a reply is not a turn of its own: the tips are still the assistant's.
    assert "twenty pages" not in theirs
    assert person_turns("No headers here, just a note.") is None


def test_the_profile_keeps_what_the_person_said_under_a_slot():
    result = profile_from(json.dumps([*KEPT_FIRST, item("mood/today", "A mood.", "I can spend $45"),
                                      item("state/pages", "Not in the text.", "this quote is not in the source")]), FIRST)
    assert [c.slot for c in result.items] == ["constraint/monthly-book-budget", "history/visited-lisbon", "instruction/prices-in-euros"]
    assert [(c.category, c.kind) for c in result.items] == [("fact", "semantic"), ("event", "episodic"), ("rule", "procedural")]
    assert [c.standing for c in result.items] == [False, False, True]
    # The two items drawn from the assistant's turn are refused; an unknown type and a made-up quote are dropped.
    assert result.dropped_gate == 2 and result.dropped_invalid == 1 and result.dropped_no_quote == 1
    assert all(c.valid_from is None and c.valid_until is None and c.assertion == "stated" and c.relation == "new"
               for c in result.items)
    # The same statements as coding knowledge have no category the knowledge gate knows.
    assert knowledge_from(json.dumps(KEPT_FIRST), FIRST).dropped_invalid == len(KEPT_FIRST)
    # Text with no turn headers cannot say who spoke: the quote only has to be in it.
    plain = profile_from(json.dumps([item("preference/reading-pace", "Dana reads twenty pages a day.", "twenty pages a day")]),
                         "Dana wrote: I read twenty pages a day.")
    assert len(plain.items) == 1


def test_a_plan_ends_on_the_day_it_is_for():
    from datetime import UTC, datetime

    said = datetime(2024, 3, 12, 9, tzinfo=UTC)
    source = "## Dana · 2024-03-12T09:00:00Z\nThe Northwind offsite is on March 29. My budget for it is $900."
    result = profile_from(json.dumps([
        item("plan/northwind-offsite", "The Northwind offsite is on 29 Mar 2024.", "offsite is on March 29", until="2024-03-29"),
        # Only a plan ends, and a day already past when it was said is no end at all.
        item("constraint/offsite-budget", "Dana's budget for the offsite is $900.", "My budget for it is $900", until="2024-03-29"),
        item("plan/northwind-kickoff", "The Northwind kickoff was on 1 Mar 2024.", "The Northwind offsite", until="2024-03-01"),
    ]), source, said)
    offsite, budget, kickoff = result.items
    assert offsite.valid_until == datetime(2024, 3, 29, 23, 59, 59, tzinfo=UTC)
    assert budget.valid_until is None and kickoff.valid_until is None


@needs_db
@pytest.mark.asyncio
async def test_a_conversation_project_keeps_a_profile_and_recalls_it_before_the_excerpts(client, monkeypatch):
    from memvine_server.config import settings
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

    llm = FakeLLM(responses={
        "2024-03-12T09:00": json.dumps(KEPT_FIRST),
        "2024-04-02T09:00": json.dumps([item("constraint/monthly-book-budget", BUDGET_RAISED, "my book budget went up to $60 a month", "update")]),
        "2024-04-20T09:00": json.dumps([item("history/visited-lisbon", WEEK, "my week in Lisbon last year", "conflict")]),
    })
    llm.fast = llm
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]

    async def said(episode_id, content, at):
        r = await client.post("/v1/ingest", json={"items": [{"id": episode_id, "content": content, "timestamp": at}]})
        assert r.status_code == 202
        await drain(app.state.pool, llm, app.state.embedder, project_id)
        assert all(j["status"] == "done" for j in (await client.get("/v1/jobs")).json()["jobs"])

    await said("ep1", FIRST, "2024-03-12T09:00:00Z")
    systems = [s for s, _ in llm.calls]
    assert CONVERSATION_SYSTEM in systems and THREADS_SYSTEM not in systems and KNOWLEDGE_SYSTEM not in systems
    asked = next(u for s, u in llm.calls if s == CONVERSATION_SYSTEM)
    assert "<profile>\n(nothing yet)\n</profile>" in asked and "What memory already knows" not in asked
    memories = (await client.get("/v1/memories")).json()
    assert sorted(m["slot"] for m in memories) == ["constraint/monthly-book-budget", "history/visited-lisbon", "instruction/prices-in-euros"]
    budget = next(m for m in memories if m["slot"].startswith("constraint"))
    assert budget["valid_to"] is None and budget["valid_from"].startswith("2024-03-12")
    assert (await client.get("/v1/threads")).json() == []

    # The next excerpt is read with the profile beside it, and a changed value lands on its slot.
    llm.calls.clear()
    await said("ep2", RAISED, "2024-04-02T09:00:00Z")
    asked = next(u for s, u in llm.calls if s == CONVERSATION_SYSTEM)
    assert f"constraint/monthly-book-budget: {BUDGET} (2024-03-12)" in asked
    memories = (await client.get("/v1/memories")).json()
    held = {m["statement"]: m for m in memories}
    assert held[BUDGET]["status"] == "superseded" and held[BUDGET]["superseded_by"] == held[BUDGET_RAISED]["id"]
    assert held[BUDGET_RAISED]["slot"] == "constraint/monthly-book-budget" and held[BUDGET_RAISED]["status"] == "active"

    # A statement that cannot be true together with the slot's, with no change said: both stand.
    await said("ep3", LISBON, "2024-04-20T09:00:00Z")
    held = {m["statement"]: m for m in (await client.get("/v1/memories")).json()}
    assert held[NEVER]["status"] == held[WEEK]["status"] == "active"
    assert held[WEEK]["conflicts_with"] == [held[NEVER]["id"]] and held[NEVER]["conflicts_with"] == [held[WEEK]["id"]]

    # A detail kept before there were slots stays stored and is not served.
    async with app.state.pool.connection() as conn:
        await conn.execute(
            "INSERT INTO memory_items (project_id, kind, statement, subjects, valid_from, recorded_at, assertion, embedding, category) "
            "SELECT project_id, kind, 'The assistant suggested a monthly book budget review.', subjects, valid_from, recorded_at, "
            "assertion, embedding, category FROM memory_items WHERE id = %s", (held[BUDGET_RAISED]["id"],))

    llm.calls.clear()
    r = (await client.post("/v1/recall", json={"query": "What is my monthly budget for books?"})).json()
    assert not r["abstained"] and not r["picked_by_model"]
    assert RELEVANCE_SYSTEM not in [s for s, _ in llm.calls], "no model pick in conversation mode"
    statements = [i["memory"]["statement"] for i in r["items"]]
    assert statements[0] == BUDGET_RAISED and r["items"][0]["previous"] == BUDGET
    assert not any("budget review" in s for s in statements), "a detail without a slot is not served"
    # The profile comes first, under its own header, then the person's original words.
    briefing = r["briefing"]
    assert PROFILE_HEADER in briefing and f"(was: {BUDGET.rstrip('.')})" in briefing
    assert briefing.index(PROFILE_HEADER) < briefing.index("--- source excerpts (the original words) ---")
    assert any("went up to $60 a month" in p["content"] for p in r["passages"])
    # The standing instruction is in its own section, never among the items.
    assert r["standing"] == [EUROS] and EUROS not in statements

    r = (await client.post("/v1/recall", json={"query": "Have I ever been to Lisbon?"})).json()
    assert "Conflicting statements on record" in r["briefing"]
    assert NEVER.rstrip(".") in r["briefing"] and WEEK.rstrip(".") in r["briefing"]

    # A question gets the few profile lines closest to it, not the whole profile.
    monkeypatch.setattr(settings, "recall_profile_lines", 1)
    r = (await client.post("/v1/recall", json={"query": "What is my monthly budget for books, and have I been to Lisbon?"})).json()
    assert len(r["items"]) == 1
    monkeypatch.setattr(settings, "recall_profile_lines", 10)

    # With a reranker, its order of the candidates decides which line leads (rerank.py).
    from memvine_server import rerank

    class LastFirst:
        async def scores(self, query, texts):
            self.asked = (query, len(texts))
            return [float(i) for i in range(len(texts))]

    wide = "Dana's budget for books and her week in Lisbon"
    before = (await client.post("/v1/recall", json={"query": wide})).json()["items"]
    stub = LastFirst()
    rerank.set_reranker(stub)
    try:
        after = (await client.post("/v1/recall", json={"query": wide})).json()["items"]
        # A summary is answered from the breadth of the fused lists: the reranker is not asked.
        asked, stub.asked = stub.asked, None
        summary = (await client.post("/v1/recall", json={"query": "Give me a summary of Dana's budget for books and her week in Lisbon"})).json()
        assert stub.asked is None and len(summary["items"]) > 1
    finally:
        rerank.set_reranker(None)
    # It reads every candidate, the standing instruction among them, which is then served in its own section.
    assert len(before) > 1 and asked == (wide, len(before) + 1)
    assert {i["memory"]["id"] for i in after} == {i["memory"]["id"] for i in before}
    assert after[0]["memory"]["id"] != before[0]["memory"]["id"]

    # Nothing in memory or the sources shares a word with the question: nothing to return.
    r = (await client.post("/v1/recall", json={"query": "zebra crossing rules"})).json()
    assert r["abstained"] and r["briefing"] == ""


@needs_db
@pytest.mark.asyncio
async def test_a_project_can_be_created_in_conversation_mode(client):
    tag = secrets.token_hex(3)  # names are unique within the organization, and the database outlives a test
    r = await client.post("/v1/projects", headers=MGMT, json={"name": f"Northwind chat {tag}", "memory_mode": "conversation"})
    assert r.status_code == 201 and r.json()["memory_mode"] == "conversation"
    r = await client.post("/v1/projects", headers=MGMT, json={"name": f"Northwind code {tag}"})
    assert r.status_code == 201 and r.json()["memory_mode"] == "coding"


@needs_db
@pytest.mark.asyncio
async def test_a_slot_name_used_for_another_thing_never_replaces_what_the_slot_holds(client):
    """Only the model's word "update" retires a value. Called new under a taken name, the item gets a
    name of its own; the same statement under another name adds evidence and writes nothing twice."""
    from memvine_server.main import app
    from memvine_server.worker import drain

    slug = client.project_slug
    await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"memory_mode": "conversation"})
    sister = "Priya is Dana's sister and lives in Porto."
    manager = "Priya Rao is Dana's manager at Northwind."
    first = "## Dana · 2024-05-01T09:00:00Z\nMy sister Priya lives in Porto.\n\n## Assistant · 2024-05-01T09:01:00Z\nNoted."
    second = ("## Dana · 2024-05-02T09:00:00Z\nMy manager is also called Priya, Priya Rao. And yes, my sister Priya lives in Porto."
              "\n\n## Assistant · 2024-05-02T09:01:00Z\nNoted.")
    llm = FakeLLM(responses={
        "2024-05-01T09:00": json.dumps([item("person/priya", sister, "My sister Priya lives in Porto")]),
        "2024-05-02T09:00": json.dumps([item("person/priya", manager, "My manager is also called Priya"),
                                        item("person/priya-sister", sister, "my sister Priya lives in Porto")]),
    })
    llm.fast = llm
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]
    for episode_id, content, at in (("ep1", first, "2024-05-01T09:00:00Z"), ("ep2", second, "2024-05-02T09:00:00Z")):
        await client.post("/v1/ingest", json={"items": [{"id": episode_id, "content": content, "timestamp": at}]})
        await drain(app.state.pool, llm, app.state.embedder, project_id)
    memories = (await client.get("/v1/memories")).json()
    assert {(m["slot"], m["statement"], m["status"]) for m in memories} == {
        ("person/priya", sister, "active"), ("person/priya-2", manager, "active")}
    assert len(next(m for m in memories if m["statement"] == sister)["evidence"]) == 2


@needs_db
@pytest.mark.asyncio
async def test_an_item_is_written_only_for_what_it_adds(client):
    """An item about to get a slot of its own is compared with the lines it most resembles: covered,
    it adds evidence; adding something lasting, it is folded into the line; a fold that would lose a
    number is refused, and both are kept."""
    from memvine_server.extraction.prompts import NET_NEW_SYSTEM
    from memvine_server.main import app
    from memvine_server.worker import drain

    slug = client.project_slug
    await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"memory_mode": "conversation"})
    likes = "Dana likes Brandon Sanderson's short stories."
    rereads = "Dana rereads Brandon Sanderson's short stories."
    lake = "Dana reads Brandon Sanderson's short stories on Sunday mornings at the lake."
    folded = "Dana likes Brandon Sanderson's short stories and reads them on Sunday mornings at the lake."
    fourteen = "Dana has read 14 of Brandon Sanderson's short stories on Sunday mornings."

    def excerpt(day, words):
        return f"## Dana · 2024-05-0{day}T09:00:00Z\n{words}\n\n## Assistant · 2024-05-0{day}T09:01:00Z\nNoted."

    llm = FakeLLM(responses={
        "2024-05-01T09:00": json.dumps([item("preference/sanderson-short-stories", likes, "I like Sanderson")]),
        "2024-05-02T09:00": json.dumps([item("state/sanderson-rereads", rereads, "rereading Sanderson")]),
        "2024-05-03T09:00": json.dumps([item("state/sunday-reading", lake, "Sunday mornings at the lake")]),
        "2024-05-04T09:00": json.dumps([item("state/sanderson-count", fourteen, "14 of them")]),
        f"NEW: {rereads}": json.dumps({"verdict": "covered", "match": 1, "merged": None}),
        f"NEW: {lake}": json.dumps({"verdict": "adds", "match": 1, "merged": folded}),
        # A fold that drops the 14: refused.
        f"NEW: {fourteen}": json.dumps({"verdict": "adds", "match": 1, "merged": folded}),
    })
    llm.fast = llm
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]
    said = ["I like Sanderson's short stories.", "I am rereading Sanderson again.", "I read them on Sunday mornings at the lake.",
            "I have read 14 of them by now."]
    for day, words in enumerate(said, start=1):
        await client.post("/v1/ingest", json={"items": [{"id": f"ep{day}", "content": excerpt(day, words),
                                                         "timestamp": f"2024-05-0{day}T09:00:00Z"}]})
        await drain(app.state.pool, llm, app.state.embedder, project_id)
    assert all(j["status"] == "done" for j in (await client.get("/v1/jobs")).json()["jobs"])
    # The first item had nothing to be compared with; each later one was.
    asked = [u.split("\n")[0] for s, u in llm.calls if s == NET_NEW_SYSTEM]
    assert asked == [f"NEW: {rereads}", f"NEW: {lake}", f"NEW: {fourteen}"]
    memories = {m["slot"]: m for m in (await client.get("/v1/memories")).json()}
    assert set(memories) == {"preference/sanderson-short-stories", "state/sanderson-count"}
    line = memories["preference/sanderson-short-stories"]
    assert line["statement"] == folded and line["status"] == "active"
    assert sorted(e["episode_id"] for e in line["evidence"]) == ["ep1", "ep2", "ep3"]
    assert memories["state/sanderson-count"]["statement"] == fourteen
    # A standing instruction takes no part in the check, on either side: it is never folded into a
    # line about the person, and nothing is folded into it.
    llm.calls.clear()
    always = "Always name the narrator when Dana asks about Brandon Sanderson's short stories."
    llm.responses["2024-05-05T09:00"] = json.dumps([item("instruction/name-the-narrator", always, "always name the narrator")])
    llm.responses[f"NEW: {always}"] = json.dumps({"verdict": "covered", "match": 1, "merged": None})
    await client.post("/v1/ingest", json={"items": [{"id": "ep5", "timestamp": "2024-05-05T09:00:00Z", "content": excerpt(
        5, "From now on, always name the narrator when I ask about Sanderson's short stories.")}]})
    await drain(app.state.pool, llm, app.state.embedder, project_id)
    assert NET_NEW_SYSTEM not in [s for s, _ in llm.calls]
    kept = next(m for m in (await client.get("/v1/memories")).json() if m["slot"] == "instruction/name-the-narrator")
    assert kept["standing"] and kept["statement"] == always
    # The folded line is found by what was added to it.
    r = (await client.post("/v1/recall", json={"query": "Where does Dana go to the lake?"})).json()
    assert r["items"][0]["memory"]["statement"] == folded
