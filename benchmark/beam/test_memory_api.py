"""Backend checks on a tiny synthetic conversation. The memvine backend test needs a running
server (MEMVINE_API_URL, MEMVINE_MANAGEMENT_KEY) and is skipped otherwise."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from dataset import Conversation, Message
from memory_api import FullContextBackend, RagBackend, episodes_of, make_backend

T0 = datetime(2024, 3, 15, 12, tzinfo=UTC)


def conversation() -> Conversation:
    turns = [
        ("s000", "user", "Craig Baker", "I'm planning a trip to Lisbon in June; budget is 2000 dollars."),
        ("s000", "assistant", "Assistant", "Lisbon in June is warm. A 2000 dollar budget covers a week comfortably."),
        ("s001", "user", "Craig Baker", "Change of plan: the trip is now Porto, not Lisbon, and the budget went up to 2500."),
        ("s001", "assistant", "Assistant", "Noted, Porto with a 2500 dollar budget."),
        ("s002", "user", "Craig Baker", "My sister Anna is joining; she is vegetarian."),
    ]
    messages = [
        Message(message_id=f"7_{s}_t{i}", session_id=s, ordinal=i, role=r, author=a, identity="craig-baker-7" if r == "user" else "assistant",
                content=c, timestamp=T0 + timedelta(days=int(s[1:]) * 10, microseconds=i), turn_id=str(i))
        for i, (s, r, a, c) in enumerate(turns)
    ]
    return Conversation(id="7", title="Trip planning", user_identity="craig-baker-7", user_traits={}, messages=messages, questions=[])


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def test_episodes_group_by_session_and_size(monkeypatch):
    conv = conversation()
    episodes = episodes_of(conv)
    assert [e["metadata"]["session"] for e in episodes] == ["s000", "s001", "s002"]
    assert episodes[0]["content"].startswith("## Craig Baker · 2024-03-15T12:00:00Z\nI'm planning")
    assert episodes[0]["timestamp"] == "2024-03-15T12:00:00Z"
    assert episodes[1]["metadata"]["turns"] == ["7_s001_t2", "7_s001_t3"]
    monkeypatch.setattr("memory_api.EPISODE_CHARS", 40)
    assert len(episodes_of(conv)) == 5, "a small cap makes every turn its own episode"


@pytest.mark.asyncio
async def test_fullcontext_respects_the_moment():
    backend = FullContextBackend()
    conv = conversation()
    handle = await backend.open(conv)
    await backend.ingest(handle, conv)
    await backend.wait(handle)
    early = await backend.recall(handle, "where is the trip?", iso(conv.messages[1].timestamp), conv.user_identity)
    assert early["asOf"] == iso(conv.messages[1].timestamp)
    assert [r["content"][:6] for r in early["results"]] == ["Craig ", "Assist"]
    late = await backend.recall(handle, "where is the trip?", iso(conv.messages[-1].timestamp), conv.user_identity)
    assert len(late["results"]) == 5 and not late["truncated"]


@pytest.mark.asyncio
async def test_rag_ranks_the_relevant_turn_first():
    backend = RagBackend()
    conv = conversation()
    handle = await backend.open(conv)
    await backend.ingest(handle, conv)
    result = await backend.recall(handle, "Is anyone vegetarian?", iso(conv.messages[-1].timestamp), conv.user_identity)
    assert "vegetarian" in result["results"][0]["content"]
    assert result["usedEvidenceTokens"] <= 8_000
    before = await backend.recall(handle, "Is anyone vegetarian?", iso(conv.messages[3].timestamp), conv.user_identity)
    assert all("vegetarian" not in r["content"] for r in before["results"]), "nothing after the moment asked"


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("MEMVINE_API_URL"), reason="needs a running Memvine server")
async def test_memvine_backend_round_trip():
    backend = make_backend("memvine", f"test {datetime.now(UTC).strftime('%H%M%S%f')}")
    conv = conversation()
    handle = await backend.open(conv)
    await backend.ingest(handle, conv)
    assert handle.extra["episodes"] == 3
    await backend.wait(handle)
    result = await backend.recall(handle, "Where is the trip and what is the budget?", iso(conv.messages[-1].timestamp), conv.user_identity)
    assert result["asOf"] == iso(conv.messages[-1].timestamp)
    assert "results" in result and "briefing" in result
    await backend.close()
