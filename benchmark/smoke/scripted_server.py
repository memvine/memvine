"""A local API with a scripted model, to check hosted.py's synthesis path without a model key.

The server's own `fake` backend always answers "[]" and cannot be steered from outside the process.
Here knowledge extraction returns hosted.py's "never run the invoice backfill script ..." sentence as
a standing rule and nothing else, so this proves the smoke test's plumbing, not a model's judgement.

    cd server && MEMVINE_DATABASE_URL=... MEMVINE_MANAGEMENT_KEY=mv_mk_... MEMVINE_EMBEDDINGS_BACKEND=fake \
        .venv/bin/python ../benchmark/smoke/scripted_server.py 8431
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))  # this checkout's server, not an installed one

import uvicorn  # noqa: E402

from memvine_server.extraction import llm as llm_mod  # noqa: E402
from memvine_server.extraction.prompts import KNOWLEDGE_SYSTEM, RECONCILE_SYSTEM, THREADS_SYSTEM  # noqa: E402


class Scripted:
    async def complete(self, system, user, *, max_tokens=4096):
        if system == THREADS_SYSTEM:
            return json.dumps({"threads": []})
        if system == KNOWLEDGE_SYSTEM:
            m = re.search(r"never run the invoice backfill script[^.]*\.", user)
            if not m:
                return "[]"
            return json.dumps([{"statement": "Never run the invoice backfill script against production without a --dry-run pass first.",
                "category": "rule", "why": "it double-charged forty accounts last quarter", "thread_key": None, "scope": "project",
                "subjects": ["invoice backfill"], "assertion": "stated", "confidence": "high", "valid_from": "2026-10-28",
                "quote": m.group(0), "standing": True}])
        if system == RECONCILE_SYSTEM:
            return json.dumps({"match": None, "relation": "different", "reason": "distinct"})
        return "{}"


def make_llm(backend=None):
    llm = Scripted()
    llm.fast = llm
    return llm


llm_mod.make_llm = make_llm
from memvine_server import main  # noqa: E402  (imports the patched make_llm)

if __name__ == "__main__":
    uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8431)
