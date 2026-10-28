// Building the memory graph from rows the console already has. The mock backend uses this as its
// GET /v1/graph and GET /v1/memories/{id}/related; the live client falls back to it when the
// server predates those routes, drawing what the plain memory fields already say. Shapes and node
// ids follow the server's (server/memvine_server/edges.py).
import type {
  EdgeDirection, EdgeKind, GraphEdge, GraphNode, GraphQuery, MemoryDetail, MemoryGraph, MemoryItem, MemoryRelated, RelatedEdge, RelatedRef,
  RelatedThread, ThreadSummary,
} from "./types";

export const GRAPH_NODE_CAP = 300;
export const RELATED_CAP = 6;
/** The order /related lists edges in. */
const EDGE_ORDER: EdgeKind[] = ["supersedes", "contradicts", "extends", "derived", "decided_in", "anchored"];

/** A file anchor as a backend stores it (memory_anchors). */
export interface FileAnchor {
  memoryId: string;
  repo: string;
  path: string;
  /** The blob the memory is anchored to. */
  version: string;
  /** The blob last reported for the file; null when deleted or never reported. */
  currentVersion: string | null;
  anchoredAt: string;
}

/** An extends or derived link: `from` builds on `to` (memory_links). */
export interface MemoryLink {
  from: string;
  to: string;
  kind: "extends" | "derived";
}

export const threadNodeId = (id: string) => `thread:${id}`;
export const fileNodeId = (repo: string, path: string) => `file:${repo}:${path}`;
export const personNodeId = (identity: string) => `person:${identity}`;
/** "thread:<uuid>" -> "<uuid>". */
export const threadIdOf = (nodeId: string) => nodeId.replace(/^thread:/, "");

export const relatedThread = (t: ThreadSummary): RelatedThread =>
  ({ id: t.id, key: t.key, kind: t.kind, title: t.title, status: t.status, summary: t.summary });

/** "package.json changed since 12 Oct 2026" -> "package.json": the file a stale memory names. */
export function staleFile(reason: string | null | undefined): string | null {
  const m = reason?.match(/^(\S+)\s+(changed|is gone|was removed|was deleted)/);
  return m ? m[1] : null;
}

const after = (iso: string | null | undefined, since: string | null) => Boolean(iso && since && iso > since);
const speakers = (m: MemoryItem) => [...new Set(m.evidence.map((e) => e.identity).filter((x): x is string => Boolean(x)))];

/** One week before now, the default "grew" window. */
export function weekAgo(now = Date.now()): string {
  return new Date(now - 7 * 86_400_000).toISOString();
}

const anchorCurrent = (a: Pick<FileAnchor, "version" | "currentVersion">): boolean | null =>
  a.currentVersion === null ? null : a.currentVersion === a.version;

export function buildGraph(input: {
  memories: MemoryItem[];
  threads: ThreadSummary[];
  anchors?: FileAnchor[];
  links?: MemoryLink[];
  since?: string | null;
  query?: GraphQuery;
}): MemoryGraph {
  const since = input.since ?? input.query?.since ?? weekAgo();
  // Forgotten (retired) facts leave the graph; superseded ones stay, dimmed, as history.
  const memories = input.memories.filter((m) => m.status !== "retired");
  const byId = new Map(memories.map((m) => [m.id, m]));
  const nodes = new Map<string, GraphNode>();
  const edges: GraphEdge[] = [];
  const seen = new Set<string>();
  const addEdge = (kind: EdgeKind, from: string, to: string, extra: Partial<GraphEdge> = {}) => {
    const key = kind === "contradicts" ? `${kind}:${[from, to].sort().join("|")}` : `${kind}:${from}|${to}`;
    if (seen.has(key) || from === to) return;
    seen.add(key);
    edges.push({ kind, from, to, version: null, current: null, ...extra });
  };
  const blank = { memory: null, saidBy: [], thread: null, file: null };

  for (const t of input.threads) {
    nodes.set(threadNodeId(t.id), { ...blank, id: threadNodeId(t.id), type: "thread", label: t.title, grew: after(t.lastActivityAt, since), thread: relatedThread(t) });
  }
  for (const m of memories) {
    nodes.set(m.id, { ...blank, id: m.id, type: "fact", label: m.statement, grew: after(m.recordedAt, since) || after(m.validTo, since), memory: m, saidBy: speakers(m) });
  }

  const anchored = new Set<string>();
  for (const a of input.anchors ?? []) {
    if (!byId.has(a.memoryId)) continue;
    const id = fileNodeId(a.repo, a.path);
    nodes.set(id, { ...blank, id, type: "file", label: a.path, grew: false, file: { repo: a.repo, path: a.path, version: a.currentVersion, deleted: false } });
    addEdge("anchored", a.memoryId, id, { version: a.version, current: anchorCurrent(a) });
    anchored.add(a.memoryId);
  }

  for (const m of memories) {
    if (m.supersedes && byId.has(m.supersedes)) addEdge("supersedes", m.id, m.supersedes);
    for (const c of m.conflictsWith ?? []) if (byId.has(c)) addEdge("contradicts", m.id, c);
    if (m.threadId && nodes.has(threadNodeId(m.threadId))) addEdge("decided_in", m.id, threadNodeId(m.threadId));
    // A stale memory with no recorded anchor still names its file: draw that edge broken.
    const file = !anchored.has(m.id) ? staleFile(m.staleReason) : null;
    if (file) {
      const id = fileNodeId("", file);
      nodes.set(id, { ...blank, id, type: "file", label: file, grew: false, file: { repo: "", path: file, version: null, deleted: false } });
      addEdge("anchored", m.id, id, { current: false });
    }
    for (const who of speakers(m)) {
      const id = personNodeId(who);
      if (!nodes.has(id)) nodes.set(id, { ...blank, id, type: "person", label: who, grew: false });
    }
  }
  for (const l of input.links ?? []) if (byId.has(l.from) && byId.has(l.to)) addEdge(l.kind, l.from, l.to);

  return scopeGraph({ nodes: [...nodes.values()], edges, since, truncated: false }, input.query);
}

/** Narrow a graph to one thread's or one subject's neighborhood (matches plus one-hop neighbours), then cap it. */
export function scopeGraph(graph: MemoryGraph, query: GraphQuery = {}): MemoryGraph {
  let keep: Set<string> | null = null;
  if (query.thread || query.subject) {
    const thread = query.thread ? threadNodeId(threadIdOf(query.thread)) : null;
    const seed = graph.nodes.filter((n) =>
      (thread && (n.id === thread || (n.memory && n.memory.threadId && threadNodeId(n.memory.threadId) === thread))) ||
      (query.subject && n.memory?.subjects.includes(query.subject)));
    keep = new Set(seed.map((n) => n.id));
    for (const e of graph.edges) if (keep.has(e.from) || keep.has(e.to)) { keep.add(e.from); keep.add(e.to); }
    for (const n of graph.nodes) if (keep.has(n.id)) for (const p of n.saidBy) keep.add(personNodeId(p));
  }
  let nodes = keep ? graph.nodes.filter((n) => keep!.has(n.id)) : graph.nodes;
  const truncated = nodes.length > GRAPH_NODE_CAP;
  if (truncated) nodes = nodes.slice(0, GRAPH_NODE_CAP);
  const ids = new Set(nodes.map((n) => n.id));
  return { ...graph, nodes, edges: graph.edges.filter((e) => ids.has(e.from) && ids.has(e.to)), truncated: graph.truncated || truncated };
}

const sortEdges = (edges: RelatedEdge[]) => edges.sort((a, b) => EDGE_ORDER.indexOf(a.kind) - EDGE_ORDER.indexOf(b.kind));

/** GET /v1/memories/{id}/related, answered from a graph. */
export function relatedFromGraph(graph: MemoryGraph, id: string): MemoryRelated | null {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const node = byId.get(id);
  if (!node?.memory) return null;
  const edges: RelatedEdge[] = [];
  for (const e of graph.edges) {
    const out = e.from === id;
    if (!out && e.to !== id) continue;
    const other = byId.get(out ? e.to : e.from);
    if (!other) continue;
    const direction: EdgeDirection = e.kind === "contradicts" ? "both" : out ? "out" : "in";
    if (e.kind === "anchored" && other.file) {
      edges.push({ kind: "anchored", direction: "out", item: null, thread: null,
        file: { repo: other.file.repo, path: other.file.path, version: e.version ?? "", anchoredAt: node.memory.recordedAt, currentVersion: other.file.version, current: e.current } });
    } else if (e.kind === "decided_in" && other.thread) {
      edges.push({ kind: "decided_in", direction: "out", item: null, file: null, thread: other.thread });
    } else if (other.memory) {
      edges.push({ kind: e.kind, direction, item: other.memory, file: null, thread: null });
    }
  }
  return { memory: node.memory, edges: sortEdges(edges) };
}

/** GET /v1/memories/{id}/related from a memory's detail, for a server without that route. */
export function relatedFromDetail(detail: MemoryDetail, threads: ThreadSummary[]): MemoryRelated {
  const m = detail.memory;
  const edges: RelatedEdge[] = [];
  const none = { item: null, file: null, thread: null };
  const i = detail.chain.findIndex((c) => c.id === m.id);
  if (i > 0) edges.push({ ...none, kind: "supersedes", direction: "out", item: detail.chain[i - 1] });
  if (i >= 0 && i < detail.chain.length - 1) edges.push({ ...none, kind: "supersedes", direction: "in", item: detail.chain[i + 1] });
  for (const c of detail.conflicts) edges.push({ ...none, kind: "contradicts", direction: "both", item: c });
  const thread = threads.find((t) => t.id === m.threadId);
  if (thread) edges.push({ ...none, kind: "decided_in", direction: "out", thread: relatedThread(thread) });
  const file = staleFile(m.staleReason);
  if (file) edges.push({ ...none, kind: "anchored", direction: "out", file: { repo: "", path: file, version: "", anchoredAt: m.recordedAt, currentVersion: null, current: false } });
  return { memory: m, edges };
}

/** What recall's include.related serves for one item: at most 6, in /related's order. */
export function relatedRefs(related: MemoryRelated): RelatedRef[] {
  return related.edges.slice(0, RELATED_CAP).map((e): RelatedRef => {
    if (e.kind === "anchored" && e.file) {
      const state = e.file.current === false ? (e.file.currentVersion === null ? " (deleted)" : " (changed)") : "";
      return { kind: e.kind, direction: e.direction, id: null, statement: `${e.file.path}@${e.file.version.slice(0, 7)}${state}` };
    }
    if (e.kind === "decided_in" && e.thread) return { kind: e.kind, direction: e.direction, id: e.thread.id, statement: e.thread.title };
    return { kind: e.kind, direction: e.direction, id: e.item?.id ?? null, statement: e.item?.statement ?? "" };
  });
}

/** How an edge reads from the point of view of the memory at one end. */
export function edgeVerb(kind: EdgeKind, direction: EdgeDirection): string {
  const words: Record<EdgeKind, [string, string]> = {
    supersedes: ["supersedes", "superseded by"],
    contradicts: ["contradicts", "contradicts"],
    anchored: ["anchored to", "anchors"],
    decided_in: ["decided in", "decided here"],
    extends: ["extends", "extended by"],
    derived: ["derived from", "used to derive"],
  };
  return words[kind][direction === "in" ? 1 : 0];
}

/** Whether a file node has an anchored edge to a version that changed (drawn broken). */
export function fileChanged(graph: MemoryGraph, fileId: string): boolean {
  const node = graph.nodes.find((n) => n.id === fileId);
  return Boolean(node?.file?.deleted) || graph.edges.some((e) => e.kind === "anchored" && e.to === fileId && e.current === false);
}

/** A small graph of some memories and their edges as /related lists them: the memories, the facts
 *  and threads at the other ends, and the edges between them. The memories themselves are marked
 *  grew. The paste screen draws what one source taught with it. */
export function graphOfRelated(items: MemoryRelated[]): MemoryGraph {
  const nodes = new Map<string, GraphNode>();
  const edges = new Map<string, GraphEdge>();
  const fact = (m: MemoryItem, grew: boolean): GraphNode =>
    ({ id: m.id, type: "fact", label: m.statement, grew, memory: m, saidBy: speakers(m), thread: null, file: null });
  for (const { memory } of items) nodes.set(memory.id, fact(memory, true));
  for (const { memory, edges: rel } of items) {
    for (const e of rel) {
      let edge: GraphEdge | null = null;
      if (e.item) {
        if (!nodes.has(e.item.id)) nodes.set(e.item.id, fact(e.item, false));
        const [from, to] = e.direction === "in" ? [e.item.id, memory.id] : [memory.id, e.item.id];
        edge = { kind: e.kind, from, to, version: null, current: null };
        if (e.kind === "contradicts" && from > to) edge = { ...edge, from: to, to: from };
      } else if (e.thread) {
        const id = threadNodeId(e.thread.id);
        if (!nodes.has(id)) nodes.set(id, { id, type: "thread", label: e.thread.title, grew: true, memory: null, saidBy: [], thread: e.thread, file: null });
        edge = { kind: "decided_in", from: memory.id, to: id, version: null, current: null };
      }
      if (edge) edges.set(`${edge.kind}|${edge.from}|${edge.to}`, edge);
    }
  }
  return { nodes: [...nodes.values()], edges: [...edges.values()], truncated: false, since: null };
}
