"""Consolidation of a conversation project's profile (profile_consolidation.py): lines about the same
thing become one line, the members of a set become a list with its count, a plan whose day has passed
stops being current, and a line no briefing serves ranks lower. What the model returns is checked
without a model, and what a person kept, a standing instruction and a recorded conflict are never
offered to it. Whether a real model groups well is measured in benchmark/beam."""
from __future__ import annotations

import json
import re
from uuid import UUID

import pytest
from conftest import needs_db

from memvine_server import consolidation
from memvine_server.extraction.llm import FakeLLM
from memvine_server.extraction.prompts import CONSOLIDATE_PAIRS_SYSTEM, CONSOLIDATE_PROFILE_SYSTEM
from memvine_server.profile_consolidation import acceptable

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}

# Dana, who runs billing operations at the invented Northwind, talking with an assistant.
FIRST = ("## Dana · 2024-02-10T09:00:00Z\n"
         "I read e-books when I travel. I buy print editions to collect. I listen to audiobooks on the commute. "
         "For the Northwind film night I planned Paddington 2. I also planned Coco. And I planned Up for it. "
         "The Northwind offsite is on March 2. My snack budget is $75. I run billing operations at Northwind. "
         "Priya is my manager. I aim to read 12 books by June. From now on, always give prices in euros. "
         "I have never been to Lisbon.\n\n## Assistant · 2024-02-10T09:01:00Z\nNoted.")
LATER = ("## Dana · 2024-04-10T09:00:00Z\nThe hotel from my week in Lisbon last year sent me a voucher.\n\n"
         "## Assistant · 2024-04-10T09:01:00Z\nNice.")

EBOOKS, PRINT, AUDIO = "Dana prefers e-books when travelling.", "Dana likes print editions to collect.", "Dana listens to audiobooks on the commute."
PADDINGTON, COCO, UP = ("Dana planned Paddington 2 for the Northwind film night.", "Dana planned Coco for the Northwind film night.",
                        "Dana planned Up for the Northwind film night.")
OFFSITE, SNACKS, ROLE = "The Northwind offsite is on 2 Mar 2024.", "Dana's snack budget is $75.", "Dana runs billing operations at Northwind."
PRIYA, BOOKS, EUROS = "Priya is Dana's manager.", "Dana aims to read 12 books by June.", "Always give Dana prices in euros."
NEVER, WEEK = "Dana says she has never been to Lisbon.", "Dana spent a week in Lisbon in 2023."
FORMATS = "Dana reads e-books when travelling, collects print editions and listens to audiobooks on the commute."
FILMS = "Dana has planned 3 films for the Northwind film night: Paddington 2, Coco and Up."


def item(slot, statement, quote, relation="new"):
    return {"slot": slot, "statement": statement, "relation": relation, "subjects": ["dana"], "confidence": "high",
            "standing": False, "quote": quote}


KEPT = [
    item("preference/ebooks-travel", EBOOKS, "e-books when I travel"), item("preference/print-collect", PRINT, "print editions to collect"),
    item("preference/audio-commute", AUDIO, "audiobooks on the commute"), item("decision/film-paddington", PADDINGTON, "I planned Paddington 2"),
    item("decision/film-coco", COCO, "I also planned Coco"), item("decision/film-up", UP, "I planned Up for it"),
    item("plan/northwind-offsite", OFFSITE, "offsite is on March 2"), item("constraint/snack-budget", SNACKS, "My snack budget is $75"),
    item("identity/role", ROLE, "I run billing operations"), item("person/priya", PRIYA, "Priya is my manager"),
    item("goal/books-by-june", BOOKS, "I aim to read 12 books by June"), item("instruction/prices-in-euros", EUROS, "always give prices in euros"),
    item("history/visited-lisbon", NEVER, "I have never been to Lisbon"),
]


class Model(FakeLLM):
    """The fake model, answering the profile prompt by the statements it is shown: `groups` are
    (statement fragments, slot, statement), `past` statement fragments."""

    groups: list = []
    past: list = []

    async def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        if system != CONSOLIDATE_PROFILE_SYSTEM:
            return await super().complete(system, user, max_tokens=max_tokens)
        self.calls.append((system, user))
        lines = {text: int(n) for n, text in re.findall(r"(?m)^(\d+)\. \[[^\]]+\] \([^)]*\) (.*)$", user)}

        def number(fragment):
            return next((n for text, n in lines.items() if fragment in text), 999)

        return json.dumps({"groups": [{"lines": [number(f) for f in fragments], "slot": slot, "statement": statement}
                                      for fragments, slot, statement in self.groups],
                           "past": [number(f) for f in self.past]})

    def asked(self) -> list[str]:
        return [u for s, u in self.calls if s == CONSOLIDATE_PROFILE_SYSTEM]


async def a_profile(client, app):
    from memvine_server.worker import drain

    slug = client.project_slug
    assert (await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"memory_mode": "conversation"})).status_code == 200
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]
    llm = Model(responses={"2024-02-10T09:00": json.dumps(KEPT),
                           "2024-04-10T09:00": json.dumps([item("history/visited-lisbon", WEEK, "my week in Lisbon last year", "conflict")])})
    llm.fast = llm
    for episode_id, content, at in (("ep1", FIRST, "2024-02-10T09:00:00Z"), ("ep2", LATER, "2024-04-10T09:00:00Z")):
        assert (await client.post("/v1/ingest", json={"items": [{"id": episode_id, "content": content, "timestamp": at}]})).status_code == 202
        await drain(app.state.pool, llm, app.state.embedder, project_id)
    async with app.state.pool.connection() as conn:  # a person kept the line about Priya
        await conn.execute("UPDATE memory_items SET review = 'kept' WHERE project_id = %s AND statement = %s", (project_id, PRIYA))
    return project_id, llm


async def held(client) -> dict:
    return {m["statement"]: m for m in (await client.get("/v1/memories", params={"limit": 200})).json()}


def test_a_groups_statement_is_checked_without_a_model():
    lines = [{"statement": SNACKS}, {"statement": BOOKS}]
    assert acceptable("constraint/snack-budget", "Dana's snack budget is $75 and she aims to read 12 books by June.", lines)
    # A number none of its lines has; a list's count is the exception.
    assert not acceptable("constraint/snack-budget", "Dana's snack budget is $90 and she aims to read 12 books.", lines)
    assert acceptable("list/budgets-and-goals", "Dana has 2 targets: a $75 snack budget and 12 books by June.", lines)
    # The day a line was said is shown beside it, so it may appear in the statement.
    from datetime import UTC, datetime
    dated = [{"statement": SNACKS, "recorded_at": datetime(2024, 4, 2, tzinfo=UTC)}, {"statement": BOOKS, "recorded_at": datetime(2024, 4, 2, tzinfo=UTC)}]
    assert acceptable("constraint/snack-budget", "Since 2 April 2024 Dana's snack budget is $75, and she aims to read 12 books by June.", dated)
    assert not acceptable("constraint/snack-budget", "Since 2 April 2024 Dana's snack budget is $75.", lines)
    # No slot, an instruction's slot, nothing to say, or far too long.
    assert not acceptable(None, SNACKS, lines) and not acceptable("instruction/euros", SNACKS, lines)
    assert not acceptable("constraint/snack-budget", "", lines)
    assert not acceptable("constraint/snack-budget", " ".join(["word"] * 120), lines)


@pytest.mark.asyncio
async def test_lines_about_the_same_thing_become_one_and_a_set_becomes_a_list(client, monkeypatch):
    from memvine_server import profile_consolidation
    from memvine_server.main import app

    monkeypatch.setattr(profile_consolidation, "MIN_LINES", 3)  # the tidied profile here is five lines
    project_id, llm = await a_profile(client, app)
    llm.groups = [
        (["e-books when travelling", "print editions to collect", "audiobooks on the commute"], "preference/reading-formats", FORMATS),
        (["Paddington 2", "planned Coco", "planned Up"], "list/films-planned", FILMS),
        # Refused: a number none of its lines has; a line that is not in the profile.
        (["snack budget", "12 books"], "constraint/snack-budget", "Dana's snack budget is $90 and she aims to read 12 books by June."),
        (["billing operations", "no such line"], "identity/role", "Dana runs billing operations at Northwind."),
    ]
    llm.past = ["offsite is on 2 Mar", "snack budget"]  # only a plan can be past
    async with app.state.pool.connection() as conn:
        stats = await consolidation.run_job(conn, llm, project_id, app.state.embedder)
    assert (stats["groups"], stats["lists"], stats["merged"], stats["refused_groups"], stats["expired_plans"]) == (2, 1, 6, 2, 1)
    assert CONSOLIDATE_PAIRS_SYSTEM not in [s for s, _ in llm.calls], "no pair judging in a conversation project"

    # What a person kept, a standing instruction and the two sides of a recorded conflict were not offered.
    (asked,) = llm.asked()
    assert asked.startswith("TODAY: 2024-04-10")
    assert all(text in asked for text in (EBOOKS, PADDINGTON, OFFSITE, SNACKS, ROLE, BOOKS))
    assert not any(text in asked for text in (PRIYA, EUROS, NEVER, WEEK))

    memories = await held(client)
    formats, films = memories[FORMATS], memories[FILMS]
    assert (formats["slot"], formats["status"], films["slot"], films["status"]) == ("preference/reading-formats", "active", "list/films-planned", "active")
    for statement, into in ((EBOOKS, formats), (PRINT, formats), (AUDIO, formats), (PADDINGTON, films), (COCO, films), (UP, films)):
        assert (memories[statement]["status"], memories[statement]["retired_reason"], memories[statement]["merged_into"]) == ("retired", "merged", into["id"])
    # The merged line carries the words each of its lines came from.
    assert {e["quote"] for e in films["evidence"]} == {"I planned Paddington 2", "I also planned Coco", "I planned Up for it"}
    # The refused groups left their lines as they were; the plan ended on the day of the last thing said.
    assert all(memories[s]["status"] == "active" and memories[s]["merged_into"] is None for s in (SNACKS, BOOKS, ROLE, PRIYA, EUROS, NEVER, WEEK))
    assert memories[OFFSITE]["valid_to"].startswith("2024-04-10") and memories[SNACKS]["valid_to"] is None

    # A question that needs the count gets the list, and none of the lines it replaced.
    r = (await client.post("/v1/recall", json={"query": "How many films has Dana planned for the Northwind film night?"})).json()
    statements = [i["memory"]["statement"] for i in r["items"]]
    assert FILMS in statements and not {PADDINGTON, COCO, UP} & set(statements)

    # The next run reads the profile as the first one left it; once a run changes nothing, the model is
    # not asked again until the profile changes.
    llm.groups, llm.past = [], []
    async with app.state.pool.connection() as conn:
        second = await consolidation.run_job(conn, llm, project_id, app.state.embedder)
        third = await consolidation.run_job(conn, llm, project_id, app.state.embedder)
    assert len(llm.asked()) == 2 and FILMS in llm.asked()[1] and OFFSITE not in llm.asked()[1]
    assert second["merged"] == 0 and second["fingerprint"] and third.get("unchanged") == 1

    # A person un-merges one line: it is current again, and the list stays.
    async with app.state.pool.connection() as conn:
        assert await consolidation.unmerge(conn, project_id, UUID(memories[COCO]["id"]), actor="person:dana@example.com")
    memories = await held(client)
    assert memories[COCO]["status"] == "active" and memories[FILMS]["status"] == "active"


@pytest.mark.asyncio
async def test_a_profile_line_no_briefing_serves_ranks_lower(client, monkeypatch):
    from memvine_server.config import settings
    from memvine_server.main import app

    project_id, _ = await a_profile(client, app)
    question = {"query": "Which film did Dana plan for the Northwind film night?"}

    async def first(**more) -> str:
        """The film line that ranks highest."""
        items = (await client.post("/v1/recall", json={**question, **more})).json()["items"]
        return next(i["memory"]["statement"] for i in items if i["memory"]["statement"] in (PADDINGTON, COCO, UP))

    leader = await first()

    async def long_unused() -> None:
        """Written long ago and served by no briefing since (every recall above served it again)."""
        async with app.state.pool.connection() as conn:
            await conn.execute("UPDATE memory_items SET created_at = now() - interval '200 days', last_recalled_at = NULL "
                               "WHERE project_id = %s AND statement = %s", (project_id, leader))

    await long_unused()
    assert await first() != leader, "the other films go ahead of it"
    # A question about a past moment is ranked without it, and so is everything with the weight off.
    await long_unused()
    assert await first(as_of="2024-03-01T00:00:00Z") == leader
    await long_unused()
    monkeypatch.setattr(settings, "recall_unused_weight", 1.0)
    assert await first() == leader
