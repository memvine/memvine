"""Per-project memory instructions and custom categories (migration 0008): what reaches the knowledge
and thread-page prompts, how custom categories are accepted, the API (validation, the cap of five,
clearing), and synthesis storing a custom category. Whether a model obeys an instruction is checked
with a real model in benchmark/knowledge/cases.py."""
from __future__ import annotations

import json

import pytest
from conftest import needs_db

from memvine_server.extraction.extract import knowledge_from, resolve_category
from memvine_server.extraction.llm import FakeLLM
from memvine_server.extraction.prompts import KNOWLEDGE_SYSTEM, THREADS_SYSTEM, knowledge_user, threads_user

MGMT = {"Authorization": "Bearer mv_mk_test"}
INSTRUCTIONS = "Also keep: on-call runbook steps. Never keep: customer names."
CATEGORIES = [("runbook", "Runbook step", "What on-call does when an alert fires"),
              ("vendor", "Vendor", "")]
SOURCE = ("User: When the Northwind invoice queue alert fires, pause the nightly ledger sync in the admin panel before "
          "draining the queue. Also the exporter is called Lark.")


def item(statement, category, quote):
    return {"statement": statement, "category": category, "why": "on-call needs it", "thread_key": None, "scope": "project",
            "subjects": ["invoice queue"], "assertion": "stated", "confidence": "high", "valid_from": None, "valid_until": None,
            "standing": False, "quote": quote}


# ------------------------------------------------------------------ prompts


def test_knowledge_prompt_carries_instructions_and_categories_only_when_set():
    plain = knowledge_user("ep", "2026-10-20", SOURCE, "billing", [])
    assert "<project_instructions>" not in plain and "<extra_categories>" not in plain

    prompt = knowledge_user("ep", "2026-10-20", SOURCE, "billing", [], context="older memory",
                            instructions=f"  {INSTRUCTIONS}\n", categories=CATEGORIES)
    block = prompt[prompt.index("<project_instructions>"):prompt.index("</project_instructions>")]
    assert f"<instructions>\n{INSTRUCTIONS}\n</instructions>" in block
    assert "cannot switch off the three tests or the never-memory list" in block
    assert "secrets and credentials" in block and "run-specific trivia" in block and "descriptions of the code" in block
    assert "Never keep X" in block and "Also keep Y" in block
    assert "- runbook (Runbook step): What on-call does when an alert fires" in prompt
    assert "- vendor (Vendor)\n" in prompt
    # Order: instructions, categories, context, excerpt; the system rules are untouched.
    order = [prompt.index(t) for t in ("<project_instructions>", "<extra_categories>", "<context>", "<excerpt>")]
    assert order == sorted(order)
    assert "<project_instructions>" not in KNOWLEDGE_SYSTEM and "Never memory, however true" in KNOWLEDGE_SYSTEM
    assert "runbook" not in knowledge_user("ep", "2026-10-20", SOURCE, "billing", [], instructions="   ", categories=[])


def test_thread_pages_follow_only_what_the_instructions_leave_out():
    assert "<project_instructions>" not in threads_user("ep", "2026-10-20", SOURCE, "billing", [], [])
    prompt = threads_user("ep", "2026-10-20", SOURCE, "billing", [], [], instructions=INSTRUCTIONS)
    assert INSTRUCTIONS in prompt and "follow only what they say to leave out" in prompt
    assert prompt.index("<project_instructions>") < prompt.index("<excerpt>")


# ------------------------------------------------------------------ categories in extraction


def test_custom_category_is_accepted_and_an_unknown_one_falls_back_to_fact():
    custom = {"runbook": "runbook", "runbook step": "runbook", "runbook-step": "runbook"}
    assert resolve_category("Runbook", custom) == "runbook"
    assert resolve_category("runbook_step", custom) == "runbook"
    assert resolve_category("Gotcha", custom) == "gotcha"
    assert resolve_category("widget", custom) == "fact", "unknown with custom categories: the built-in fact"
    assert resolve_category("widget", {}) is None, "no custom categories: unknown is invalid, as before"
    assert resolve_category("", custom) is None

    raw = json.dumps([
        item("When the Northwind invoice queue alert fires, pause the nightly ledger sync before draining the queue.",
             "runbook", "pause the nightly ledger sync in the admin panel"),
        item("The Northwind exporter is called Lark.", "glossary", "the exporter is called Lark"),
        item("The nightly ledger sync can be paused in the admin panel.", "Runbook step", "nightly ledger sync in the admin panel"),
    ])
    result = knowledge_from(raw, SOURCE, CATEGORIES)
    assert [(c.category, c.kind) for c in result.items] == [("runbook", "semantic"), ("fact", "semantic"), ("runbook", "semantic")]
    assert result.dropped_invalid == 0

    # The same output for a project without custom categories: the custom and unknown ones are invalid.
    plain = knowledge_from(raw, SOURCE)
    assert plain.items == [] and plain.dropped_invalid == 3


# ------------------------------------------------------------------ API


@needs_db
@pytest.mark.asyncio
async def test_memory_instructions_are_set_cleared_and_bounded(client):
    slug = client.project_slug
    r = await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"memory_instructions": f"  {INSTRUCTIONS}  "})
    assert r.status_code == 200 and r.json()["memory_instructions"] == INSTRUCTIONS
    listed = next(p for p in (await client.get("/v1/projects", headers=MGMT)).json() if p["slug"] == slug)
    assert listed["memory_instructions"] == INSTRUCTIONS and listed["categories"] == []

    # Leaving the field out keeps it; another field changes alone.
    r = await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"description": "billing"})
    assert r.json()["memory_instructions"] == INSTRUCTIONS
    assert (await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"memory_instructions": "x" * 1501})).status_code == 422
    # Secrets are stripped on the way in, like any text that reaches a model.
    r = await client.patch(f"/v1/projects/{slug}", headers=MGMT,
                           json={"memory_instructions": "Never keep the key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"})
    assert "abcdefghijklmnop" not in r.json()["memory_instructions"]
    for clear in (None, "   "):
        r = await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"memory_instructions": clear})
        assert r.status_code == 200 and r.json()["memory_instructions"] is None

    # Each change is in the activity feed with lengths only, never the text; an unchanged value adds nothing.
    events = (await client.get("/v1/events?kind=project_settings")).json()["events"]
    details = [e["detail"] for e in reversed(events)]
    assert [(d["field"], d["before_len"], d["after_len"]) for d in details] == [
        ("memory_instructions", 0, len(INSTRUCTIONS)), ("memory_instructions", len(INSTRUCTIONS), details[1]["after_len"]),
        ("memory_instructions", details[1]["after_len"], 0)]
    assert all(e["memory_id"] is None and e["actor"].startswith("key:") for e in events)
    assert "on-call" not in json.dumps(events) and "Never keep" not in json.dumps(events)


@needs_db
@pytest.mark.asyncio
async def test_categories_are_validated_and_capped_at_five(client):
    base = f"/v1/projects/{client.project_slug}/categories"
    assert (await client.get(base, headers=MGMT)).json() == []
    for bad in ("x", "Runbook", "run_book", "a" * 25, "run book"):
        assert (await client.post(base, headers=MGMT, json={"slug": bad, "name": "Bad"})).status_code == 422, bad
    r = await client.post(base, headers=MGMT, json={"slug": "fact", "name": "Fact"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "category-builtin"
    assert (await client.post(base, headers=MGMT, json={"slug": "runbook", "name": "Runbook", "description": "d" * 301})).status_code == 422

    r = await client.post(base, headers=MGMT, json={"slug": "runbook", "name": " Runbook step ", "description": "On-call steps"})
    assert r.status_code == 201 and r.json()["name"] == "Runbook step"
    assert (await client.post(base, headers=MGMT, json={"slug": "runbook", "name": "Again"})).status_code == 409
    for slug in ("vendor", "glossary", "on-call", "ab"):
        assert (await client.post(base, headers=MGMT, json={"slug": slug, "name": slug.title()})).status_code == 201
    r = await client.post(base, headers=MGMT, json={"slug": "sixth", "name": "Sixth"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "category-limit"
    assert [c["slug"] for c in (await client.get(base, headers=MGMT)).json()] == ["runbook", "vendor", "glossary", "on-call", "ab"]

    r = await client.patch(f"{base}/runbook", headers=MGMT, json={"description": "What on-call does when an alert fires"})
    assert r.status_code == 200 and r.json()["name"] == "Runbook step" and r.json()["description"].startswith("What on-call")
    assert (await client.patch(f"{base}/nope", headers=MGMT, json={"name": "x"})).status_code == 404
    assert (await client.delete(f"{base}/ab", headers=MGMT)).status_code == 204
    assert (await client.delete(f"{base}/ab", headers=MGMT)).status_code == 404
    assert (await client.post(base, headers=MGMT, json={"slug": "sixth", "name": "Sixth"})).status_code == 201, "a freed place"
    project = next(p for p in (await client.get("/v1/projects", headers=MGMT)).json() if p["slug"] == client.project_slug)
    assert [c["slug"] for c in project["categories"]] == ["runbook", "vendor", "glossary", "on-call", "sixth"]
    events = (await client.get("/v1/events?kind=project_settings")).json()["events"]
    assert [(e["detail"]["action"], e["detail"]["slug"]) for e in reversed(events)] == [
        ("created", "runbook"), ("created", "vendor"), ("created", "glossary"), ("created", "on-call"), ("created", "ab"),
        ("updated", "runbook"), ("deleted", "ab"), ("created", "sixth")]

    # A project key administers nothing.
    assert (await client.post(base, json={"slug": "keyed", "name": "Keyed"})).status_code in (401, 403)


@needs_db
@pytest.mark.asyncio
async def test_synthesis_sends_the_blocks_and_stores_custom_categories(client):
    from memvine_server.main import app
    from memvine_server.worker import drain

    slug = client.project_slug
    assert (await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"memory_instructions": INSTRUCTIONS})).status_code == 200
    for slug_, name, description in CATEGORIES:
        assert (await client.post(f"/v1/projects/{slug}/categories", headers=MGMT,
                                  json={"slug": slug_, "name": name, "description": description})).status_code == 201

    llm = FakeLLM(responses={
        "NEW (": json.dumps({"match": None, "relation": "different", "reason": "new"}),
        "invoice queue alert fires": json.dumps([
            item("When the Northwind invoice queue alert fires, pause the nightly ledger sync before draining the queue.",
                 "runbook", "pause the nightly ledger sync in the admin panel"),
            item("The Northwind exporter is called Lark.", "glossary", "the exporter is called Lark"),
        ]),
    })
    llm.fast = llm
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]
    r = await client.post("/v1/ingest", json={"items": [{"id": "ep1", "content": SOURCE, "timestamp": "2026-10-20T09:00:00Z"}]})
    assert r.status_code == 202
    await drain(app.state.pool, llm, app.state.embedder, project_id)
    assert all(j["status"] == "done" for j in (await client.get("/v1/jobs")).json()["jobs"])

    knowledge = [u for s, u in llm.calls if s == KNOWLEDGE_SYSTEM]
    pages = [u for s, u in llm.calls if s == THREADS_SYSTEM]
    assert knowledge and INSTRUCTIONS in knowledge[0] and "- runbook (Runbook step)" in knowledge[0]
    assert pages and INSTRUCTIONS in pages[0] and "<extra_categories>" not in pages[0]

    memories = {m["category"]: m for m in (await client.get("/v1/memories")).json()}
    assert set(memories) == {"runbook", "fact"}, "the unknown 'glossary' fell back to fact"
    assert memories["runbook"]["kind"] == "semantic"
    filtered = (await client.get("/v1/memories?category=runbook")).json()
    assert [m["id"] for m in filtered] == [memories["runbook"]["id"]]

    # Stored directly: a custom category is accepted, an unknown one refused.
    r = await client.post("/v1/memories", json={"statement": "Northwind's ledger vendor is Quill.", "category": "vendor"})
    assert r.status_code == 201 and r.json()["memory"]["category"] == "vendor"
    r = await client.post("/v1/memories", json={"statement": "Something else entirely.", "category": "glossary"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "category-unknown"

    # Deleting a category leaves the memories filed under it; the export carries both settings.
    assert (await client.delete(f"/v1/projects/{slug}/categories/runbook", headers=MGMT)).status_code == 204
    assert (await client.get("/v1/memories?category=runbook")).json()[0]["id"] == memories["runbook"]["id"]
    exported = (await client.post("/v1/export", headers={**MGMT, "X-Memvine-Project": slug})).json()
    assert exported["project"]["memory_instructions"] == INSTRUCTIONS
    assert [c["slug"] for c in exported["categories"]] == ["vendor"]
