"""An update retires the older memory, so a pair too far apart to be one fact is never an update,
whatever the model says (reconcile.py, the embedder's update_min_similarity)."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from memvine_server.embeddings import FastEmbedEmbedder
from memvine_server.extraction.extract import Candidate
from memvine_server.extraction.llm import FakeLLM
from memvine_server.reconcile import _classify, update_floor

NOW = datetime(2026, 10, 21, tzinfo=UTC)
RULE = Candidate(statement="Always deploy muse with `make deploy ENV=prod`, never with kubectl apply.", kind="procedural",
                 subjects=["muse"], assertion="stated", confidence="high", valid_from=None, quote="Always deploy muse", category="rule")


def existing(similarity: float) -> dict:
    return {"id": 1, "statement": "The muse production database is Postgres 16 running on port 5544.", "kind": "semantic",
            "category": "fact", "recorded_at": NOW, "similarity": similarity}


@pytest.mark.asyncio
async def test_an_update_needs_a_pair_similar_enough_to_be_one_fact():
    llm = FakeLLM(default=json.dumps({"match": 1, "relation": "update", "reason": "both about muse"}))
    floor = FastEmbedEmbedder.update_min_similarity
    # Two facts of one project (about 0.66 on BGE-small): both stay.
    assert await _classify(llm, RULE, NOW, [existing(0.66)], update_floor=floor) == ("different", None)
    # A changed value of one fact (about 0.87): the update stands.
    close = existing(0.87)
    assert await _classify(llm, RULE, NOW, [close], update_floor=floor) == ("update", close)
    # An embedder that declares no floor (the tests' fake one) leaves the model's word as it is.
    assert update_floor(object()) == 0.0
    assert (await _classify(llm, RULE, NOW, [existing(0.2)]))[0] == "update"
    # Only updates are held to it: a contradiction keeps both items anyway.
    llm.default = json.dumps({"match": 1, "relation": "contradiction", "reason": ""})
    assert (await _classify(llm, RULE, NOW, [existing(0.66)], update_floor=floor))[0] == "contradiction"
