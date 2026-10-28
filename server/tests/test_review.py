"""The review loop (review.py): synthesis-made memories start `new` and a person keeps them; a person's
own start `kept`; forgetting or correcting a synthesis-made one records a private feedback case; and
synthesis never learns a forgotten memory again."""
from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from conftest import TEST_DSN, needs_db

from memvine_server.worker import drain

pytestmark = needs_db
MGMT = {"Authorization": "Bearer mv_mk_test"}

PADDING = "Assistant: Reading the deploy scripts and the runbook before changing anything. " * 25  # ~2000 chars
RULE_QUOTE = "Never run migrations from a laptop against the shared staging database"
EPISODE_1 = f"{PADDING}\nUser: {RULE_QUOTE}; use the migrate job in CI.\nAssistant: Understood.\n{PADDING}"
EPISODE_2 = "User: We switched the muse frontend to SvelteKit for smaller bundles. Assistant: Noted."
EPISODE_3 = "User: reminder again, never run migrations from a laptop against shared staging. Assistant: ok."

RULE = "Never run migrations from a laptop against the shared staging database; use the CI migrate job."
DECISION = "The muse frontend uses SvelteKit, chosen for smaller bundles."


def item(statement, quote, category="rule"):
    return {"statement": statement, "kind": "procedural" if category == "rule" else "semantic", "category": category,
            "subjects": ["muse"], "assertion": "stated", "confidence": "high", "quote": quote}


async def project_id(app, slug):
    async with app.state.pool.connection() as conn:
        return (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]


async def synthesize(client, app, episodes):
    llm = app.state.llm
    llm.responses = {
        "use the migrate job in CI": json.dumps([item(RULE, RULE_QUOTE)]),
        "switched the muse frontend": json.dumps([item(DECISION, "switched the muse frontend to SvelteKit", "decision")]),
        # The same rule again, worded exactly as before.
        "reminder again": json.dumps([item(RULE, "never run migrations from a laptop against shared staging")]),
        "NEW (": json.dumps({"match": None, "relation": "different", "reason": "distinct"}),
    }
    llm.default = "[]"
    r = await client.post("/v1/ingest", json={"items": episodes})
    assert r.status_code == 202, r.text
    await drain(app.state.pool, app.state.llm, app.state.embedder, await project_id(app, client.project_slug))
    jobs = (await client.get("/v1/jobs")).json()["jobs"]
    assert not [j for j in jobs if j["status"] == "failed"]
    return {j["episode_id"]: j["result"] for j in jobs}


def ep(id_, content, day="2026-10-20"):
    return {"id": id_, "content": content, "timestamp": f"{day}T10:00:00Z", "label": "Claude Code · muse"}


def by_statement(memories, prefix):
    return next(m for m in memories if m["statement"].startswith(prefix))


@pytest.mark.asyncio
async def test_review_state_filter_and_count(client):
    from memvine_server.main import app

    await synthesize(client, app, [ep("e1", EPISODE_1), ep("e2", EPISODE_2)])
    memories = (await client.get("/v1/memories")).json()
    assert len(memories) == 2 and all(m["review"] == "new" and m["origin"] == "synthesis" for m in memories)
    assert len((await client.get("/v1/memories", params={"review": "new"})).json()) == 2

    project = next(p for p in (await client.get("/v1/projects", headers=MGMT)).json() if p["slug"] == client.project_slug)
    assert project["review_count"] == 2

    # New items are served by recall all the same.
    r = (await client.post("/v1/recall", json={"query": "Which frontend framework does muse use?"})).json()
    assert any(i["memory"]["statement"] == DECISION for i in r["items"])

    # Keep (Still true) keeps it.
    decision = by_statement(memories, "The muse frontend")
    kept = (await client.patch(f"/v1/memories/{decision['id']}", json={"confirm": True})).json()
    assert kept["review"] == "kept"
    assert [m["statement"] for m in (await client.get("/v1/memories", params={"review": "new"})).json()] == [RULE]
    assert [m["statement"] for m in (await client.get("/v1/memories", params={"review": "kept"})).json()] == [DECISION]

    # Forgotten items leave the queue.
    rule = by_statement(memories, "Never run")
    await client.patch(f"/v1/memories/{rule['id']}", json={"status": "retired"})
    assert (await client.get("/v1/memories", params={"review": "new"})).json() == []
    project = next(p for p in (await client.get("/v1/projects", headers=MGMT)).json() if p["slug"] == client.project_slug)
    assert project["review_count"] == 0

    # A person's own memory starts kept.
    r = await client.post("/v1/memories", json={"statement": "Staging deploys need a green nightly run first.", "category": "rule"})
    assert r.status_code == 201 and r.json()["memory"]["review"] == "kept" and r.json()["memory"]["origin"] == "person"

    bad = await client.get("/v1/memories", params={"review": "maybe"})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_forget_records_a_feedback_case(client):
    from memvine_server.main import app

    await synthesize(client, app, [ep("e1", EPISODE_1), ep("e2", EPISODE_2)])
    rule = by_statement((await client.get("/v1/memories")).json(), "Never run")
    r = await client.patch(f"/v1/memories/{rule['id']}", json={"status": "retired"})
    assert r.json()["status"] == "retired"

    cases = (await client.get("/v1/feedback-cases")).json()["cases"]
    assert len(cases) == 1
    case = cases[0]
    assert case["kind"] == "forgotten" and case["statement"] == RULE and case["category"] == "rule"
    assert case["memory_id"] == rule["id"] and case["correction"] is None and case["episode_id"] == "e1"
    assert case["quotes"] == [{"episode_id": "e1", "quote": RULE_QUOTE}]
    assert RULE_QUOTE in case["excerpt"] and len(case["excerpt"]) <= 1500 < len(EPISODE_1)

    # Exported with the project.
    exported = (await client.post("/v1/export")).json()
    assert [c["id"] for c in exported["feedback_cases"]] == [case["id"]]

    # Restoring it takes the feedback back.
    await client.patch(f"/v1/memories/{rule['id']}", json={"status": "active"})
    assert (await client.get("/v1/feedback-cases")).json()["cases"] == []
    await client.patch(f"/v1/memories/{rule['id']}", json={"status": "retired"})
    assert len((await client.get("/v1/feedback-cases")).json()["cases"]) == 1

    # A person's own memory, forgotten, is not feedback for extraction.
    mine = (await client.post("/v1/memories", json={"statement": "Release notes go in CHANGELOG.md."})).json()["memory"]
    await client.patch(f"/v1/memories/{mine['id']}", json={"status": "retired"})
    assert len((await client.get("/v1/feedback-cases")).json()["cases"]) == 1

    # Deleting the source deletes the case quoting it.
    assert (await client.delete("/v1/episodes/e1")).status_code == 204
    assert (await client.get("/v1/feedback-cases")).json()["cases"] == []


@pytest.mark.asyncio
async def test_wrong_records_a_feedback_case(client):
    from memvine_server.main import app

    await synthesize(client, app, [ep("e2", EPISODE_2)])
    decision = by_statement((await client.get("/v1/memories")).json(), "The muse frontend")
    r = await client.post("/v1/memories", json={"statement": "The muse frontend uses Next.js.", "supersedes": decision["id"]})
    assert r.status_code == 201
    correction = r.json()["memory"]
    assert correction["review"] == "kept" and correction["origin"] == "person"

    case = (await client.get("/v1/feedback-cases")).json()["cases"][0]
    assert case["kind"] == "wrong" and case["statement"] == DECISION and case["correction"] == "The muse frontend uses Next.js."
    assert case["category"] == "decision" and "SvelteKit" in case["excerpt"]

    # Correcting a person's own memory is not extraction feedback.
    await client.post("/v1/memories", json={"statement": "The muse frontend uses Remix.", "supersedes": correction["id"]})
    assert len((await client.get("/v1/feedback-cases")).json()["cases"]) == 1


@pytest.mark.asyncio
async def test_synthesis_never_recreates_a_forgotten_memory(client):
    from memvine_server.main import app

    await synthesize(client, app, [ep("e1", EPISODE_1)])
    rule = by_statement((await client.get("/v1/memories")).json(), "Never run")
    await client.patch(f"/v1/memories/{rule['id']}", json={"status": "retired"})

    results = await synthesize(client, app, [ep("e3", EPISODE_3, "2026-10-22"), ep("e2", EPISODE_2, "2026-10-22")])
    assert results["e3"]["dropped_forgotten"] == 1 and results["e3"]["relations"] == {}
    assert results["e2"]["dropped_forgotten"] == 0
    active = [m["statement"] for m in (await client.get("/v1/memories", params={"status": "active"})).json()]
    assert active == [DECISION], "the forgotten rule did not come back"

    # Aging-style retirement (not a person) does not block anything: only forgotten_at does.
    from memvine_server.review import matches_forgotten

    vec = (await app.state.embedder.embed([RULE]))[0]
    async with app.state.pool.connection() as conn:
        pid = await project_id(app, client.project_slug)
        assert await matches_forgotten(conn, pid, RULE, vec)
        assert await matches_forgotten(conn, pid, "  never RUN migrations from a laptop against the shared staging database; use the CI migrate job. ",
                                       (await app.state.embedder.embed(["something else entirely"]))[0])
        await conn.execute("UPDATE memory_items SET forgotten_at = NULL WHERE id = %s", (rule["id"],))
        assert not await matches_forgotten(conn, pid, RULE, vec)


@pytest.mark.asyncio
async def test_feedback_cases_need_a_manager_and_stay_in_their_project(client):
    from memvine_server.main import app

    await synthesize(client, app, [ep("e2", EPISODE_2)])
    decision = by_statement((await client.get("/v1/memories")).json(), "The muse frontend")
    await client.patch(f"/v1/memories/{decision['id']}", json={"status": "retired"})

    other = f"t-{uuid.uuid4().hex[:8]}"
    await client.post("/v1/projects", headers=MGMT, json={"name": other, "slug": other})
    try:
        r = await client.get("/v1/feedback-cases", headers={**MGMT, "X-Memvine-Project": other})
        assert r.status_code == 200 and r.json()["cases"] == []
        r = await client.get("/v1/feedback-cases", headers={**MGMT, "X-Memvine-Project": client.project_slug})
        assert len(r.json()["cases"]) == 1
    finally:
        await client.delete(f"/v1/projects/{other}?confirm={other}", headers=MGMT)

    # Deleted with the project.
    pid = await project_id(app, client.project_slug)
    await client.delete(f"/v1/projects/{client.project_slug}?confirm={client.project_slug}", headers=MGMT)
    async with app.state.pool.connection() as conn:
        left = await (await conn.execute("SELECT count(*) AS n FROM feedback_cases WHERE project_id = %s", (pid,))).fetchone()
    assert left["n"] == 0


def test_feedback_export_cli(tmp_path):
    from typer.testing import CliRunner

    from memvine_server.cli import app as cli
    from memvine_server.review import excerpt_around

    slug = f"t-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        # The schema exists: an async test (or the app) applied it.
        pid = conn.execute("INSERT INTO projects (slug, name) VALUES (%s, %s) RETURNING id", (slug, slug)).fetchone()[0]
        conn.execute("INSERT INTO feedback_cases (project_id, kind, statement, category, quotes, excerpt) VALUES "
                     "(%s, 'forgotten', 'A forgotten fact.', 'fact', '[{\"episode_id\": \"x\", \"quote\": \"q\"}]', 'the excerpt')", (pid,))
        conn.execute("INSERT INTO feedback_cases (project_id, kind, statement, correction, excerpt) VALUES "
                     "(%s, 'wrong', 'A wrong fact.', 'The right fact.', 'another')", (pid,))
    try:
        out = tmp_path / "cases.jsonl"
        result = CliRunner().invoke(cli, ["feedback-export", slug, "--out", str(out)])
        assert result.exit_code == 0, result.output
        lines = [json.loads(line) for line in out.read_text().splitlines()]
        assert [c["kind"] for c in lines] == ["forgotten", "wrong"]
        assert lines[0]["project"] == slug and lines[0]["profile"] == slug and lines[0]["excerpt"] == "the excerpt" and lines[0]["quotes"][0]["quote"] == "q"
        assert lines[1]["correction"] == "The right fact."
        assert CliRunner().invoke(cli, ["feedback-export", "no-such-project"]).exit_code != 0
    finally:
        with psycopg.connect(TEST_DSN, autocommit=True) as conn:
            conn.execute("DELETE FROM projects WHERE slug = %s", (slug,))

    text = "a" * 1000 + "NEEDLE" + "b" * 1000
    cut = excerpt_around(text, "NEEDLE", 100)
    assert len(cut) == 100 and "NEEDLE" in cut
    assert excerpt_around(text, "missing", 50) == "a" * 50
    assert excerpt_around("short", "x") == "short"


@pytest.mark.asyncio
async def test_gate_replays_private_drop_cases(tmp_path, monkeypatch):
    """benchmark/knowledge/cases.py: a forgotten statement re-extracted from its excerpt fails the case."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("gate_cases", Path(__file__).parents[2] / "benchmark" / "knowledge" / "cases.py")
    cases = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cases)

    path = tmp_path / "drop-cases.jsonl"
    assert cases.load_private_cases(path) == [] and await cases.run_private_cases(path=path) == []
    path.write_text("\n".join(json.dumps(c) for c in [
        {"kind": "forgotten", "statement": "CI for PR 88 is green with 142 tests passing.", "excerpt": "Assistant: PR #88 CI is green: 142 passed."},
        {"kind": "wrong", "statement": "The deploy target is fly.io.", "excerpt": ""},  # excerpt expired: skipped
    ]) + "\n")
    assert len(cases.load_private_cases(path)) == 1
    assert cases.keyword_overlap("CI for PR 88 is green with 142 tests passing.", "PR 88: CI green, 142 tests passing") >= 0.7

    from memvine_server.extraction import llm as llm_module

    responses = {}
    make_llm = llm_module.make_llm

    def fake(backend=None):
        llm = make_llm("fake")
        llm.responses = responses
        return llm

    monkeypatch.setattr(llm_module, "make_llm", fake)
    [(name, ok, hits)] = await cases.run_private_cases(path=path)
    assert ok and not hits and name.startswith("drop (forgotten)")
    responses["PR #88"] = json.dumps([{**item("CI for PR #88 is green with 142 tests passing.", "PR #88 CI is green", "fact"), "kind": "episodic"}])
    [(_, ok, hits)] = await cases.run_private_cases(path=path)
    assert not ok and hits
