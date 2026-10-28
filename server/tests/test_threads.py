"""Threads and knowledge: thread identity, thread pages edited in place with verified evidence, the
knowledge gate, the threads API, and recall with thread pages."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from conftest import needs_db

from memvine_server.extraction.llm import FakeLLM
from memvine_server.extraction.prompts import KNOWLEDGE_SYSTEM, RECONCILE_SYSTEM, THREADS_SYSTEM
from memvine_server.threads import (
    MAX_ITEMS,
    branch_from_label,
    clean_sections,
    dedupe,
    derive_keys,
    item_words,
    merge_sections,
    normalize_key,
    release_of_branch,
    same_item,
    stable_kind,
    stable_title,
    strip_log_noise,
)

LABEL = "Claude Code · billing · release-billing-v4.2.0"
MGMT = {"Authorization": "Bearer mv_mk_test"}

EP1 = (
    "## user · 2026-10-20T09:00\nWe're cutting release 4.2.0 for billing this week. The goal is to ship the export fixes from PR #440. "
    "Open question: does INV-255 go in 4.2.0 or wait?\n\n"
    "## assistant · 2026-10-20T09:05\nCherry-picked 3fa91c0b7 as 8d2e45a10 onto release-billing-v4.2.0; staging dry-run validate passed 55/55.\n\n"
    "## user · 2026-10-20T09:10\nWe decided to keep the old CSV exporter as a fallback until release 4.3 because two customers still depend on it."
)
EP2 = (
    "## user · 2026-10-22T16:00\nINV-255 stays out of 4.2.0: its migration needs a data backfill, so it moves to 4.2.1.\n\n"
    "## assistant · 2026-10-22T16:20\nDone: 4.2.0 is tagged and shipped to production."
)

THREAD_1 = json.dumps({"threads": [{
    "key": "release:4.2.0", "kind": "release", "title": "Release 4.2.0 (billing)", "status": "open",
    "summary": "Release 4.2.0 for billing is being cut. Whether INV-255 goes in is still open.",
    "sections": [
        # Out of vocabulary order on purpose: the server orders sections.
        {"label": "Open questions", "items": [{"text": "Does INV-255 go in 4.2.0?", "date": "2026-10-20",
                                               "evidence": [{"episode_id": None, "quote": "does INV-255 go in 4.2.0 or wait?"}]}]},
        {"label": "Goal", "items": [{"text": "Ship the export fixes from PR #440", "date": "2026-10-20",
                                     "evidence": [{"episode_id": None, "quote": "ship the export fixes from PR #440"}]}]},
        {"label": "Decisions", "items": [{"text": "A decision the excerpt never made", "date": None,
                                          "evidence": [{"episode_id": None, "quote": "this sentence is not in the episode"}]}]},
        {"label": "Progress log", "items": [{"text": "not a vocabulary label", "evidence": [{"quote": "staging dry-run validate passed 55/55"}]}]},
    ],
    "aliases": [], "related": ["pr:440"]}]})

THREAD_2 = json.dumps({"threads": [{
    "key": "release:4.2.0", "kind": "release", "title": "Release 4.2.0 (billing)", "status": "done",
    "summary": "Release 4.2.0 shipped to production on 22 Oct 2026. INV-255 was left out and moves to 4.2.1.",
    "sections": [
        {"label": "Goal", "items": [{"text": "Ship the export fixes from PR #440", "date": "2026-10-20",
                                     "evidence": [{"episode_id": "ep1", "quote": "ship the export fixes from PR #440"}]}]},
        {"label": "Done", "items": [
            {"text": "INV-255 left out of 4.2.0; it moves to 4.2.1 because its migration needs a data backfill", "date": "2026-10-22",
             "evidence": [{"episode_id": None, "quote": "its migration needs a data backfill, so it moves to 4.2.1"}]},
            # Claims to be kept from the old page, but the old page never had this quote.
            {"text": "Forged old item", "date": "2026-10-20", "evidence": [{"episode_id": "ep1", "quote": "We're cutting release 4.2.0"}]},
        ]},
        {"label": "Outcome", "items": [{"text": "Tagged and shipped to production", "date": "2026-10-22",
                                        "evidence": [{"episode_id": None, "quote": "4.2.0 is tagged and shipped to production"}]}]},
    ],
    "aliases": ["ticket:INV-255"], "related": []}]})

KNOWLEDGE_1 = json.dumps([
    {"statement": "The old CSV exporter stays as a fallback until release 4.3 because two customers still depend on it.",
     "category": "decision", "why": "two customers still depend on it", "thread_key": "release:4.2.0", "subjects": ["csv exporter"],
     "assertion": "stated", "confidence": "high", "valid_from": "2026-10-20",
     "quote": "keep the old CSV exporter as a fallback until release 4.3"},
    # A progress event the model mislabelled as a fact: the gate drops it.
    {"statement": "Cherry-picked 3fa91c0b7 as 8d2e45a10 onto release-billing-v4.2.0; staging dry-run validate passed 55/55.",
     "category": "fact", "why": "release progress", "thread_key": "release:4.2.0", "subjects": ["release 4.2.0"],
     "assertion": "stated", "confidence": "high", "quote": "staging dry-run validate passed 55/55"},
    {"statement": "The release branch was pushed.", "category": "progress", "why": "", "thread_key": None, "subjects": [],
     "assertion": "stated", "confidence": "medium", "quote": "onto release-billing-v4.2.0"},
])


@dataclass
class Router:
    """Two keyed fakes, one per synthesis step, chosen by system prompt (both see the same excerpt)."""
    threads: FakeLLM = field(default_factory=lambda: FakeLLM(default='{"threads": []}'))
    knowledge: FakeLLM = field(default_factory=lambda: FakeLLM(default="[]"))

    async def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        target = self.threads if system == THREADS_SYSTEM else self.knowledge
        return await target.complete(system, user, max_tokens=max_tokens)


# ------------------------------------------------------------------ pure


def test_thread_keys_come_from_labels_prs_tickets_and_releases():
    assert branch_from_label(LABEL) == "release-billing-v4.2.0"
    assert branch_from_label(LABEL + " (imported)") == "release-billing-v4.2.0"
    for trivial in ("main", "master", "HEAD", "develop"):
        assert branch_from_label(f"Claude Code · billing · {trivial}") is None
    assert branch_from_label("Claude Code · main") is None and branch_from_label(None) is None

    text = ("Fixes in PR #440, pull request #12 and https://github.com/o/r/pull/99; see INV-255 (not UTF-8 or SHA-256). "
            "Release 4.2.0 is next; numpy 1.26.4 is installed; v4.2.1 later.")
    assert derive_keys(LABEL, text) == ["release:4.2.0", "branch:release-billing-v4.2.0", "pr:440", "pr:12", "pr:99",
                                        "ticket:INV-255", "release:4.2.1"]
    assert derive_keys("Claude Code · billing · main", "PR 7") == ["pr:7"]
    assert derive_keys(None, "nothing to see, version 2 of the doc") == []

    assert normalize_key("PR:#440") == "pr:440"
    assert normalize_key("release:v4.2.0") == "release:4.2.0"
    assert normalize_key("ticket:inv-255") == "ticket:INV-255"
    assert normalize_key("Export retries") == "topic:export-retries"
    assert normalize_key("branch:feature/x") == "branch:feature/x"


def test_sections_keep_order_cap_and_only_verified_evidence():
    source = "line one is here. line two is here."
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]
    items = [{"text": f"{w} item", "date": f"2026-10-{i + 1:02d}", "evidence": [{"quote": "line one is here"}]}
             for i, w in enumerate(words)]
    old = [{"label": "Goal", "items": [{"text": "g", "evidence": [{"episode_id": "e0", "quote": "an older quote"}]}]}]
    sections, dropped = clean_sections([
        {"label": "contains", "items": items},
        {"label": "Goal", "items": [{"text": "kept", "evidence": [{"episode_id": "e0", "quote": "an older quote"}]},
                                    {"text": "forged", "evidence": [{"episode_id": "e0", "quote": "never said"}]}]},
    ], source, "e1", old)
    assert [s["label"] for s in sections] == ["Goal", "Contains"]
    assert [i["text"] for i in sections[0]["items"]] == ["kept"] and dropped == 1
    assert len(sections[1]["items"]) == MAX_ITEMS and sections[1]["items"][0]["text"] == "charlie item", "the newest eight stay"
    assert sections[1]["items"][0]["evidence"] == [{"episode_id": "e1", "quote": "line one is here"}]


def _item(text, date=None, episode="e1", quote=None):
    return {"text": text, "date": date, "evidence": [{"episode_id": episode, "quote": quote or text[:40]}]}


def test_near_duplicates_merge_keeping_the_newer_with_pooled_evidence():
    # The three duplicate pairs a real release page grew (the last pair paraphrases two "regular PR" decisions).
    pairs = [
        ("Whether the failing lint gate blocks the merge of #454 depends on master's branch protection.",
         "Does the failing lint check block the merge? It depends on master's branch protection."),
        ("The user merges release PR #454 into master, which releases 4.2.0.", "The user merges #454 into master."),
        ("The release PR is a regular (non-draft) PR.", "Open release PR #454 as a regular (non-draft) PR."),
    ]
    for older, newer in pairs:
        assert same_item(item_words(older), item_words(newer)), (older, newer)
        merged = dedupe([_item(older, "2026-10-20", "e1"), _item("Something else entirely about QA", None), _item(newer, "2026-10-21", "e2")])
        assert [i["text"] for i in merged] == [newer, "Something else entirely about QA"], "the newer wins, in the first's place"
        assert [ev["episode_id"] for ev in merged[0]["evidence"]] == ["e2", "e1"], "evidence pooled, newer first"
    # Same date: the later one in the section wins.
    assert [i["text"] for i in dedupe([_item(pairs[1][1], "2026-10-21"), _item(pairs[1][0], "2026-10-21")])] == [pairs[1][0]]

    # Two different PRs' fixes are two items, however alike the wording.
    a = "The fix for #445 is cherry-picked onto the release branch."
    b = "The fix for PR 446 is cherry-picked onto the release branch."
    assert not same_item(item_words(a), item_words(b))
    assert len(dedupe([_item(a), _item(b)])) == 2
    assert item_words("PR #454 passed at 6ae3f0c84: 132/132 checks") >= {"#454", "6ae3f0c84", "passed"}
    assert "132" not in item_words("132/132 checks"), "bare numbers are not words"


def test_state_keeps_three_newest_lines_newest_first():
    source = "QA passed. Waiting on merge. Tag pushed. Docs built. Branch cut."
    states = [_item(t, f"2026-10-{d}", quote=t) for t, d in
              [("QA passed", "20"), ("Waiting on merge", "23"), ("Tag pushed", "22"), ("Docs built", "21"), ("Branch cut", "19")]]
    nexts = [_item(f"{w} step", "2026-10-20", quote="Branch cut") for w in ["alpha", "bravo", "charlie", "delta", "echo"]]
    sections, _ = clean_sections([{"label": "State", "items": states}, {"label": "Next steps", "items": nexts}], source, "e1", [])
    assert [i["text"] for i in sections[0]["items"]] == ["Waiting on merge", "Tag pushed", "Docs built"]
    assert len(sections[1]["items"]) == 5, "other sections keep up to eight"
    merged = merge_sections(sections, [{"label": "State", "items": [_item("Released", "2026-10-24")]}])
    assert [i["text"] for i in merged[0]["items"]] == ["Released", "Waiting on merge", "Tag pushed"]


def test_the_key_fixes_kind_and_release_titles():
    assert stable_kind("release:4.2.0", "pr", "release") == "release"
    assert stable_kind("branch:feature/x", "topic", None) == "task"
    assert stable_kind("pr:454", "release", None) == "pr" and stable_kind("ticket:INV-1", "task", None) == "ticket"
    assert stable_kind("topic:retries", "task", "topic") == "task" and stable_kind("topic:retries", "bogus", "task") == "task"

    assert stable_title("release:4.2.0", "PR #454: Release 4.2.0 to master", "Release 4.2.0 (billing)", False) == "Release 4.2.0 (billing)"
    assert stable_title("release:4.2.0", "PR #454: Release 4.2.0 to master", "Release 4.2.0 (billing)", True) == "Release 4.2.0"
    assert stable_title("release:4.2.0", "Release 4.2.0 (billing)", None, False) == "Release 4.2.0 (billing)"
    assert stable_title("release:4.2.0", "Ship it", None, False) == "Release 4.2.0"
    assert stable_title("release:4.2.0", "Release 4.2.0 (billing)", "PR #454: drifted", False) == "Release 4.2.0", "a drifted title heals"
    assert stable_title("pr:454", "Release 4.2.0 to master", "Release PR for 4.2.0", False) == "Release PR for 4.2.0"
    assert stable_title("pr:454", "Release PR #454, re-scoped to 4.2.1", "Release PR for 4.2.0", True) == "Release PR #454, re-scoped to 4.2.1"
    assert stable_title("topic:retries", "Export retries", None, False) == "Export retries"


# ------------------------------------------------------------------ database


async def _project_id(app, slug):
    async with app.state.pool.connection() as conn:
        return (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]


async def setup_release(client, *, thread_2: str = THREAD_2, knowledge_1: str = KNOWLEDGE_1, extra: tuple = ()) -> Router:
    """A project with two episodes of release 4.2.0 work extracted in order, then any `extra`
    (episode id, content, timestamp, label, thread output[, knowledge output]) after them."""
    from memvine_server.main import app
    from memvine_server.worker import drain

    patched = await client.patch(f"/v1/projects/{client.project_slug}", headers=MGMT,
                                 json={"description": "billing: the Northwind billing service and its releases"})
    assert patched.status_code == 200 and "memory_model" not in patched.json()
    assert patched.json()["description"].startswith("billing:")

    router = Router()
    router.threads.responses = {"INV-255 stays out": thread_2, "We're cutting release 4.2.0": THREAD_1}
    router.knowledge.responses = {"INV-255 stays out": "[]", "We're cutting release 4.2.0": knowledge_1}
    project_id = await _project_id(app, client.project_slug)
    episodes = [("ep1", EP1, "2026-10-20T09:00:00Z", LABEL), ("ep2", EP2, "2026-10-22T16:00:00Z", LABEL)]
    for ep_id, content, at, label, output, *knowledge in extra:
        router.threads.responses[content[-40:]] = output
        if knowledge:
            router.knowledge.responses[content[-40:]] = knowledge[0]
        episodes.append((ep_id, content, at, label))
    for ep_id, content, at, label in episodes:
        r = await client.post("/v1/ingest", json={"items": [{"id": ep_id, "content": content, "timestamp": at, "label": label}]})
        assert r.status_code == 202
        await drain(app.state.pool, router, app.state.embedder, project_id)
    jobs = (await client.get("/v1/jobs")).json()["jobs"]
    assert all(j["status"] == "done" for j in jobs), jobs
    return router


@needs_db
@pytest.mark.asyncio
async def test_thread_page_is_edited_in_place_and_knowledge_is_gated(client):
    router = await setup_release(client)

    first = next(u for _, u in router.threads.calls if "We're cutting" in u)
    assert "Candidate keys: release:4.2.0, branch:release-billing-v4.2.0, pr:440, ticket:INV-255" in first
    assert "(no existing page matches)" in first
    second = next(u for _, u in router.threads.calls if "INV-255 stays out" in u)
    assert '"key": "release:4.2.0"' in second and "does INV-255 go in 4.2.0 or wait?" in second, "the old page is shown"
    knowledge_prompt = next(u for _, u in router.knowledge.calls if "We're cutting" in u)
    assert "billing: the Northwind billing service" in knowledge_prompt and "- release:4.2.0: Release 4.2.0 (billing)" in knowledge_prompt

    threads = (await client.get("/v1/threads")).json()
    assert len(threads) == 1, "one page, updated in place"
    detail = (await client.get(f"/v1/threads/{threads[0]['id']}")).json()
    assert detail["status"] == "done" and detail["summary"].startswith("Release 4.2.0 shipped")
    labels = [s["label"] for s in detail["sections"]]
    assert labels == ["Goal", "Done", "Outcome"], "open question resolved into Done; vocabulary order"
    goal = detail["sections"][0]["items"][0]
    assert goal["evidence"] == [{"episode_id": "ep1", "quote": "ship the export fixes from PR #440"}], "kept evidence survives"
    done = [i["text"] for i in detail["sections"][1]["items"]]
    assert done == ["INV-255 left out of 4.2.0; it moves to 4.2.1 because its migration needs a data backfill"], "forged item dropped"
    assert detail["sections"][1]["items"][0]["evidence"][0]["episode_id"] == "ep2"
    assert "branch:release-billing-v4.2.0" in detail["aliases"] and "ticket:INV-255" in detail["aliases"]
    assert [s["episode_id"] for s in detail["sources"]] == ["ep1", "ep2"]
    assert detail["first_seen_at"].startswith("2026-10-20") and detail["last_activity_at"].startswith("2026-10-22")

    jobs = {j["episode_id"]: j["result"] for j in (await client.get("/v1/jobs")).json()["jobs"]}
    assert jobs["ep1"]["thread_items_dropped_no_quote"] == 1 and jobs["ep2"]["thread_items_dropped_no_quote"] == 1
    assert jobs["ep1"]["dropped_gate"] == 1 and jobs["ep1"]["dropped_invalid"] == 1

    memories = (await client.get("/v1/memories")).json()
    assert [m["category"] for m in memories] == ["decision"], "the decision stays, progress events do not"
    decision = memories[0]
    assert decision["kind"] == "semantic" and decision["why"] == "two customers still depend on it"
    assert decision["thread_id"] == detail["id"]
    assert (await client.get("/v1/memories?category=decision")).json()[0]["id"] == decision["id"]
    assert (await client.get("/v1/memories?category=rule")).json() == []


@needs_db
@pytest.mark.asyncio
async def test_threads_api_shapes(client):
    await setup_release(client)
    listing = (await client.get("/v1/threads")).json()
    assert set(listing[0]) == {"id", "kind", "key", "title", "status", "summary", "first_seen_at", "last_activity_at",
                               "session_count", "knowledge_count"}
    t = listing[0]
    assert (t["kind"], t["key"], t["title"], t["status"]) == ("release", "release:4.2.0", "Release 4.2.0 (billing)", "done")
    assert t["session_count"] == 2 and t["knowledge_count"] == 1
    assert (await client.get("/v1/threads?status=done&kind=release&q=4.2.0")).json()[0]["id"] == t["id"]
    assert (await client.get("/v1/threads?status=open")).json() == []
    assert (await client.get("/v1/threads?q=giraffe")).json() == []

    detail = (await client.get(f"/v1/threads/{t['id']}")).json()
    assert set(detail) == set(t) | {"aliases", "sections", "related", "knowledge", "sources"}
    assert set(detail["sections"][0]) == {"label", "items"}
    assert set(detail["sections"][0]["items"][0]) == {"text", "date", "evidence"}
    assert set(detail["sources"][0]) == {"episode_id", "label", "occurred_at"} and detail["sources"][0]["label"] == LABEL
    assert detail["related"] == []
    assert detail["knowledge"][0]["category"] == "decision" and {"why", "thread_id", "evidence"} <= set(detail["knowledge"][0])
    missing = await client.get("/v1/threads/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404

    # Deleting a source takes its evidence off the page; deleting the last one removes the thread.
    mgmt = {**MGMT, "X-Memvine-Project": client.project_slug}
    assert (await client.delete("/v1/episodes/ep2", headers=mgmt)).status_code == 204
    after = (await client.get(f"/v1/threads/{t['id']}")).json()
    assert [s["label"] for s in after["sections"]] == ["Goal"] and [s["episode_id"] for s in after["sources"]] == ["ep1"]
    assert (await client.delete("/v1/episodes/ep1", headers=mgmt)).status_code == 204
    assert (await client.get("/v1/threads")).json() == []


@needs_db
@pytest.mark.asyncio
async def test_recall_brings_the_thread_page(client):
    await setup_release(client)
    r = (await client.post("/v1/recall", json={"query": "What is the status of release 4.2.0?"})).json()
    assert not r["abstained"]
    assert [t["key"] for t in r["threads"]] == ["release:4.2.0"] and "pages" not in r
    briefing = r["briefing"]
    assert "### Release 4.2.0 (billing) [done] (release:4.2.0)" in briefing
    assert "Outcome:\n- Tagged and shipped to production (22 Oct)" in briefing
    assert "[decision 20 Oct] The old CSV exporter stays as a fallback" in briefing
    assert briefing.index("### Release 4.2.0") < briefing.index("[decision 20 Oct]")

    nothing = (await client.post("/v1/recall", json={"query": "quantum giraffe weather"})).json()
    assert nothing["abstained"] and nothing["threads"] == []
    early = (await client.post("/v1/recall", json={"query": "release 4.2.0", "as_of": "2026-10-21T00:00:00Z"})).json()
    assert early["threads"] == [], "the page knows what happened after as_of"


# The release PR episode tries to retitle the release page after the PR and restate items.
_drift = json.loads(THREAD_2)["threads"][0]
THREAD_2_DRIFT = json.dumps({"threads": [{
    **_drift, "kind": "pr", "title": "PR #454: Release 4.2.0 to master",
    "sections": [
        *_drift["sections"],
        {"label": "State", "items": [
            {"text": f"State line {word}", "date": f"2026-10-{day}", "evidence": [{"episode_id": None, "quote": quote}]}
            for word, day, quote in [("alpha", "18", "INV-255 stays out of 4.2.0"), ("bravo", "22", "4.2.0 is tagged"),
                                     ("charlie", "19", "shipped to production"), ("delta", "21", "needs a data backfill"),
                                     ("echo", "20", "it moves to 4.2.1")]]},
        {"label": "Next steps", "items": [
            {"text": "INV-255 moves to 4.2.1.", "date": "2026-10-22", "evidence": [{"episode_id": None, "quote": "it moves to 4.2.1"}]},
            {"text": "INV-255 moves to release 4.2.1.", "date": "2026-10-22",
             "evidence": [{"episode_id": None, "quote": "INV-255 stays out of 4.2.0"}]},
        ]},
    ]}]})


@needs_db
@pytest.mark.asyncio
async def test_page_keeps_its_identity_and_tidies_sections(client):
    await setup_release(client, thread_2=THREAD_2_DRIFT)
    [t] = (await client.get("/v1/threads")).json()
    assert (t["key"], t["kind"], t["title"]) == ("release:4.2.0", "release", "Release 4.2.0 (billing)"), "the key decides"
    detail = (await client.get(f"/v1/threads/{t['id']}")).json()
    sections = {s["label"]: s["items"] for s in detail["sections"]}
    assert [i["text"] for i in sections["State"]] == ["State line bravo", "State line delta", "State line echo"]
    [step] = sections["Next steps"]
    assert step["text"] == "INV-255 moves to release 4.2.1."
    assert [ev["quote"] for ev in step["evidence"]] == ["INV-255 stays out of 4.2.0", "it moves to 4.2.1"]


KNOWLEDGE_SCOPED = json.dumps([
    {**json.loads(KNOWLEDGE_1)[0], "scope": "project"},
    {"statement": "Release 4.2.0 for billing was cut in the week of 20 Oct 2026.", "category": "fact", "why": "dates the release",
     "thread_key": "release:4.2.0", "scope": "thread", "subjects": ["release 4.2.0"], "assertion": "stated", "confidence": "high",
     "quote": "We're cutting release 4.2.0 for billing this week"},
    # Thread-scoped without a thread: it becomes project knowledge.
    {"statement": "Whether INV-255 belongs in billing 4.2.0 was undecided on 20 Oct 2026.", "category": "fact", "why": "scoping",
     "thread_key": None, "scope": "thread", "subjects": ["inv-255"], "assertion": "stated", "confidence": "medium",
     "quote": "does INV-255 go in 4.2.0 or wait?"},
])


@needs_db
@pytest.mark.asyncio
async def test_thread_scoped_knowledge_is_stored_filtered_and_recalled_with_its_page(client):
    await setup_release(client, knowledge_1=KNOWLEDGE_SCOPED)
    by_scope = {s: (await client.get(f"/v1/memories?scope={s}")).json() for s in ("project", "thread")}
    assert [m["statement"] for m in by_scope["thread"]] == ["Release 4.2.0 for billing was cut in the week of 20 Oct 2026."]
    assert sorted(m["category"] for m in by_scope["project"]) == ["decision", "fact"]
    assert {m["scope"] for m in by_scope["project"]} == {"project"}
    assert (await client.get("/v1/memories?scope=team")).status_code == 422
    thread_item = by_scope["thread"][0]

    [t] = (await client.get("/v1/threads")).json()
    knowledge = (await client.get(f"/v1/threads/{t['id']}")).json()["knowledge"]
    assert {k["scope"] for k in knowledge} == {"project", "thread"} and thread_item["id"] in [k["id"] for k in knowledge]

    r = (await client.post("/v1/recall", json={"query": "What is the status of release 4.2.0?"})).json()
    briefing = r["briefing"]
    assert "Known in this thread:\n- [fact 20 Oct] Release 4.2.0 for billing was cut in the week of 20 Oct 2026" in briefing
    assert briefing.index("### Release 4.2.0") < briefing.index("Known in this thread:")
    assert briefing.count("Release 4.2.0 for billing was cut") == 1
    assert thread_item["id"] in [i["memory"]["id"] for i in r["items"]]

    # Without its page (written after as_of), thread knowledge stays out even when the words match.
    early = (await client.post("/v1/recall", json={"query": "release 4.2.0 for billing was cut this week",
                                                   "as_of": "2026-10-21T00:00:00Z"})).json()
    assert early["threads"] == [] and "Release 4.2.0 for billing was cut" not in early["briefing"]
    assert thread_item["id"] not in [i["memory"]["id"] for i in early["items"]]


EP3 = "## user · 2026-10-23T10:00\nThe csv-exporter retirement: we remove the old CSV exporter once both customers have moved off it."
THREAD_CSV = json.dumps({"threads": [{
    "key": "topic:csv-exporter", "kind": "task", "title": "CSV exporter retirement", "status": "open",
    "summary": "The old CSV exporter goes once both customers have moved off it.",
    "sections": [{"label": "Goal", "items": [{"text": "Remove the old CSV exporter once both customers moved off it", "date": "2026-10-23",
                                              "evidence": [{"episode_id": None, "quote": "we remove the old CSV exporter"}]}]}],
    "aliases": [], "related": []}]})


@needs_db
@pytest.mark.asyncio
async def test_recall_puts_the_callers_branch_thread_first(client):
    await setup_release(client, extra=(("ep3", EP3, "2026-10-23T10:00:00Z", "Claude Code · billing · feature/csv", THREAD_CSV),))
    threads = {t["key"]: t for t in (await client.get("/v1/threads")).json()}
    assert set(threads) == {"release:4.2.0", "topic:csv-exporter"}
    assert threads["topic:csv-exporter"]["kind"] == "task", "a topic's kind is the model's"

    async def keys(**extra):
        r = (await client.post("/v1/recall", json={"query": "What's left on csv-exporter?", **extra})).json()
        return [t["key"] for t in r["threads"]]

    assert (await keys())[0] == "topic:csv-exporter"
    assert await keys(branch="release-billing-v4.2.0") == ["release:4.2.0", "topic:csv-exporter"]
    assert (await keys(branch="main"))[0] == "topic:csv-exporter", "trunk names no work"
    assert (await keys(branch="feature/csv"))[0] == "topic:csv-exporter"
    assert (await keys(branch="no-such-branch"))[0] == "topic:csv-exporter"
    assert (await client.post("/v1/recall", json={"query": "x" * 20, "branch": "b" * 201})).status_code == 422


# ------------------------------------------------------------------ one page per piece of work, outcomes not logs


def test_release_branches_name_their_release():
    assert release_of_branch("task/release-4-2-0") == "4.2.0"
    assert release_of_branch("release-billing-v4.2.0") == "4.2.0"
    assert release_of_branch("release/v10.2.0") == "10.2.0" and release_of_branch("Release_4.2.1") == "4.2.1"
    for other in ("feature/csv", "release-notes", "fix-4-2-0", "releases", None):
        assert release_of_branch(other) is None, other
    assert derive_keys("Claude Code · billing · task/release-4-2-0", "Ships PR #440.") == [
        "release:4.2.0", "branch:task/release-4-2-0", "pr:440"], "the release comes ahead of the branch"


# The real Done and Contains items of a release page, before and after the cleanup.
NOISE = [
    ("Release PR #454 opened to master (non-draft, head release-billing-v4.2.0 at 5b7c3e9f2, no new commits)",
     "Release PR #454 opened to master (non-draft, head release-billing-v4.2.0, no new commits)"),
    ("#448 QA fixes for the export screen picked as e4a19d6c3 and 7c02f8b5e", "#448 QA fixes for the export screen picked"),
    ("staging dry-run validate passed (55/55 checks, 557/557 tests, 93.6% coverage on touched files)",
     "staging dry-run validate passed"),
    ("Hotfix #445 kept (as 91d4be0a6) and fixed by #451", "Hotfix #445 kept and fixed by #451"),
    # Not in parentheses nor after "as"/"at": left alone; the prompt keeps such lines out.
    ("staging dry-run validate passed with #448: 55/55 checks, 557/557 tests, 93.6% coverage on touched files",
     "staging dry-run validate passed with #448: 55/55 checks, 557/557 tests, 93.6% coverage on touched files"),
    # Fewer than four words would be left: kept as written.
    ("QA passed at 6ae3f0c84", "QA passed at 6ae3f0c84"),
    # Hex-looking words without a digit, and PR numbers, are not hashes.
    ("Review of #452 done (Sam approved, deadbeef build)", "Review of #452 done (Sam approved, deadbeef build)"),
]


def test_done_is_capped_and_log_noise_is_stripped():
    for before, after in NOISE:
        assert strip_log_noise(before) == after, before
    source = " ".join(f"step {i} done." for i in range(7))
    done = [_item(f"Outcome {w} reached", f"2026-10-{20 + i}", quote=f"step {i} done")
            for i, w in enumerate(["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"])]
    contains = [_item(NOISE[1][0], quote="step 6 done")]
    goal = [_item("Goal (as 91d4be0a6) of the release work", quote="step 0 done")]
    sections, _ = clean_sections([{"label": "Done", "items": done}, {"label": "Contains", "items": contains},
                                  {"label": "Goal", "items": goal}], source, "e1", [])
    by_label = {s["label"]: [i["text"] for i in s["items"]] for s in sections}
    assert by_label["Done"] == ["Outcome charlie reached", "Outcome delta reached", "Outcome echo reached", "Outcome foxtrot reached"]
    assert by_label["Contains"] == [NOISE[1][1]]
    assert by_label["Goal"] == ["Goal (as 91d4be0a6) of the release work"], "only Done, Contains and State are cleaned"


def test_prompts_route_delivering_prs_settle_pending_items_and_reject_tooling():
    assert "the assistant's own tooling, shell and editor behaviour" in KNOWLEDGE_SYSTEM
    assert "pending a decision" in KNOWLEDGE_SYSTEM and "there is nothing to keep" in KNOWLEDGE_SYSTEM
    assert "aliases" in THREADS_SYSTEM and "no page of its own" in THREADS_SYSTEM
    assert "deferred the CHF code fix to 4.2.1" in RECONCILE_SYSTEM


def test_threads_prompt_starts_pages_for_work_not_for_facts():
    """A single stated fact ("the DB is on port 5544") once became a topic page of its own, with a
    summary padded from the project profile; facts are knowledge, pages are for work."""
    assert "A thread is a piece of WORK" in THREADS_SYSTEM
    assert 'A "topic:" page needs work in progress too' in THREADS_SYSTEM
    assert "Never start a page for a single statement of a fact, rule, decision or preference" in THREADS_SYSTEM
    assert "the production database is on port 5544" in THREADS_SYSTEM
    assert "an excerpt that only states them starts no thread" in THREADS_SYSTEM
    assert "never the project's description or stack" in THREADS_SYSTEM
    assert "This is a configuration fact recorded for" in THREADS_SYSTEM


async def run_episodes(client, episodes, *, judge: dict | None = None) -> Router:
    """A project fed `episodes` (id, content, timestamp, label, thread output[, knowledge output])
    in order. `judge`: canned reconciliation verdicts, keyed by text of the judge's prompt."""
    from memvine_server.main import app
    from memvine_server.worker import drain

    await client.patch(f"/v1/projects/{client.project_slug}", headers=MGMT, json={"description": "billing releases"})
    router = Router()
    project_id = await _project_id(app, client.project_slug)
    for _, content, _, _, output, *knowledge in episodes:
        router.threads.responses[content[-40:]] = output
        if knowledge:
            router.knowledge.responses[content[-40:]] = knowledge[0]
    router.knowledge.responses |= judge or {}
    for ep_id, content, at, label, *_ in episodes:
        r = await client.post("/v1/ingest", json={"items": [{"id": ep_id, "content": content, "timestamp": at, "label": label}]})
        assert r.status_code == 202
        await drain(app.state.pool, router, app.state.embedder, project_id)
    jobs = (await client.get("/v1/jobs")).json()["jobs"]
    assert all(j["status"] == "done" for j in jobs), jobs
    return router


def _ev(quote, episode=None):
    return [{"episode_id": episode, "quote": quote}]


def _page(key, title, quote, *, kind="task", label="Goal", aliases=()):
    return {"key": key, "kind": kind, "title": title, "status": "open", "summary": f"{title}.",
            "sections": [{"label": label, "items": [{"text": quote, "date": None, "evidence": _ev(quote)}]}],
            "aliases": list(aliases), "related": []}


EP_TASK_RELEASE = "## user · 2026-10-21T09:00\nPrep the 4.2.0 release from this branch; the export fixes of PR #440 go in."
EP_BILLING_BRANCH = "## user · 2026-10-22T09:00\nThe billing branch is cut for that same release; tagging comes next."


@needs_db
@pytest.mark.asyncio
async def test_a_release_branch_page_becomes_the_release_page(client):
    await run_episodes(client, [
        ("r1", EP_TASK_RELEASE, "2026-10-21T09:00:00Z", "Claude Code · billing · task/release-4-2-0",
         json.dumps({"threads": [_page("branch:task/release-4-2-0", "Release prep", "Prep the 4.2.0 release from this branch",
                                       aliases=["pr:440"])]})),
        # Another branch of the same release, after the release key is taken: kind and title only.
        ("r2", EP_BILLING_BRANCH, "2026-10-22T09:00:00Z", LABEL,
         json.dumps({"threads": [_page("branch:release-billing-v4.2.0", "Billing branch", "tagging comes next")]})),
    ])
    threads = {t["key"]: t for t in (await client.get("/v1/threads")).json()}
    assert set(threads) == {"release:4.2.0", "branch:release-billing-v4.2.0"}
    release = threads["release:4.2.0"]
    assert (release["kind"], release["title"]) == ("release", "Release 4.2.0"), "re-keyed; kind and title follow the key"
    detail = (await client.get(f"/v1/threads/{release['id']}")).json()
    assert set(detail["aliases"]) == {"pr:440", "branch:task/release-4-2-0"}
    other = threads["branch:release-billing-v4.2.0"]
    assert (other["kind"], other["title"]) == ("release", "Release 4.2.0")


EP_RELEASE_PR = (
    "## user · 2026-10-23T10:00\nRelease PR #454 is open to master (non-draft, head release-billing-v4.2.0 at 5b7c3e9f2). "
    "Sam's review comments are fixed and re-review is requested. #448 QA fixes are picked as e4a19d6c3 and 7c02f8b5e. "
    "Docs updated. Changelog written. Tag drafted.")

THREAD_WITH_PR = json.dumps({"threads": [
    {"key": "release:4.2.0", "kind": "release", "title": "Release 4.2.0 (billing)", "status": "open",
     "summary": "Release PR #454 is open to master.",
     "sections": [
         {"label": "Goal", "items": [{"text": "Ship the export fixes from PR #440", "date": "2026-10-20",
                                      "evidence": _ev("ship the export fixes from PR #440", "ep1")}]},
         {"label": "Contains", "items": [{"text": "#448 QA fixes for the export screen picked as e4a19d6c3 and 7c02f8b5e",
                                          "date": "2026-10-23", "evidence": _ev("#448 QA fixes are picked")}]},
         {"label": "Done", "items": [
             {"text": "Release PR #454 opened to master (non-draft, head release-billing-v4.2.0 at 5b7c3e9f2)", "date": "2026-10-23",
              "evidence": _ev("Release PR #454 is open to master")},
             {"text": "Sam's review comments fixed and re-review requested", "date": "2026-10-23",
              "evidence": _ev("Sam's review comments are fixed")},
             {"text": "Docs updated for the release", "date": "2026-10-20", "evidence": _ev("Docs updated.")},
             {"text": "Changelog written for the release", "date": "2026-10-22", "evidence": _ev("Changelog written.")},
             {"text": "Tag drafted for the release", "date": "2026-10-23", "evidence": _ev("Tag drafted.")},
         ]}],
     "aliases": ["pr:454"], "related": ["pr:448"]},
    # The release PR as a page of its own, though the release page claims its key.
    {"key": "pr:454", "kind": "pr", "title": "#454 Release 4.2.0 to master", "status": "open", "summary": "The release PR.",
     "sections": [{"label": "State", "items": [{"text": "Release PR #454 waits for re-review", "date": "2026-10-23",
                                                "evidence": _ev("re-review is requested")}]}],
     "aliases": [], "related": []},
]})


@needs_db
@pytest.mark.asyncio
async def test_a_delivering_pr_page_is_merged_into_the_page_that_claims_it(client):
    await setup_release(client, extra=(("ep3", EP_RELEASE_PR, "2026-10-23T10:00:00Z", LABEL, THREAD_WITH_PR),))
    [t] = (await client.get("/v1/threads")).json()
    assert (t["key"], t["kind"], t["title"]) == ("release:4.2.0", "release", "Release 4.2.0 (billing)"), "the holder keeps its identity"
    detail = (await client.get(f"/v1/threads/{t['id']}")).json()
    assert "pr:454" in detail["aliases"]
    sections = {s["label"]: [i["text"] for i in s["items"]] for s in detail["sections"]}
    assert sections["State"] == ["Release PR #454 waits for re-review"], "the PR page's sections join the release page"
    assert sections["Done"] == ["Release PR #454 opened to master (non-draft, head release-billing-v4.2.0)",
                                "Sam's review comments fixed and re-review requested",
                                "Changelog written for the release", "Tag drafted for the release"], "four newest, SHAs off"
    assert sections["Contains"] == ["#448 QA fixes for the export screen picked"]
    job = next(j for j in (await client.get("/v1/jobs")).json()["jobs"] if j["episode_id"] == "ep3")
    assert job["result"]["threads"] == ["release:4.2.0"] and job["result"]["threads_merged"] == 1


EP_PR_ALONE = "## user · 2026-10-23T11:00\nPR #454 ships 4.2.0 to master; it is open for review now."
EP_CLAIM_PR = "## user · 2026-10-24T09:00\nThe release page should track #454 as its release PR from here on."


@needs_db
@pytest.mark.asyncio
async def test_recall_never_shows_a_page_beside_one_that_lists_it_as_an_alias(client):
    claim = _page("release:4.2.0", "Release 4.2.0 (billing)", "track #454 as its release PR", kind="release", label="State",
                  aliases=["pr:454"])
    claim["sections"].insert(0, {"label": "Goal", "items": [{"text": "Ship the export fixes from PR #440", "date": "2026-10-20",
                                                             "evidence": _ev("ship the export fixes from PR #440", "ep1")}]})
    await setup_release(client, extra=(
        # A pr:454 page of its own exists before the release page claims the key: both stay stored.
        ("ep3", EP_PR_ALONE, "2026-10-23T11:00:00Z", "Claude Code · billing · main",
         json.dumps({"threads": [_page("pr:454", "#454 Release 4.2.0 to master", "it is open for review now", kind="pr")]})),
        ("ep4", EP_CLAIM_PR, "2026-10-24T09:00:00Z", LABEL, json.dumps({"threads": [claim]})),
    ))
    assert {t["key"] for t in (await client.get("/v1/threads")).json()} == {"release:4.2.0", "pr:454"}
    query = "Where are release 4.2.0 and PR #454?"
    r = (await client.post("/v1/recall", json={"query": query})).json()
    assert len(r["threads"]) == 1, r["threads"]
    r = (await client.post("/v1/recall", json={"query": query, "branch": "release-billing-v4.2.0"})).json()
    assert [t["key"] for t in r["threads"]] == ["release:4.2.0"]


EP_GOTCHA = ("## user · 2026-10-21T12:00\nKnown issue: in a PDF invoice, the currency code 'CHF' prints as 'CH'. "
             "Whether that fix goes into 4.2.0 is still my call.")
EP_DECIDE = "## user · 2026-10-22T09:00\nDecision: the CHF code fix waits for 4.2.1, same as the footer copy fix A."
GOTCHA = json.dumps([{
    "statement": "Known issue: in a PDF invoice, the currency code CHF prints as CH. Whether the CHF code fix goes into 4.2.0 "
                 "is pending a user decision.", "category": "gotcha", "why": "invoices show the wrong currency", "thread_key": None,
    "subjects": ["chf code"], "assertion": "stated", "confidence": "high", "quote": "the currency code 'CHF' prints as 'CH'"}])
DECIDES = json.dumps([{
    "statement": "The user deferred the CHF currency code fix for PDF invoices to release 4.2.1 on 22 Oct 2026.",
    "category": "decision", "why": "scoping 4.2.0", "thread_key": None, "subjects": ["chf code"], "assertion": "stated",
    "confidence": "high", "quote": "the CHF code fix waits for 4.2.1"}])


@needs_db
@pytest.mark.asyncio
async def test_a_decision_supersedes_the_item_that_called_it_pending(client):
    from memvine_server.main import app

    router = await run_episodes(client, [
        ("g1", EP_GOTCHA, "2026-10-21T12:00:00Z", "Claude Code · billing · main", '{"threads": []}', GOTCHA),
        ("g2", EP_DECIDE, "2026-10-22T09:00:00Z", "Claude Code · billing · main", '{"threads": []}', DECIDES),
    ], judge={"NEW (2026-10-22): The user deferred": '{"match": 1, "relation": "update", "reason": "settles the pending decision"}'})
    [(system, user)] = [(s, u) for s, u in router.knowledge.calls if "NEW (2026-10-22)" in u]
    assert system == RECONCILE_SYSTEM and "EXISTING 1 (2026-10-21): Known issue" in user
    async with app.state.pool.connection() as conn:
        rows = await (await conn.execute(
            "SELECT id, statement, status, superseded_by FROM memory_items WHERE project_id = %s ORDER BY recorded_at",
            (await _project_id(app, client.project_slug),))).fetchall()
    assert [r["status"] for r in rows] == ["superseded", "active"]
    assert rows[0]["superseded_by"] == rows[1]["id"] and rows[1]["statement"].startswith("The user deferred")


def test_brief_page_keeps_what_steers_the_next_action():
    from memvine_server.threads import render_page_brief

    def item(text, date):
        return {"text": text, "date": date, "evidence": []}

    row = {"title": "Release 4.2.0", "status": "open", "key": "release:4.2.0", "summary": "PR #454 is open; the user merges it.",
           "sections": [{"label": "Contains", "items": [item(f"#{n} fix", "2026-10-21") for n in range(441, 448)]},
                        {"label": "State", "items": [item("PR #454 open to master", "2026-10-26")]},
                        {"label": "Decisions", "items": [item(f"decision {n}", f"2026-10-2{n}") for n in range(0, 7)]},
                        {"label": "Open questions", "items": [item("Does the lint gate block the merge?", "2026-10-26")]}]}
    text = render_page_brief(row)
    assert "Contains:" not in text and "State:" in text and "Open questions:" in text
    assert text.index("State:") < text.index("Open questions:") < text.index("Decisions:")
    assert text.count("- decision") == 4 and "decision 6" in text and "decision 0" not in text, "newest decisions, at most 4"


def test_release_title_keeps_a_product_but_not_a_branch():
    from memvine_server.threads import stable_title
    assert stable_title("release:4.2.0", "Release 4.2.0 (task/release-4-2-0)", None, False) == "Release 4.2.0"
    assert stable_title("release:4.2.0", "Release 4.2.0 (billing)", None, False) == "Release 4.2.0 (billing)"
    assert stable_title("release:4.2.0", "PR #454: Release 4.2.0 to master", None, False) == "Release 4.2.0"
