"""POST /v1/ask: a recall, then the model's answer written from what the recall returned. With
nothing recalled the model is not asked; with no model the recall still comes back; and the call is
authorised, logged and charged as the recall it is."""
from __future__ import annotations

import asyncio
import json

import pytest
from conftest import needs_db

from memvine_server.ask import ASK_MAX_TOKENS
from memvine_server.config import settings
from memvine_server.extraction.llm import FakeLLM, LLMError
from memvine_server.extraction.prompts import ASK_SYSTEM, RELEVANCE_SYSTEM
from memvine_server.main import app
from memvine_server.worker import drain

pytestmark = needs_db

MGMT = {"Authorization": "Bearer mv_mk_test"}
SOURCE = ("## Developer · 2026-10-20T09:00\nNorthwind billing notes: invoices retry and invoices round. "
          "Always quote Northwind amounts in euros.")
EXTRACTED = json.dumps([
    {"statement": "Northwind invoices retry three times before failing.", "kind": "semantic", "category": "fact",
     "subjects": ["invoices"], "assertion": "stated", "confidence": "high", "quote": "invoices retry"},
    {"statement": "Northwind invoices round to the nearest cent.", "kind": "semantic", "category": "fact",
     "subjects": ["invoices"], "assertion": "stated", "confidence": "high", "quote": "invoices round"},
    {"statement": "Always quote Northwind amounts in euros.", "kind": "procedural", "category": "rule", "standing": True,
     "subjects": ["amounts"], "assertion": "stated", "confidence": "high", "quote": "Always quote Northwind amounts in euros"},
])
STANDING = "Always quote Northwind amounts in euros."
QUESTION = "What happens when a Northwind invoice run fails: do invoices retry?"
UNRELATED = "Weather forecast for Paris tomorrow?"
ANSWER = "Northwind invoices retry three times before failing (recorded 20 Oct)."


async def seed(client):
    """Two facts and one standing rule; afterwards the fake model answers every ask with ANSWER."""
    app.state.llm.responses = {"invoices retry and invoices round": EXTRACTED}
    app.state.llm.default = "[]"
    r = await client.post("/v1/ingest", json={"items": [{"id": "n-1", "content": SOURCE, "timestamp": "2026-10-20T09:00:00Z"}]})
    assert r.status_code == 202, r.text
    async with app.state.pool.connection() as conn:
        pid = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    await drain(app.state.pool, app.state.llm, app.state.embedder, pid)
    assert len((await client.get("/v1/memories")).json()) == 3
    app.state.llm.responses = {"<question>": ANSWER}
    app.state.llm.calls.clear()


def ask_calls(llm=None) -> list[str]:
    """The user prompts of the answering calls the model received."""
    return [user for system, user in (llm or app.state.llm).calls if system == ASK_SYSTEM]


def model_does(behaviour):
    """The fake model runs `behaviour(user, max_tokens)` for the answering call; nothing else changes."""
    llm, original = app.state.llm, app.state.llm.complete

    async def complete(system, user, *, max_tokens=4096):
        if system == ASK_SYSTEM:
            llm.calls.append((system, user))
            return await behaviour(user, max_tokens)
        return await original(system, user, max_tokens=max_tokens)

    llm.complete = complete


async def ask(client, query=QUESTION, **extra):
    r = await client.post("/v1/ask", json={"query": query, **extra})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_ask_returns_the_answer_and_the_recall_it_was_based_on(client):
    await seed(client)
    plain = (await client.post("/v1/recall", json={"query": QUESTION})).json()
    assert not plain["abstained"]

    r = await ask(client)
    assert r["answered"] is True and r["answer"] == ANSWER and r["reason"] is None
    # The recall inside is the recall POST /v1/recall gives for the same question, whole (the first
    # recall was tracked, so each memory's own recall count has moved on).
    assert set(r["recall"]) == set(plain)
    assert r["recall"]["briefing"] == plain["briefing"] and r["recall"]["tokens"] == plain["tokens"]
    assert [i["memory"]["id"] for i in r["recall"]["items"]] == [i["memory"]["id"] for i in plain["items"]]
    assert r["recall"]["items"]
    assert "Northwind invoices retry three times before failing" in r["recall"]["briefing"]
    assert r["recall"]["standing"] == [STANDING]

    # One answering call, and the model read exactly that briefing (standing rules included) and the question.
    [prompt] = ask_calls()
    assert f"<memory>\n{plain['briefing']}\n</memory>" in prompt
    assert f"<question>\n{QUESTION}\n</question>" in prompt
    assert f"- {STANDING}" in prompt and prompt.startswith("Today: ")


@pytest.mark.asyncio
async def test_the_answer_comes_from_the_main_model_with_a_modest_limit(client):
    await seed(client)
    app.state.llm.fast = FakeLLM(default="[]")  # the fast model, apart: it picks what the question needs
    limits = []

    async def answer(user, max_tokens):
        limits.append(max_tokens)
        return f"  {ANSWER}\n"

    model_does(answer)
    r = await ask(client)
    assert r["answer"] == ANSWER, "surrounding whitespace is not part of the answer"
    assert limits == [ASK_MAX_TOKENS] and ASK_MAX_TOKENS <= 1000
    assert len(ask_calls()) == 1 and ask_calls(app.state.llm.fast) == []
    assert any(system == RELEVANCE_SYSTEM for system, _ in app.state.llm.fast.calls)


@pytest.mark.asyncio
async def test_as_of_is_the_date_the_model_judges_against(client):
    await seed(client)
    await ask(client, as_of="2026-10-25T12:00:00Z")
    assert ask_calls()[0].startswith("Today: 25 Oct 2026\n")


@pytest.mark.asyncio
async def test_an_unrelated_question_gets_no_answer_and_no_model_call(client):
    await seed(client)
    r = await ask(client, UNRELATED)
    assert r["answered"] is False and r["answer"] is None and r["reason"] == "nothing_relevant"
    assert r["recall"]["abstained"] and r["recall"]["abstain_reason"] == "below_threshold"
    # The standing rule still comes back with the recall, and is not taken for an answer.
    assert r["recall"]["briefing"] == "" and r["recall"]["standing"] == [STANDING]
    assert ask_calls() == []


@pytest.mark.asyncio
async def test_an_empty_memory_gets_no_answer_and_no_model_call(client):
    app.state.llm.responses = {"<question>": ANSWER}
    r = await ask(client)
    assert r["answered"] is False and r["reason"] == "nothing_relevant"
    assert r["recall"]["abstain_reason"] == "empty_store"
    assert ask_calls() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["llm_error", "other_error", "timeout", "empty"])
async def test_a_failing_model_returns_the_recall_without_an_answer(client, monkeypatch, failure):
    await seed(client)

    async def behaviour(user, max_tokens):
        if failure == "llm_error":
            raise LLMError("response truncated at max_tokens")
        if failure == "other_error":
            raise ConnectionError("connection reset")
        if failure == "timeout":
            await asyncio.sleep(5)
        return "  "

    if failure == "timeout":
        monkeypatch.setattr(settings, "ask_timeout", 0.05)
    model_does(behaviour)
    r = await client.post("/v1/ask", json={"query": QUESTION})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answered"] is False and body["answer"] is None and body["reason"] == "model_unavailable"
    assert not body["recall"]["abstained"]
    assert "Northwind invoices retry three times before failing." in [i["memory"]["statement"] for i in body["recall"]["items"]]
    assert len(ask_calls()) == 1


@pytest.mark.asyncio
async def test_no_model_configured_returns_the_recall_without_an_answer(client):
    await seed(client)
    llm, app.state.llm = app.state.llm, None
    try:
        r = await client.post("/v1/ask", json={"query": QUESTION})
    finally:
        app.state.llm = llm
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answered"] is False and body["reason"] == "model_unavailable"
    assert not body["recall"]["abstained"] and body["recall"]["items"]


@pytest.mark.asyncio
async def test_ask_is_authorised_like_a_recall(client):
    await seed(client)
    assert (await client.post("/v1/ask", json={"query": QUESTION}, headers={"Authorization": "Bearer mv_sk_unknown"})).status_code == 401
    assert (await client.post("/v1/ask", json={"query": QUESTION}, headers={"Authorization": "Bearer mv_mk_wrong",
                                                                           "X-Memvine-Project": client.project_slug})).status_code == 403
    assert (await client.post("/v1/ask", json={"query": QUESTION}, headers=MGMT)).status_code == 400, "the console names the project"
    assert (await client.post("/v1/ask", json={"query": ""})).status_code == 422
    assert ask_calls() == []


@pytest.mark.asyncio
async def test_ask_is_logged_tracked_and_charged_as_a_recall(client):
    await seed(client)
    console = {**MGMT, "X-Memvine-Project": client.project_slug}

    async def recall_counts() -> dict[str, int]:
        return {m["statement"]: m["recall_count"] for m in (await client.get("/v1/memories")).json()}

    async def usage() -> dict:
        return (await client.get("/v1/usage?days=1")).json()[-1]

    # The console (no project key) is a person looking: answered, logged, not tracked, not charged.
    r = await client.post("/v1/ask", json={"query": QUESTION}, headers=console)
    assert r.status_code == 200 and r.json()["answered"] is True
    assert set((await recall_counts()).values()) == {0}
    assert (await usage())["recall"] == 0

    # A project key: each ask is one recall in usage, whatever came of it, and what it served is marked recalled.
    served = await ask(client)
    await ask(client, UNRELATED)
    model_does(lambda user, max_tokens: asyncio.sleep(0, result=""))
    await ask(client)
    today = await usage()
    assert today["recall"] == 3 and float(today["recall_credits"]) == pytest.approx(0.3)
    counts = await recall_counts()
    assert counts[STANDING] == 3, "standing rules go with every recall, an abstained one too"
    assert all(counts[i["memory"]["statement"]] == 2 for i in served["recall"]["items"])

    # The Requests log: the recall's own line, then what came of the answer.
    rows = [row for row in (await client.get("/v1/requests")).json() if row["path"] == "/v1/ask"]
    assert [(row["method"], row["status"], row["key_name"]) for row in rows] == [("POST", 200, "test")] * 3 + [("POST", 200, None)]
    unavailable, unrelated, answered, from_console = (row["detail"] for row in rows)
    assert answered.startswith(f"served {len(served['recall']['items'])} items, ") and answered.endswith("; answered")
    assert from_console.startswith("served ") and from_console.endswith("; answered")
    assert unrelated.startswith("abstained: below_threshold") and unrelated.endswith("; no answer: nothing_relevant")
    assert unavailable.startswith("served ") and unavailable.endswith("; no answer: model_unavailable")
