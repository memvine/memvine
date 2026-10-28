"""The memory graph: named edges between facts, threads and files, read from what the store keeps.

    supersedes   memory_items.supersedes / superseded_by    newer fact -> the older one it replaced
    contradicts  memory_items.conflicts_with                 two active facts that disagree (no direction)
    extends      memory_links kind 'extends'                 newer fact -> the older one it adds detail to
    derived      memory_links kind 'derived'                 inferred fact -> each item it was drawn from
    anchored     memory_anchors (+ code_files)               fact -> file at a version; current or changed
    decided_in   memory_items.thread_id                      fact -> the thread of work it came out of

Who said a fact (its sources' identities) and why it matters (`why`) are fields on the node, not edges.
Retired memories are never a neighbor; recall's audience limits apply to neighbors as to the items.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from .models import (
    GraphEdge,
    GraphFile,
    GraphNode,
    GraphOut,
    MemoryRelated,
    RelatedEdge,
    RelatedFile,
    RelatedRef,
    RelatedThread,
)
from .store import MEMORY_COLUMNS, evidence_for, memory_out

EDGE_ORDER = ["supersedes", "contradicts", "extends", "derived", "decided_in", "anchored"]
MAX_RECALL_RELATED = 6
MAX_GRAPH_NODES = 300
# Without a thread or subject, the graph starts from the most recent facts and the open threads.
MAX_OPEN_THREADS = 50
THREAD_SELECT = "SELECT id, key, kind, title, status, summary, first_seen_at, last_activity_at FROM threads"


async def edges_for(conn, project_id: UUID, ids: list[UUID], audiences: list[str] | None = None) -> tuple[dict, dict, dict]:
    """Every edge of the given memories. Returns (edges by memory id, neighbor memory rows by id,
    thread rows by id). An edge is {"kind", "direction", "other": UUID} for a memory neighbor,
    {"kind", "direction", "thread": UUID} for decided_in, or {"kind", "direction", "file": dict}."""
    out: dict[UUID, list[dict]] = {i: [] for i in ids}
    if not ids:
        return out, {}, {}
    rows = await (await conn.execute(
        "SELECT id, supersedes, superseded_by, conflicts_with, thread_id FROM memory_items WHERE project_id = %s AND id = ANY(%s)",
        (project_id, ids))).fetchall()
    raw: list[tuple[UUID, str, str, UUID]] = []  # (memory, kind, direction, other memory)
    for r in rows:
        if r["supersedes"]:
            raw.append((r["id"], "supersedes", "out", r["supersedes"]))
        if r["superseded_by"]:
            raw.append((r["id"], "supersedes", "in", r["superseded_by"]))
        for other in r["conflicts_with"] or []:
            raw.append((r["id"], "contradicts", "both", other))
    for r in await (await conn.execute(
            "SELECT id, supersedes FROM memory_items WHERE project_id = %s AND supersedes = ANY(%s)", (project_id, ids))).fetchall():
        raw.append((r["supersedes"], "supersedes", "in", r["id"]))
    for r in await (await conn.execute(
            "SELECT from_id, to_id, kind FROM memory_links WHERE project_id = %s AND (from_id = ANY(%s) OR to_id = ANY(%s))",
            (project_id, ids, ids))).fetchall():
        if r["from_id"] in out:
            raw.append((r["from_id"], r["kind"], "out", r["to_id"]))
        if r["to_id"] in out:
            raw.append((r["to_id"], r["kind"], "in", r["from_id"]))

    clause, params = "", [project_id, list({o for *_, o in raw})]
    if audiences is not None:
        clause, params = " AND audience = ANY(%s)", [*params, audiences]
    neighbors = {r["id"]: r for r in await (await conn.execute(
        f"SELECT {MEMORY_COLUMNS} FROM memory_items WHERE project_id = %s AND id = ANY(%s) AND status <> 'retired'{clause}",
        params)).fetchall()}
    seen: set[tuple] = set()
    for mid, kind, direction, other in raw:
        if other in neighbors and other != mid and (mid, kind, direction, other) not in seen:
            seen.add((mid, kind, direction, other))
            out[mid].append({"kind": kind, "direction": direction, "other": other})

    thread_ids = list({r["thread_id"] for r in rows if r["thread_id"]})
    threads = {t["id"]: t for t in await (await conn.execute(
        THREAD_SELECT + " WHERE project_id = %s AND id = ANY(%s)", (project_id, thread_ids))).fetchall()} if thread_ids else {}
    for r in rows:
        if r["thread_id"] in threads:
            out[r["id"]].append({"kind": "decided_in", "direction": "out", "thread": r["thread_id"]})
    for a in await anchors_for(conn, project_id, ids):
        out[a["memory_id"]].append({"kind": "anchored", "direction": "out", "file": a})

    def order(edge: dict) -> tuple:
        other = neighbors.get(edge.get("other"))
        return EDGE_ORDER.index(edge["kind"]), -(other["recorded_at"].timestamp() if other else 0)
    for mid in out:
        out[mid].sort(key=order)
    return out, neighbors, threads


async def anchors_for(conn, project_id: UUID, ids: list[UUID]) -> list[dict]:
    rows = await (await conn.execute(
        """
        SELECT a.memory_id, a.repo, a.path, a.blob, a.anchored_at, f.blob AS current_blob, f.path IS NOT NULL AS reported
        FROM memory_anchors a LEFT JOIN code_files f ON f.project_id = a.project_id AND f.repo = a.repo AND f.path = a.path
        WHERE a.project_id = %s AND a.memory_id = ANY(%s) ORDER BY a.repo, a.path
        """, (project_id, ids))).fetchall()
    return [dict(r) for r in rows]


def related_file(a: dict) -> RelatedFile:
    return RelatedFile(repo=a["repo"], path=a["path"], version=a["blob"], anchored_at=a["anchored_at"],
                       current_version=a["current_blob"], current=(a["current_blob"] == a["blob"]) if a["reported"] else None)


def related_thread(t) -> RelatedThread:
    return RelatedThread(id=t["id"], key=t["key"], kind=t["kind"], title=t["title"], status=t["status"], summary=t["summary"])


async def related(conn, project_id: UUID, memory_id: UUID) -> MemoryRelated | None:
    """GET /v1/memories/{id}/related: the memory and every edge, both directions."""
    row = await (await conn.execute(
        f"SELECT {MEMORY_COLUMNS} FROM memory_items WHERE project_id = %s AND id = %s", (project_id, memory_id))).fetchone()
    if not row:
        return None
    edges, neighbors, threads = await edges_for(conn, project_id, [memory_id])
    evidence = await evidence_for(conn, [memory_id, *neighbors])
    out = []
    for e in edges[memory_id]:
        if "other" in e:
            n = neighbors[e["other"]]
            out.append(RelatedEdge(kind=e["kind"], direction=e["direction"], item=memory_out(n, evidence[n["id"]])))
        elif "thread" in e:
            out.append(RelatedEdge(kind=e["kind"], direction=e["direction"], thread=related_thread(threads[e["thread"]])))
        else:
            out.append(RelatedEdge(kind=e["kind"], direction=e["direction"], file=related_file(e["file"])))
    return MemoryRelated(memory=memory_out(row, evidence[memory_id]), edges=out)


async def recall_refs(conn, project_id: UUID, ids: list[UUID], audiences: list[str] | None) -> dict[UUID, list[RelatedRef]]:
    """Recall's include.related: each item's edges as {kind, direction, id, statement}, at most
    MAX_RECALL_RELATED, memory edges first."""
    edges, neighbors, threads = await edges_for(conn, project_id, ids, audiences)
    out: dict[UUID, list[RelatedRef]] = {}
    for mid, items in edges.items():
        refs = []
        for e in items[:MAX_RECALL_RELATED]:
            if "other" in e:
                refs.append(RelatedRef(kind=e["kind"], direction=e["direction"], id=e["other"],
                                       statement=neighbors[e["other"]]["statement"]))
            elif "thread" in e:
                refs.append(RelatedRef(kind=e["kind"], direction=e["direction"], id=e["thread"], statement=threads[e["thread"]]["title"]))
            else:
                f = related_file(e["file"])
                state = "" if f.current is not False else (" (deleted)" if f.current_version is None else " (changed)")
                refs.append(RelatedRef(kind=e["kind"], direction=e["direction"], id=None, statement=f"{f.path}@{f.version[:7]}{state}"))
        out[mid] = refs
    return out


# ------------------------------------------------------------------ the graph


def _grew(since: datetime | None, *times: datetime | None) -> bool:
    return since is not None and any(t is not None and t >= since for t in times)


async def graph(conn, project_id: UUID, *, since: datetime | None = None, thread: UUID | None = None,
                subject: str | None = None, everything: bool = False) -> GraphOut | None:
    """A neighborhood for the console: facts (with their one-hop fact neighbors), the threads they
    were decided in, the files they are anchored to, and the people who said them, at most
    MAX_GRAPH_NODES nodes. Filtered by thread and/or subject; otherwise the open threads and the most
    recent facts. `everything`: every thread whatever its status (most recently active first, after
    the facts, while there is room) and the most recent facts; still capped, `truncated` when
    something was left out. Returns None when `thread` is not one of the project's threads."""
    clauses, params = ["project_id = %s", "status IN ('active', 'stale', 'superseded')"], [project_id]
    if thread:
        clauses.append("thread_id = %s")
        params.append(thread)
    if subject:
        clauses.append("%s = ANY(subjects)")
        params.append(subject.strip().lower())
    seeds = await (await conn.execute(
        f"SELECT {MEMORY_COLUMNS} FROM memory_items WHERE {' AND '.join(clauses)} ORDER BY recorded_at DESC LIMIT %s",
        [*params, MAX_GRAPH_NODES + 1])).fetchall()
    if thread:
        hub = await (await conn.execute(THREAD_SELECT + " WHERE project_id = %s AND id = %s", (project_id, thread))).fetchone()
        if not hub:
            return None
        hubs = [hub]
    elif subject:
        hubs = []
    elif everything:
        hubs = []  # every thread is added after the facts, below
    else:
        hubs = await (await conn.execute(
            THREAD_SELECT + " WHERE project_id = %s AND status IN ('open', 'blocked') ORDER BY last_activity_at DESC NULLS LAST LIMIT %s",
            (project_id, MAX_OPEN_THREADS))).fetchall()
    truncated = len(seeds) > MAX_GRAPH_NODES
    seeds = seeds[:MAX_GRAPH_NODES]

    facts = {r["id"]: r for r in seeds}
    edges, neighbors, threads = await edges_for(conn, project_id, list(facts))
    for n in neighbors.values():  # one hop out: the older value, the other side of a contradiction, ...
        facts.setdefault(n["id"], n)
    extra = [i for i in facts if i not in edges]
    if extra:  # the neighbors' own edges, so edges among them are drawn too
        more, _, more_threads = await edges_for(conn, project_id, extra)
        edges.update(more)
        threads.update(more_threads)
    for t in hubs:
        threads.setdefault(t["id"], t)
    said_by = await speakers(conn, project_id, list(facts))

    nodes: dict[str, GraphNode] = {}

    def add(node: GraphNode) -> None:
        nodes.setdefault(node.id, node)

    def thread_node(t) -> GraphNode:
        return GraphNode(id=f"thread:{t['id']}", type="thread", label=t["title"], thread=related_thread(t),
                         grew=_grew(since, t["first_seen_at"], t["last_activity_at"]))

    for t in hubs:
        add(thread_node(t))
    evidence = await evidence_for(conn, list(facts))
    chosen: list[UUID] = []
    for fid, r in facts.items():
        attached = []
        if r.get("thread_id") in threads:
            attached.append(f"thread:{r['thread_id']}")
        attached += [f"file:{e['file']['repo']}:{e['file']['path']}" for e in edges.get(fid, []) if "file" in e]
        attached += [f"person:{p}" for p in said_by.get(fid, [])]
        new = 1 + len({a for a in attached if a not in nodes})
        if len(nodes) + new > MAX_GRAPH_NODES:
            truncated = True
            break
        chosen.append(fid)
        # Grew: learned since (recorded_at is when its source happened, not when it was processed), or
        # changed since (superseded or retired from valid_to on).
        grew = _grew(since, r["recorded_at"], r["valid_to"])
        add(GraphNode(id=str(fid), type="fact", label=r["statement"], memory=memory_out(r, evidence[fid]),
                      said_by=said_by.get(fid, []), grew=grew))
        if r.get("thread_id") in threads:
            add(thread_node(threads[r["thread_id"]]))
        for e in edges.get(fid, []):
            if "file" in e:
                f = e["file"]
                add(GraphNode(id=f"file:{f['repo']}:{f['path']}", type="file", label=f["path"],
                              file=GraphFile(repo=f["repo"], path=f["path"], version=f["current_blob"],
                                             deleted=f["reported"] and f["current_blob"] is None)))
        for p in said_by.get(fid, []):
            add(GraphNode(id=f"person:{p}", type="person", label=p))
            nodes[f"person:{p}"].grew |= grew  # a person grew when something they said did
    if everything and not thread and not subject:
        rest = await (await conn.execute(
            THREAD_SELECT + " WHERE project_id = %s ORDER BY last_activity_at DESC NULLS LAST LIMIT %s",
            (project_id, MAX_GRAPH_NODES + 1))).fetchall()
        for t in rest:
            if f"thread:{t['id']}" in nodes:
                continue
            if len(nodes) >= MAX_GRAPH_NODES:
                truncated = True
                break
            add(thread_node(t))
    # A file grew when a fact was anchored to it since.
    for fid in chosen:
        for e in edges.get(fid, []):
            if "file" in e and _grew(since, e["file"]["anchored_at"]):
                nodes[f"file:{e['file']['repo']}:{e['file']['path']}"].grew = True

    present = set(chosen)
    out_edges: list[GraphEdge] = []
    seen: set[tuple] = set()
    for fid in chosen:
        for e in edges.get(fid, []):
            if "other" in e:
                if e["other"] not in present or e["direction"] == "in":
                    continue  # an "in" edge is listed from the other end
                a, b = (str(fid), str(e["other"]))
                if e["kind"] == "contradicts":
                    a, b = sorted((a, b))
                edge = GraphEdge(kind=e["kind"], from_=a, to=b)
            elif "thread" in e:
                edge = GraphEdge(kind="decided_in", from_=str(fid), to=f"thread:{e['thread']}")
            else:
                f = related_file(e["file"])
                edge = GraphEdge(kind="anchored", from_=str(fid), to=f"file:{f.repo}:{f.path}", version=f.version, current=f.current)
            key = (edge.kind, edge.from_, edge.to)
            if key not in seen:
                seen.add(key)
                out_edges.append(edge)
    return GraphOut(nodes=list(nodes.values()), edges=out_edges, truncated=truncated, since=since)


async def speakers(conn, project_id: UUID, ids: list[UUID]) -> dict[UUID, list[str]]:
    """Who said each memory: the identities of the sources its evidence comes from."""
    if not ids:
        return {}
    rows = await (await conn.execute(
        "SELECT DISTINCT e.memory_id, ep.identity FROM evidence e JOIN episodes ep ON ep.project_id = e.project_id AND ep.id = e.episode_id "
        "WHERE e.project_id = %s AND e.memory_id = ANY(%s) AND ep.identity IS NOT NULL ORDER BY ep.identity",
        (project_id, ids))).fetchall()
    out: dict[UUID, list[str]] = {}
    for r in rows:
        out.setdefault(r["memory_id"], []).append(r["identity"])
    return out
