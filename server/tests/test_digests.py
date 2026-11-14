"""Digests (migration 0012, digests.py): in conversation mode each excerpt gets a short digest, every
four consecutive digests are rolled up, and a question that asks for a summary is served a cover of
the conversation in time order. Whether a model's digests carry the right topics is measured with a
real model in benchmark/beam."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import needs_db

from memvine_server import digests
from memvine_server.extraction.prompts import DIGEST_SYSTEM, ROLLUP_SYSTEM

MGMT = {"Authorization": "Bearer mv_mk_test"}
TOPICS = ["invoice numbering", "late fee rules", "currency rounding", "tax codes", "dunning emails"]


class Model:
    """A profile item for the extraction prompt, a digest for an excerpt, a roll-up for digests."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.fast = self

    async def complete(self, system: str, user: str, *, max_tokens: int = 4096) -> str:
        self.calls.append((system, user))
        topics = [t for t in TOPICS if t in user]
        if system == DIGEST_SYSTEM:
            return f"The user worked on {topics[0]} for the Northwind billing service and the assistant recommended a fix."
        if system == ROLLUP_SYSTEM:
            return "The user worked through " + ", ".join(topics) + " for the Northwind billing service."
        return json.dumps([{"slot": "state/current-work", "statement": f"The user is working on {topics[0]} for Northwind billing.",
                            "relation": "update", "subjects": ["billing"], "confidence": "high", "standing": False,
                            "quote": f"working on {topics[0]}"}])


def tokens_of(text: str) -> int:
    return max(1, len(text) // 4)


@needs_db
@pytest.mark.asyncio
async def test_a_summary_question_reads_the_digests_in_order(client):
    from memvine_server.main import app
    from memvine_server.worker import drain

    slug = client.project_slug
    assert (await client.patch(f"/v1/projects/{slug}", headers=MGMT, json={"memory_mode": "conversation"})).status_code == 200
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (slug,))).fetchone())["id"]
    start = datetime(2024, 3, 1, 9, tzinfo=UTC)
    items = [{"id": f"ep{i}", "timestamp": (start + timedelta(days=i)).isoformat(),
              "content": f"## user · day {i}\nToday I am working on {topic} for the Northwind billing service."}
             for i, topic in enumerate(TOPICS)]
    assert (await client.post("/v1/ingest", json={"items": items})).status_code == 202
    llm = Model()
    await drain(app.state.pool, llm, app.state.embedder, project_id)
    assert all(j["status"] == "done" for j in (await client.get("/v1/jobs")).json()["jobs"])

    async with app.state.pool.connection() as conn:
        rows = await (await conn.execute(
            "SELECT id, level, parent_id, episode_id, content FROM digests WHERE project_id = %s ORDER BY level, occurred_at",
            (project_id,))).fetchall()
        # Five excerpts: five digests, the first four rolled up, the fifth waiting for three more.
        assert [r["level"] for r in rows] == [1, 1, 1, 1, 1, 2]
        rollup = rows[-1]
        assert [r["parent_id"] for r in rows[:5]] == [rollup["id"]] * 4 + [None]
        assert all(t in rollup["content"] for t in TOPICS[:4]) and TOPICS[4] not in rollup["content"]

        # Room for everything: the roll-up is opened into its four digests. Room for less: it stays closed.
        vector = (await app.state.embedder.embed(["a summary of the billing work"]))[0]
        whole = await digests.relevant(conn, project_id, vector, audiences=None, as_of=None, budget=2_000, tokens_of=tokens_of)
        assert [r["episode_id"] for r in whole] == ["ep0", "ep1", "ep2", "ep3", "ep4"]
        tight = await digests.relevant(conn, project_id, vector, audiences=None, as_of=None, budget=70, tokens_of=tokens_of)
        assert [r["level"] for r in tight] == [2, 1]
        # Asked as of the third day, the roll-up runs past the moment: only the digests up to it are used.
        early = await digests.relevant(conn, project_id, vector, audiences=None, as_of=start + timedelta(days=2, hours=1),
                                       budget=2_000, tokens_of=tokens_of)
        assert [r["episode_id"] for r in early] == ["ep0", "ep1", "ep2"]

    r = (await client.post("/v1/recall", json={"query": "Give me a summary of the billing work so far"})).json()
    assert [d["content"].split(" for ")[0] for d in r["digests"]] == [f"The user worked on {t}" for t in TOPICS]
    assert "--- the conversation in order, as digests of its excerpts ---" in r["briefing"]
    assert r["briefing"].index(TOPICS[0]) < r["briefing"].index(TOPICS[4])
    # Any other question is answered from the details, as before.
    r = (await client.post("/v1/recall", json={"query": "What did I do about late fee rules?"})).json()
    assert r["digests"] == [] and "digests of its excerpts" not in r["briefing"]

    # An excerpt sent again with other words gets a digest of the new words. Synthesized again with the
    # words it had (a rebuild of memory), it keeps its digest: the model is not asked.
    from memvine_server.rebuild import rebuild_memory

    def digests_written() -> int:
        return [s for s, _ in llm.calls].count(DIGEST_SYSTEM)

    assert digests_written() == 5
    resent = {**items[2], "content": items[2]["content"] + " The late fee rules came up again."}
    assert (await client.post("/v1/ingest", json={"items": [resent]})).json()["items"][0]["outcome"] == "replaced"
    await drain(app.state.pool, llm, app.state.embedder, project_id)
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute("SELECT content FROM digests WHERE project_id = %s AND episode_id = 'ep2'", (project_id,))).fetchone()
        assert digests_written() == 6 and "late fee rules" in row["content"]
        assert (await rebuild_memory(conn, project_id)).queued == 5
    await drain(app.state.pool, llm, app.state.embedder, project_id)
    assert digests_written() == 6

    # A digest stored again replaces the old one; the roll-up that repeated the old one is gone,
    # and the four are rolled up again.
    async with app.state.pool.connection() as conn:
        ep = await (await conn.execute("SELECT id, content, occurred_at, audience FROM episodes WHERE project_id = %s AND id = 'ep1'",
                                       (project_id,))).fetchone()
        assert await digests.store(conn, app.state.embedder, project_id, ep, "The user replaced the late fee rules for Northwind billing.")
        left = await (await conn.execute("SELECT level, parent_id FROM digests WHERE project_id = %s", (project_id,))).fetchall()
        assert [r["level"] for r in left] == [1] * 5 and not any(r["parent_id"] for r in left)
        assert await digests.roll_up(conn, llm, app.state.embedder, project_id) == 1
        # A coding project has no digests.
        assert await digests.store(conn, app.state.embedder, project_id, ep, None) is False


def test_a_refusal_or_json_is_not_a_digest():
    assert digests._clean("NONE") is None and digests._clean('[{"statement": "The user asked about invoices and the rest."}]') is None
    long = digests._clean("The user set up invoice numbering for Northwind. " * 40)
    assert len(long) <= digests.MAX_CHARS and long.endswith(".")
