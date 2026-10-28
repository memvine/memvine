"""Offline: the client sends what the API expects (no server needed)."""
import json

import httpx
from memvine_sdk import Memvine


def test_recall_related_and_graph_requests():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), json.loads(request.content) if request.content else None))
        if request.url.path == "/v1/recall":
            return httpx.Response(200, json={"briefing": "b", "tokens": 3, "abstained": False, "items": [
                {"memory": {"id": "m-1"}, "score": 1.0, "related": [
                    {"kind": "supersedes", "direction": "out", "id": "m-0", "statement": "Launch is 29 Oct."}]}]})
        return httpx.Response(200, json={"memory": {"id": "m-1"}, "edges": [], "nodes": []})

    http = httpx.Client(base_url="http://memvine.test", transport=httpx.MockTransport(handler))
    mv = Memvine("mv_sk_x", http=http)
    r = mv.recall("When is the launch?", include={"related": True})
    assert r.items[0]["related"][0]["kind"] == "supersedes"
    mv.recall("When is the launch?")
    mv.related("m-1")
    mv.graph(since="2026-10-19T00:00:00Z", subject="launch")

    assert seen[0][2]["include"] == {"related": True}
    assert "include" not in seen[1][2]
    assert seen[2][:2] == ("GET", "http://memvine.test/v1/memories/m-1/related")
    assert seen[3][:2] == ("GET", "http://memvine.test/v1/graph?since=2026-10-19T00%3A00%3A00Z&subject=launch")


def test_remember_supersedes_and_graph_all():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={"memory": {"id": "m-2"}, "relation": "update", "related": "m-1", "nodes": []})

    mv = Memvine("mv_sk_x", http=httpx.Client(base_url="http://memvine.test", transport=httpx.MockTransport(handler)))
    mv.remember("Launch is 12 Nov.", supersedes="m-1")
    mv.remember("Deploys on Tuesdays.")
    mv.graph(all=True)
    # A correction leaves kind, subjects and audience to be inherited from the memory it replaces.
    assert seen[0][2] == {"statement": "Launch is 12 Nov.", "assertion": "stated", "supersedes": "m-1"}
    assert seen[1][2] == {"statement": "Deploys on Tuesdays.", "kind": "semantic", "subjects": [], "assertion": "stated",
                          "audience": "project"}
    assert seen[2][1] == "http://memvine.test/v1/graph?all=true"
