"""Memvine memory API client.

    from memvine_sdk import Memvine

    mv = Memvine("mv_sk_...", base_url="https://api.memvine.dev")
    mv.ingest([{"id": "chat-42:1", "content": "...", "timestamp": "2026-10-25T09:00:00Z"}])
    print(mv.recall("When is the launch?").briefing)

    session = mv.session("run-17", agent="my-agent", identity="user-17")
    session.send("user", "Let's use Postgres for the queue.")
    session.send("assistant", "Done, Postgres it is.")
    print(session.brief().text)   # after a context reset: this session's state + relevant memory
    session.close()               # its turns become long-term memory
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

__all__ = ["Memvine", "MemvineError", "Recall", "Session"]
__version__ = "0.1.0"


class MemvineError(Exception):
    """The API refused a request. `code` is the API's error code (e.g. project-cap-reached)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


@dataclass
class Recall:
    briefing: str
    tokens: int
    abstained: bool
    items: list[dict] = field(default_factory=list)
    standing: list[str] = field(default_factory=list)
    threads: list[dict] = field(default_factory=list)
    passages: list[dict] = field(default_factory=list)
    pending: list[dict] = field(default_factory=list)

    def __str__(self) -> str:
        return self.briefing


def _iso(value: datetime | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return value.isoformat()


class Memvine:
    """A client for one project, authenticated with its project key (mv_sk_...)."""

    def __init__(self, api_key: str, base_url: str = "https://api.memvine.dev", *, timeout: float = 60.0,
                 http: httpx.Client | None = None) -> None:
        if not api_key:
            raise ValueError("api_key is required (a project key starting with mv_sk_)")
        self._http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._headers = {"Authorization": f"Bearer {api_key}", "User-Agent": f"memvine-sdk-python/{__version__}"}

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Memvine:  # noqa: PYI034 - typing.Self needs 3.11; this package supports 3.10
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _call(self, method: str, path: str, body: Any = None, params: dict | None = None) -> Any:
        response = self._http.request(method, path, json=body, params=params, headers=self._headers)
        if response.status_code == 204:
            return None
        if response.is_error:
            try:
                err = response.json().get("error") or {}
            except ValueError:
                err = {}
            raise MemvineError(response.status_code, err.get("code", "error"), err.get("message", response.text[:300]))
        return response.json()

    # ---------------------------------------------------------------- memory

    def ingest(self, episodes: list[dict]) -> dict:
        """Send sources: dicts with id, content, timestamp, and optionally identity, label,
        metadata, audience. Re-sending an id with the same content is free; with new content it
        replaces the episode and everything learned from it."""
        for e in episodes:
            if "timestamp" in e:
                e["timestamp"] = _iso(e["timestamp"])
        return self._call("POST", "/v1/ingest", {"items": episodes})

    def recall(self, query: str, *, budget: int | None = None, identity: str | None = None,
               as_of: datetime | str | None = None, session_id: str | None = None,
               include_history: bool | None = None, exclude: list[str] | None = None, include: dict | None = None,
               branch: str | None = None) -> Recall:
        """include={"related": True}: each item lists its edges in item["related"], as
        {kind, direction, id, statement} (at most 6)."""
        body = {"query": query, "budget": budget, "identity": identity, "as_of": _iso(as_of), "session_id": session_id,
                "include_history": include_history, "exclude": exclude or [], "branch": branch,
                "include": include}
        data = self._call("POST", "/v1/recall", {k: v for k, v in body.items() if v is not None})
        return Recall(**{k: data.get(k, default) for k, default in
                         (("briefing", ""), ("tokens", 0), ("abstained", True), ("items", []), ("standing", []),
                          ("threads", []), ("passages", []), ("pending", []))})

    def remember(self, statement: str, *, kind: str | None = None, subjects: list[str] | None = None,
                 assertion: str = "stated", audience: str | None = None, standing: bool | None = None,
                 category: str | None = None, why: str | None = None, supersedes: str | None = None) -> dict:
        """Store one fact directly; returns the memory and how it related to what was known.

        With `supersedes` (a current memory's id), the statement replaces that memory as a person
        said, without asking the judge (relation "update"); kind, subjects, category, thread and
        audience come from it unless given. Otherwise kind defaults to "semantic", audience to "project"."""
        if supersedes is None:
            kind, subjects, audience = kind or "semantic", subjects or [], audience or "project"
        body = {"statement": statement, "kind": kind, "subjects": subjects, "assertion": assertion,
                "audience": audience, "standing": standing, "category": category, "why": why, "supersedes": supersedes}
        return self._call("POST", "/v1/memories", {k: v for k, v in body.items() if v is not None})

    def memories(self, *, q: str | None = None, status: str | None = None, kind: str | None = None, limit: int = 50) -> list[dict]:
        params = {k: v for k, v in {"q": q, "status": status, "kind": kind, "limit": limit}.items() if v is not None}
        return self._call("GET", "/v1/memories", params=params)

    def memory(self, memory_id: str) -> dict:
        return self._call("GET", f"/v1/memories/{memory_id}")

    def related(self, memory_id: str) -> dict:
        """A memory and every edge it has, both directions: {"memory": {...}, "edges": [{kind,
        direction, item | file | thread}]}. Kinds: supersedes, contradicts, extends, derived,
        anchored, decided_in."""
        return self._call("GET", f"/v1/memories/{memory_id}/related")

    def graph(self, *, since: datetime | str | None = None, thread: str | None = None, subject: str | None = None,
              all: bool = False) -> dict:  # noqa: A002 - the API parameter
        """A neighborhood of the memory graph, at most 300 nodes: {"nodes", "edges", "truncated",
        "since"}. Without `thread` or `subject` it is the open threads and the most recent facts;
        `all=True` adds every thread whatever its status. Nodes added or
        changed since `since` carry grew=True."""
        params = {k: v for k, v in {"since": _iso(since), "thread": thread, "subject": subject,
                                    "all": "true" if all else None}.items() if v is not None}
        return self._call("GET", "/v1/graph", params=params)

    def retire(self, memory_id: str) -> dict:
        return self._call("PATCH", f"/v1/memories/{memory_id}", {"status": "retired"})

    def delete_source(self, episode_id: str) -> None:
        """Delete a source and the memories drawn only from it."""
        self._call("DELETE", f"/v1/episodes/{episode_id}")

    # ---------------------------------------------------------------- live sessions

    def session(self, session_id: str, *, agent: str | None = None, identity: str | None = None,
                label: str | None = None) -> Session:
        return Session(self, session_id, agent=agent, identity=identity, label=label)


class Session:
    """One live agent session. Messages are stored as they are sent; completed stretches become
    memory while the session runs; close() flushes the rest."""

    def __init__(self, client: Memvine, session_id: str, *, agent: str | None, identity: str | None, label: str | None) -> None:
        self._client, self.id = client, session_id
        self._meta = {k: v for k, v in {"agent": agent, "identity": identity, "label": label}.items() if v}

    def send(self, role: str, content: str, at: datetime | str | None = None) -> dict:
        return self.send_many([{"role": role, "content": content, **({"at": _iso(at)} if at else {})}])

    def send_many(self, events: list[dict]) -> dict:
        return self._client._call("POST", f"/v1/sessions/{self.id}/events", {"events": events, **self._meta})

    def state(self) -> dict:
        return self._client._call("GET", f"/v1/sessions/{self.id}")

    def brief(self, budget: int = 2000) -> Any:
        """This session's working state, then relevant long-term memory: what to hand an agent
        after its context was compacted or reset. Returns an object with .text and .tokens."""
        data = self._client._call("GET", f"/v1/sessions/{self.id}/brief", params={"budget": budget})
        return _Brief(text=data["text"], tokens=data["tokens"], session=data["session"])

    def recall(self, query: str, **kwargs: Any) -> Recall:
        """Recall that leaves out this session's own pending messages (already in its context)."""
        return self._client.recall(query, session_id=self.id, **kwargs)

    def close(self) -> dict:
        return self._client._call("POST", f"/v1/sessions/{self.id}/close")


@dataclass
class _Brief:
    text: str
    tokens: int
    session: dict

    def __str__(self) -> str:
        return self.text
