"""The memory type table's promises: a preference strengthens each time it is restated, and progress
on a thread page (State, Next steps) expires 30 days after its newest source."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from conftest import needs_db

from memvine_server.extraction.llm import FakeLLM
from memvine_server.extraction.prompts import RECONCILE_SYSTEM, THREADS_SYSTEM
from memvine_server.threads import expire_progress

MGMT = {"Authorization": "Bearer mv_mk_test"}
SAME = '{"match": 1, "relation": "same", "reason": "restates the preference"}'


# ------------------------------------------------------------------ preferences strengthen

@needs_db
@pytest.mark.asyncio
async def test_sample_preference_restated_twice_is_high_with_three_quotes(client):
    from memvine_server.main import app

    r = await client.post("/v1/sample", headers={**MGMT, "X-Memvine-Project": client.project_slug})
    assert r.status_code == 200 and r.json()["loaded"] is True
    pref = next(m for m in (await client.get("/v1/memories")).json() if m["kind"] == "preference")
    assert pref["statement"] == "Priya prefers async review over meetings."
    assert pref["confidence"] == "low" and len(pref["evidence"]) == 1, "said once: low"

    judge = FakeLLM(responses={"NEW (": SAME})
    llm, app.state.llm = app.state.llm, judge
    try:
        seen = []
        for statement in ["Priya prefers async review to meetings.",
                          "Priya still prefers async review instead of meetings.",
                          "Priya prefers async review, not meetings."]:
            r = await client.post("/v1/memories", json={"statement": statement, "kind": "preference", "subjects": ["priya"]})
            assert r.status_code == 201, r.text
            assert r.json()["relation"] == "same" and r.json()["memory"]["id"] == pref["id"]
            seen.append(r.json()["memory"])
    finally:
        app.state.llm = llm
    # The judge was asked about the preference, numbered first, each time.
    assert all(s == RECONCILE_SYSTEM and "EXISTING 1 (" in u and "Priya prefers async review over meetings." in u.split("\n")[0]
               for s, u in judge.calls)

    assert [m["confidence"] for m in seen] == ["medium", "high", "high"], "one step per restatement; high stays high"
    restated_twice = seen[1]
    assert len(restated_twice["evidence"]) == 3
    assert {e["quote"] for e in restated_twice["evidence"]} == {"I prefer async review over meetings.",
                                                               "Priya prefers async review to meetings.",
                                                               "Priya still prefers async review instead of meetings."}
    stored = (await client.get(f"/v1/memories/{pref['id']}")).json()
    assert stored["confidence"] == "high" and len(stored["evidence"]) == 4


@needs_db
@pytest.mark.asyncio
async def test_a_restated_fact_takes_high_confidence_at_once_but_never_drops(client):
    """Other kinds keep the old rule: a high-confidence restatement raises to high, a weaker one
    never lowers. Only preferences climb by repetition."""
    from memvine_server.main import app

    r = await client.post("/v1/memories", json={"statement": "The billing service runs on Postgres 16.", "subjects": ["billing"]})
    fact = r.json()["memory"]
    assert fact["confidence"] == "medium"
    judge = FakeLLM(responses={"NEW (": SAME})
    llm, app.state.llm = app.state.llm, judge
    try:
        r = await client.post("/v1/memories", json={"statement": "Billing runs on Postgres 16.", "subjects": ["billing"]})
    finally:
        app.state.llm = llm
    assert r.json()["relation"] == "same" and r.json()["memory"]["id"] == fact["id"]
    assert r.json()["memory"]["confidence"] == "medium", "a medium restatement of a fact leaves it as it was"


# ------------------------------------------------------------------ progress expires

def _item(text: str, day: str | None, *episodes: str) -> dict:
    return {"text": text, "date": day, "evidence": [{"episode_id": e, "quote": text} for e in episodes]}


def test_progress_items_expire_thirty_days_after_their_newest_source():
    at = datetime(2026, 12, 1, tzinfo=UTC)
    dates = {"old": datetime(2026, 10, 1, tzinfo=UTC), "edge": datetime(2026, 11, 1, tzinfo=UTC),
             "new": datetime(2026, 11, 30, tzinfo=UTC)}
    sections = [
        {"label": "Goal", "items": [_item("Ship 4.2.0", "2026-10-01", "old")]},
        {"label": "State", "items": [
            _item("Waiting on QA", "2026-10-01", "old"),
            _item("QA passed", "2026-11-30", "old", "new"),       # an old item a newer source restates
            _item("Freeze in place", "2026-11-01", "edge"),       # exactly 30 days: kept
            _item("Model re-dated it", "2026-11-30", "old"),      # the date is the model's; the source is old
        ]},
        {"label": "Next steps", "items": [_item("Tag the release", "2026-10-01", "old")]},
        {"label": "Done", "items": [_item("Cherry-picks in", "2026-10-01", "old")]},
        {"label": "Open questions", "items": [_item("Undated, unknown source", None, "gone")]},
    ]
    kept, dropped = expire_progress(sections, at, dates)
    assert dropped == 3
    assert [s["label"] for s in kept] == ["Goal", "State", "Done", "Open questions"], "an emptied section goes; others never expire"
    assert [i["text"] for i in kept[1]["items"]] == ["QA passed", "Freeze in place"]
    # With no known source, the item's own date decides; with neither, it stays.
    kept, dropped = expire_progress([{"label": "Next steps", "items": [_item("Old", "2026-09-01"), _item("Undated", None)]}], at, {})
    assert dropped == 1 and [i["text"] for i in kept[0]["items"]] == ["Undated"]


EP_OLD = ("## user · 2026-09-01T09:00\nWorking on the export task. Right now we are waiting on the vendor sandbox. "
          "Next we write the importer.")
EP_NEW = "## user · 2026-10-20T09:00\nBack on the export task: the goal is to export invoices as CSV."

PAGE_OLD = json.dumps({"threads": [{
    "key": "topic:export", "kind": "topic", "title": "Export", "status": "open", "summary": "Exporting invoices.",
    "sections": [
        {"label": "State", "items": [{"text": "Waiting on the vendor sandbox", "date": "2026-09-01",
                                      "evidence": [{"episode_id": None, "quote": "waiting on the vendor sandbox"}]}]},
        {"label": "Next steps", "items": [{"text": "Write the importer", "date": "2026-09-01",
                                           "evidence": [{"episode_id": None, "quote": "Next we write the importer"}]}]},
    ], "aliases": [], "related": []}]})
# The model keeps the old State and Next steps items unchanged, as it often does.
PAGE_NEW = json.dumps({"threads": [{
    "key": "topic:export", "kind": "topic", "title": "Export", "status": "open", "summary": "Exporting invoices as CSV.",
    "sections": [
        {"label": "Goal", "items": [{"text": "Export invoices as CSV", "date": "2026-10-20",
                                     "evidence": [{"episode_id": None, "quote": "the goal is to export invoices as CSV"}]}]},
        {"label": "State", "items": [{"text": "Waiting on the vendor sandbox", "date": "2026-10-20",
                                      "evidence": [{"episode_id": "x1", "quote": "waiting on the vendor sandbox"}]}]},
        {"label": "Next steps", "items": [{"text": "Write the importer", "date": "2026-09-01",
                                           "evidence": [{"episode_id": "x1", "quote": "Next we write the importer"}]}]},
    ], "aliases": [], "related": []}]})


@needs_db
@pytest.mark.asyncio
async def test_stale_state_and_next_steps_drop_at_the_next_rewrite(client):
    from memvine_server.main import app
    from memvine_server.worker import drain

    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]

    class Router:
        threads = FakeLLM(responses={"vendor sandbox. Next": PAGE_OLD, "export invoices as CSV": PAGE_NEW}, default='{"threads": []}')
        knowledge = FakeLLM(default="[]")

        async def complete(self, system, user, *, max_tokens=4096):
            return await (self.threads if system == THREADS_SYSTEM else self.knowledge).complete(system, user, max_tokens=max_tokens)

    router = Router()
    for ep_id, content, at in [("x1", EP_OLD, "2026-09-01T09:00:00Z"), ("x2", EP_NEW, "2026-10-20T09:00:00Z")]:
        r = await client.post("/v1/ingest", json={"items": [{"id": ep_id, "content": content, "timestamp": at, "label": "export"}]})
        assert r.status_code == 202
        await drain(app.state.pool, router, app.state.embedder, project_id)
        if ep_id == "x1":
            [thread] = (await client.get("/v1/threads")).json()
            first = (await client.get(f"/v1/threads/{thread['id']}")).json()
            assert [s["label"] for s in first["sections"]] == ["State", "Next steps"]

    detail = (await client.get(f"/v1/threads/{thread['id']}")).json()
    assert [s["label"] for s in detail["sections"]] == ["Goal"], "49-day-old progress is gone, whatever the model kept"
    jobs = {j["episode_id"]: j["result"] for j in (await client.get("/v1/jobs")).json()["jobs"]}
    assert jobs["x2"]["thread_items_expired"] == 2 and jobs["x1"]["thread_items_expired"] == 0
