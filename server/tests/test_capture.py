"""Automatic capture from coding sessions: the plugin's tool outcome lines reach extraction, the
knowledge prompt admits only what passes its three tests (another task, not in the code, still true
later), and the gate refuses run-specific trivia and status claims."""
from __future__ import annotations

import json

import pytest
from conftest import needs_db

from memvine_server.extraction.extract import gated, knowledge_from
from memvine_server.extraction.prompts import KNOWLEDGE_SYSTEM, THREADS_SYSTEM

MGMT = {"Authorization": "Bearer mv_mk_test"}

# A session as the Claude Code plugin 0.6 sends it: prompts and replies, and between them one
# "tool" event per stretch of tool work.
TURNS = [
    {"role": "user", "content": "Why does the export test fail on my machine?", "at": "2026-10-26T09:00:00Z"},
    {"role": "tool", "at": "2026-10-26T09:01:00Z", "content":
        "[tools] Read ok: server/tests/conftest.py, server/docker-compose.yml; Bash `uv run pytest tests/test_export.py -q` "
        "failed (exit 1): E psycopg.OperationalError: connection refused on port 5432 1 failed in 0.4s"},
    {"role": "assistant", "at": "2026-10-26T09:02:00Z", "content":
        "conftest.py reads MEMVINE_TEST_DATABASE_URL and the compose file publishes Postgres on 5433, not 5432. "
        "Tests also create their own database, so they never touch the dev one."},
    {"role": "tool", "at": "2026-10-26T09:03:00Z", "content":
        "[tools] Bash `MEMVINE_TEST_DATABASE_URL=postgresql://memvine:memvine@localhost:5433/memvine_test uv run pytest tests/test_export.py -q` "
        "exit 0: .... 4 passed in 0.9s"},
    {"role": "assistant", "at": "2026-10-26T09:04:00Z", "content": "Task complete: all four export tests pass now."},
    {"role": "user", "content": "Great, thanks.", "at": "2026-10-26T09:05:00Z"},
]

LESSONS = [
    # fail then succeed: the cause and the fix, quoting the failing tool line.
    {"statement": "The server tests need Postgres on port 5433 (docker-compose); against 5432 they fail with connection refused.",
     "category": "gotcha", "why": "the default port is wrong for this project", "scope": "project", "subjects": ["server tests"],
     "assertion": "observed", "confidence": "high", "quote": "connection refused on port 5432"},
    # read then applied: a fact found by reading code.
    {"statement": "Server tests create and use their own database, never the development one.",
     "category": "fact", "why": "a test that drains the queue must not pick up real jobs", "scope": "project", "subjects": ["server tests"],
     "assertion": "observed", "confidence": "high", "quote": "Tests also create their own database"},
    # verified by success: the approach a passing run confirmed.
    {"statement": "Run the server tests with MEMVINE_TEST_DATABASE_URL pointing at the Postgres on localhost:5433.",
     "category": "rule", "why": "confirmed by a passing run", "scope": "project", "subjects": ["server tests"],
     "assertion": "observed", "confidence": "high", "quote": "4 passed in 0.9s"},
    # Trivia and claims the gate refuses whatever the model says.
    {"statement": "The export tests ran from /private/tmp/claude-501/scratchpad/run3.", "category": "fact", "why": "",
     "assertion": "observed", "quote": "4 passed in 0.9s"},
    {"statement": "Session 3f2b9c1e-8a7d-4e6f-9b0a-1c2d3e4f5a6b ran the export tests.", "category": "fact", "why": "",
     "assertion": "observed", "quote": "4 passed in 0.9s"},
    {"statement": "The task is complete: all four export tests pass.", "category": "fact", "why": "",
     "assertion": "claimed", "quote": "Task complete: all four export tests pass now."},
    # The same lesson twice in one answer is stored once.
    {"statement": "The server tests need Postgres on port 5433 (docker-compose); against 5432 they fail with connection refused.",
     "category": "gotcha", "why": "dup", "scope": "project", "subjects": ["server tests"],
     "assertion": "observed", "confidence": "high", "quote": "connection refused on port 5432"},
]


def test_knowledge_prompt_holds_the_admission_tests_and_the_exclusions():
    """Memory keeps only what passes all three tests; the work, states of the moment and code
    descriptions are refused (real data: 39 items from 8 sessions under the old rules, 7 now)."""
    for phrase in ("Too much memory is worse than too little", "The normal answer is [] or one item",
                   "1. Another task.", "2. Not in the code.", "3. Still true later.",
                   "Never describe what the code does", "the work and its findings", "states of the moment",
                   'role "tool"', "run-specific trivia", "status and completion claims", "the same lesson twice"):
        assert phrase in KNOWLEDGE_SYSTEM, phrase
    assert "never copy them onto the page" in THREADS_SYSTEM


def test_gate_refuses_run_trivia_and_status_but_keeps_rules_about_temp_folders():
    assert gated("Built artifacts land in /private/tmp/claude-501/x/scratchpad/out")
    assert gated("Logs are under /var/folders/ab/T/memvine")
    assert gated("The run wrote /tmp/tmpk3j9x/out.json")
    assert gated("Job 3f2b9c1e-8a7d-4e6f-9b0a-1c2d3e4f5a6b failed twice")
    assert gated("[tools] Bash `npm test` exit 0")
    assert gated("The implementation is now complete")
    assert gated("qa validate passed 55/55"), "progress events are still refused"
    assert not gated("Tests must never write to /tmp/; use the tmp_path fixture instead")
    assert not gated("The server tests need Postgres on port 5433")
    assert not gated("Completing a checkout requires the payment webhook")


def test_knowledge_from_keeps_the_three_lessons_from_a_coding_excerpt():
    source = "\n\n".join(f"## {t['role']} · {t['at']}\n{t['content']}" for t in TURNS)
    result = knowledge_from(json.dumps(LESSONS), source)
    assert [c.category for c in result.items] == ["gotcha", "fact", "rule"]
    assert all(c.assertion == "observed" for c in result.items), "a tool line shows the result"
    assert result.dropped_gate == 3


@needs_db
@pytest.mark.asyncio
async def test_session_with_tool_outcomes_becomes_lessons(client):
    """End to end: the plugin's tool events are stored and rendered into the
    episode, extraction sees them, and only the lessons become memory."""
    from test_threads import Router

    from memvine_server.main import app
    from memvine_server.worker import drain

    patched = await client.patch(f"/v1/projects/{client.project_slug}", headers=MGMT,
                                 json={"description": "memvine: the hosted team memory server"})
    assert patched.status_code == 200
    router = Router()
    router.knowledge.responses = {"memvine: the hosted team memory server\nEpisode:": json.dumps(LESSONS),  # the knowledge prompt only
                                 "Previous state:": "{}"}  # the live session state pass

    r = await client.post("/v1/sessions/cap-1/events", json={"events": TURNS, "agent": "claude-code",
                                                              "label": "Claude Code · memvine · main"})
    assert r.status_code == 202, r.text
    assert (await client.post("/v1/sessions/cap-1/close")).status_code == 200
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, router, app.state.embedder, project_id)

    asked = next(u for s, u in router.knowledge.calls if s == KNOWLEDGE_SYSTEM and "Why does the export test fail" in u)
    assert "## tool · " in asked and "failed (exit 1): E psycopg.OperationalError" in asked, "tool lines reach extraction"
    jobs = [j for j in (await client.get("/v1/jobs")).json()["jobs"] if j["episode_id"].startswith("session:cap-1:")]
    assert jobs and all(j["status"] == "done" for j in jobs), jobs
    assert jobs[0]["result"]["dropped_gate"] == 3

    memories = (await client.get("/v1/memories")).json()
    assert sorted(m["category"] for m in memories) == ["fact", "gotcha", "rule"]
    assert not any("tmp" in m["statement"] or "complete" in m["statement"].lower() for m in memories)
    assert all(m["assertion"] == "observed" for m in memories)
