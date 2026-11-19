"""Profiles (profiles.py): a short summary per person and per project, built by the fast model from
current memory. Only lines citing memories it was given survive; forgetting or superseding a memory
drops its line at once and marks the profile dirty; rebuilds are debounced; caps hold; the
session-start brief carries the profiles and per-prompt recall does not."""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from conftest import needs_db

from memvine_server import profiles
from memvine_server.worker import drain

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}


async def project_id(app, slug):
    async with app.state.pool.connection() as conn:
        return (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]


@contextmanager
def fake(app, responses: dict[str, str]):
    llm = app.state.llm
    saved, saved_default = dict(llm.responses), llm.default
    llm.responses = responses
    llm.default = "[]"
    try:
        yield llm
    finally:
        llm.responses, llm.default = saved, saved_default


async def numbers(app, pid, kind: str, subject: str) -> dict[str, str]:
    """statement -> its [m] number in the prompt the builder will send."""
    async with app.state.pool.connection() as conn:
        rows = await profiles.candidates(conn, pid, kind, subject)
    return {r["statement"]: f"m{n}" for n, r in enumerate(rows, 1)}


def cite(nums: dict[str, str], *fragments: str) -> list[str]:
    return [m for statement, m in nums.items() for f in fragments if f in statement]


async def build(client, app, pid, kind: str, subject: str, lines: list[dict]) -> dict:
    """Rebuild one profile with the model answering `lines`, and return it as served."""
    marker = f"PROFILE of person {subject}" if kind == "person" else "PROFILE of the project"
    with fake(app, {marker: json.dumps({"lines": lines})}):
        r = await client.post("/v1/profiles/rebuild", json={"subject_kind": kind, "subject": subject})
        assert r.status_code == 202, r.text
        await drain(app.state.pool, app.state.llm, app.state.embedder, pid)
    jobs = (await client.get("/v1/jobs")).json()["jobs"]
    assert not [j for j in jobs if j["status"] == "failed"], jobs
    r = await client.get("/v1/profiles", params={"subject_kind": kind, "subject": subject})
    assert r.status_code == 200, r.text
    return r.json()[0]


async def sample(client, app):
    assert (await client.post("/v1/sample")).status_code == 200
    return await project_id(app, client.project_slug)


@pytest.mark.asyncio
async def test_person_profile_keeps_only_cited_lines(client):
    from memvine_server.main import app

    pid = await sample(client, app)
    nums = await numbers(app, pid, "person", "Priya")
    assert any("async review" in s for s in nums), "her stated preference is a candidate"
    assert any("Only Priya merges" in s for s in nums), "a rule naming her is a candidate"
    profile = await build(client, app, pid, "person", "Priya", [
        {"text": "Prefers async review over meetings.", "cite": cite(nums, "async review")},
        {"text": "Only she merges release branches.", "cite": cite(nums, "Only Priya merges")},
        {"text": "Leads the payments team.", "cite": []},                                  # no citation
        {"text": "Owns the data warehouse.", "cite": ["m99"]},                             # unknown id
        {"text": "Prefers async review, and joined in 2019.", "cite": cite(nums, "async review")},  # a number no memory has
        {"text": "Never deploys on Fridays.", "cite": [*cite(nums, "Fridays"), "m77"]},     # one unknown among known
    ])
    assert [line["text"] for line in profile["lines"]] == ["Prefers async review over meetings.", "Only she merges release branches."]
    cited = (await client.get(f"/v1/memories/{profile['lines'][0]['memory_ids'][0]}")).json()
    assert "async review" in cited["statement"]
    assert profile["text"].startswith("=== memvine · about Priya ===\n- Prefers async review")
    assert not profile["dirty"] and profile["built_at"]
    # The model was asked with the fast model's prompt, numbered memories and no ids in the text.
    system, user = app.state.llm.calls[-1]
    assert system == profiles.PROFILE_SYSTEM and "[m1]" in user and "stated by this person" in user
    # The rebuild is in the activity feed.
    feed = (await client.get("/v1/events", params={"kind": "profile_rebuilt"})).json()["events"]
    assert feed and feed[0]["memory_id"] is None and feed[0]["actor"] == "system"
    assert feed[0]["detail"]["subject"] == "Priya" and feed[0]["detail"]["lines"] == 2


@pytest.mark.asyncio
async def test_forgetting_drops_the_line_marks_dirty_and_rebuilds_without_it(client):
    from memvine_server.main import app

    pid = await sample(client, app)
    nums = await numbers(app, pid, "person", "Priya")
    profile = await build(client, app, pid, "person", "Priya", [
        {"text": "Prefers async review over meetings.", "cite": cite(nums, "async review")},
        {"text": "Only she merges release branches.", "cite": cite(nums, "Only Priya merges")},
    ])
    forgotten = next(i for line in profile["lines"] if "async" in line["text"] for i in line["memory_ids"])
    assert (await client.patch(f"/v1/memories/{forgotten}", json={"status": "retired"})).status_code == 200

    # Served at once without it, before any rebuild.
    now = (await client.get("/v1/profiles", params={"subject_kind": "person", "subject": "Priya"})).json()[0]
    assert [line["text"] for line in now["lines"]] == ["Only she merges release branches."]
    # The sweep marks it dirty, but the debounce holds the rebuild for 10 minutes.
    async with app.state.pool.connection() as conn:
        await profiles.sweep(conn, force=True)
        row = await (await conn.execute("SELECT dirty FROM profiles WHERE project_id = %s AND subject = 'Priya'", (pid,))).fetchone()
        assert row["dirty"]
        assert not await (await conn.execute("SELECT 1 FROM jobs WHERE project_id = %s AND kind = 'profile' AND status = 'queued'",
                                             (pid,))).fetchone()
        await conn.execute("UPDATE profiles SET built_at = now() - interval '11 minutes' WHERE project_id = %s", (pid,))
        await profiles.sweep(conn, force=True)
        assert await (await conn.execute("SELECT 1 FROM jobs WHERE project_id = %s AND kind = 'profile' AND status = 'queued'",
                                         (pid,))).fetchone()
    nums = await numbers(app, pid, "person", "Priya")
    assert not cite(nums, "async review"), "a forgotten memory is never given to the model"
    with fake(app, {"PROFILE of person Priya": json.dumps({"lines": [
            {"text": "Only she merges release branches.", "cite": cite(nums, "Only Priya merges")}]})}):
        await drain(app.state.pool, app.state.llm, app.state.embedder, pid)
    rebuilt = (await client.get("/v1/profiles", params={"subject_kind": "person", "subject": "Priya"})).json()[0]
    assert [line["text"] for line in rebuilt["lines"]] == ["Only she merges release branches."] and not rebuilt["dirty"]


@pytest.mark.asyncio
async def test_a_superseded_value_is_replaced(client):
    from memvine_server.main import app

    pid = await sample(client, app)
    nums = await numbers(app, pid, "person", "Priya")
    profile = await build(client, app, pid, "person", "Priya", [
        {"text": "Prefers async review over meetings.", "cite": cite(nums, "async review")}])
    old = profile["lines"][0]["memory_ids"][0]
    r = await client.post("/v1/memories", json={"statement": "Priya now prefers a short weekly review meeting over async review.",
                                                "supersedes": old})
    assert r.status_code == 201, r.text
    new_id = r.json()["memory"]["id"]
    assert (await client.get("/v1/profiles", params={"subject_kind": "person", "subject": "Priya"})).json()[0]["lines"] == []
    async with app.state.pool.connection() as conn:
        await profiles.sweep(conn, force=True)
        assert (await (await conn.execute("SELECT dirty FROM profiles WHERE project_id = %s AND subject = 'Priya'",
                                          (pid,))).fetchone())["dirty"]
    nums = await numbers(app, pid, "person", "Priya")
    rebuilt = await build(client, app, pid, "person", "Priya", [
        {"text": "Prefers a short weekly review meeting over async review.", "cite": cite(nums, "weekly review meeting")}])
    assert [line["memory_ids"] for line in rebuilt["lines"]] == [[new_id]]
    assert "weekly review meeting" in rebuilt["text"]


@pytest.mark.asyncio
async def test_new_relevant_memory_marks_dirty_and_unrelated_memory_does_not(client):
    from memvine_server.main import app

    pid = await sample(client, app)
    nums = await numbers(app, pid, "person", "Priya")
    await build(client, app, pid, "person", "Priya", [
        {"text": "Prefers async review over meetings.", "cite": cite(nums, "async review")}])
    with fake(app, {"NEW (": json.dumps({"match": None, "relation": "different", "reason": "new"})}):
        assert (await client.post("/v1/memories", json={"statement": "The Northwind ledger export runs at 04:00 UTC."})).status_code == 201
    async with app.state.pool.connection() as conn:
        await profiles.sweep(conn, force=True)
        row = await (await conn.execute("SELECT dirty, built_from_event_id FROM profiles WHERE project_id = %s AND subject = 'Priya'",
                                        (pid,))).fetchone()
        assert not row["dirty"], "a memory that is not about Priya leaves her profile alone"
        latest = (await (await conn.execute("SELECT max(id) AS id FROM memory_events WHERE project_id = %s AND kind = 'created'",
                                            (pid,))).fetchone())["id"]
        assert row["built_from_event_id"] >= latest, "and the events it checked are not checked again"
    with fake(app, {"NEW (": json.dumps({"match": None, "relation": "different", "reason": "new"})}):
        assert (await client.post("/v1/memories", json={"statement": "Priya owns the Northwind billing on-call rota.",
                                                        "category": "fact"})).status_code == 201
    async with app.state.pool.connection() as conn:
        await profiles.sweep(conn, force=True)
        assert (await (await conn.execute("SELECT dirty FROM profiles WHERE project_id = %s AND subject = 'Priya'",
                                          (pid,))).fetchone())["dirty"]


@pytest.mark.asyncio
async def test_caps_and_stale_lines(client):
    from memvine_server.main import app

    pid = await sample(client, app)
    nums = await numbers(app, pid, "person", "Priya")
    every = list(nums.values())
    long = "Prefers async review over meetings and keeps every review written down so the whole team can read it later"
    profile = await build(client, app, pid, "person", "Priya", [
        {"text": f"{long} ({word}).", "cite": [every[0]]} for word in "abcdefghij"])
    assert len(profile["lines"]) <= profiles.PERSON_LINES
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute("SELECT token_estimate FROM profiles WHERE project_id = %s AND subject = 'Priya'",
                                        (pid,))).fetchone()
    assert row["token_estimate"] <= profiles.PERSON_TOKENS and len(profile["lines"]) < 10

    # A stale memory's line says it may be outdated.
    async with app.state.pool.connection() as conn:
        mid = (await (await conn.execute("SELECT id FROM memory_items WHERE project_id = %s AND statement LIKE '%%async review%%'",
                                         (pid,))).fetchone())["id"]
        await conn.execute("UPDATE memory_items SET status = 'stale' WHERE id = %s", (mid,))
    nums = await numbers(app, pid, "person", "Priya")
    profile = await build(client, app, pid, "person", "Priya", [
        {"text": "Prefers async review over meetings.", "cite": cite(nums, "async review")}])
    assert profile["lines"][0]["text"] == "Prefers async review over meetings. (may be outdated)"

    # The project profile: description first, lines within its own caps.
    assert (await client.patch(f"/v1/projects/{client.project_slug}", json={"description": "Northwind billing: invoices and refunds."},
                               headers=MGMT)).status_code == 200
    pnums = await numbers(app, pid, "project", "")
    assert cite(pnums, "Never deploy on Fridays"), "standing rules are project candidates"
    project = await build(client, app, pid, "project", "", [
        {"text": f"Rule {word}: never deploy on Fridays, so plan releases for earlier in the week.",
         "cite": cite(pnums, "Never deploy on Fridays")} for word in "abcdefghijkl"])
    assert project["text"].startswith("=== memvine · this project ===\n- Northwind billing: invoices and refunds.")
    assert 0 < len(project["lines"]) <= profiles.PROJECT_LINES - 1 and project["tokens"] <= profiles.PROJECT_TOKENS + 15
    assert (await client.get("/v1/profiles/project")).json()["description"] == "Northwind billing: invoices and refunds."


@pytest.mark.asyncio
async def test_the_session_start_brief_carries_profiles_and_prompt_recall_does_not(client):
    from memvine_server.main import app

    pid = await sample(client, app)
    nums = await numbers(app, pid, "person", "Priya")
    await build(client, app, pid, "person", "Priya", [{"text": "Prefers async review over meetings.", "cite": cite(nums, "async review")}])
    pnums = await numbers(app, pid, "project", "")
    await build(client, app, pid, "project", "", [
        {"text": "Never deploy on Fridays.", "cite": cite(pnums, "Never deploy on Fridays")},
        {"text": "Refunds over $500 need a human approver.", "cite": cite(pnums, "Refunds over $500")}])

    brief = (await client.post("/v1/recall", json={"query": "billing recent decisions, conventions and open work", "identity": "Priya",
                                                    "profiles": True, "budget": 1500})).json()
    assert brief["profiles"].startswith("=== memvine · about Priya ===\n- Prefers async review over meetings.")
    assert "=== memvine · this project ===\n- Refunds over $500 need a human approver." in brief["profiles"]
    # Nothing twice: the Friday rule is a standing rule, served in its own section, so the profile
    # line that only restates it is left out of the brief (the profile itself keeps it).
    assert "Never deploy on Fridays." in brief["standing"]
    assert "Fridays" not in brief["profiles"]
    assert "Never deploy on Fridays." in (await client.get("/v1/profiles/project")).json()["text"]
    assert 0 < brief["profile_tokens"] < 100 and brief["tokens"] + brief["profile_tokens"] <= 1500
    prompt = (await client.post("/v1/recall", json={"query": "when is the public launch?", "identity": "Priya"})).json()
    assert prompt["profiles"] == "" and "about Priya" not in prompt["briefing"]
    # Someone with no profile yet gets the project's; theirs is created to be built.
    other = (await client.post("/v1/recall", json={"query": "launch", "identity": "Sam", "profiles": True})).json()
    assert "about Sam" not in other["profiles"] and "this project" in other["profiles"]
    async with app.state.pool.connection() as conn:
        assert await (await conn.execute("SELECT dirty FROM profiles WHERE project_id = %s AND subject = 'Sam'", (pid,))).fetchone()

    # After a compaction: the session's state, then the profiles.
    r = await client.post("/v1/sessions/s-prof/events", json={"identity": "Priya", "events": [{"role": "user", "content": "hello"}]})
    assert r.status_code == 202
    with fake(app, {"New turns": json.dumps({"goal": "Ship the refund copy"})}):
        text = (await client.get("/v1/sessions/s-prof/brief")).json()["text"]
    assert text.startswith("=== memvine · this session's working state ===")
    assert "=== memvine · about Priya ===\n- Prefers async review" in text and "=== memvine · this project ===" in text
    assert text.count("Fridays") <= 1, "the session brief says the Friday rule once, not in the profile and again as standing"

    # Only managers rebuild; members read.
    assert (await client.post("/v1/profiles/rebuild", json={"subject_kind": "person"})).status_code == 400
    listed = (await client.get("/v1/profiles")).json()
    assert {(p["subject_kind"], p["subject"]) for p in listed} >= {("person", "Priya"), ("project", ""), ("person", "Sam")}


@pytest.mark.asyncio
async def test_profiles_leave_out_audience_restricted_memory(client):
    from memvine_server.main import app

    pid = await sample(client, app)
    assert (await client.put("/v1/audiences/leads", json={"kind": "fixed", "members": ["Priya"]})).status_code == 200
    with fake(app, {"NEW (": json.dumps({"match": None, "relation": "different", "reason": "new"})}):
        r = await client.post("/v1/memories", json={"statement": "Priya prefers to keep salary bands in the leads channel.",
                                                    "kind": "preference", "audience": "leads"})
    assert r.status_code == 201, r.text
    nums = await numbers(app, pid, "person", "Priya")
    assert not cite(nums, "salary bands"), "a profile is shown to anyone in the project"
