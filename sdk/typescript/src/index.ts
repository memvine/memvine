/**
 * Memvine memory API client.
 *
 *   const mv = new Memvine({ apiKey: "mv_sk_...", baseUrl: "https://api.memvine.dev" });
 *   await mv.ingest([{ id: "chat-42:1", content: "...", timestamp: new Date() }]);
 *   const { briefing } = await mv.recall("When is the launch?");
 *
 *   const session = mv.session("run-17", { agent: "my-agent", identity: "user-17" });
 *   await session.send("user", "Let's use Postgres for the queue.");
 *   const brief = await session.brief();   // after a context reset
 *   await session.close();                 // its turns become long-term memory
 */

export const VERSION = "0.1.0";

export class MemvineError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(`${status} ${code}: ${message}`);
    this.name = "MemvineError";
  }
}

export interface Episode {
  id: string;
  content: string;
  timestamp: string | Date;
  identity?: string;
  label?: string;
  metadata?: Record<string, unknown>;
  audience?: string;
}

export interface Memory {
  id: string;
  kind: "episodic" | "semantic" | "procedural" | "prospective" | "preference";
  statement: string;
  subjects: string[];
  valid_from: string;
  valid_to: string | null;
  recorded_at: string;
  status: "active" | "superseded" | "retired" | "stale";
  supersedes: string | null;
  superseded_by: string | null;
  assertion: "observed" | "stated" | "claimed" | "inferred";
  confidence: "high" | "medium" | "low";
  audience: string;
  evidence: { episode_id: string; quote: string; identity?: string | null }[];
  conflicts_with: string[];
  standing: boolean;
  stale_reason?: string | null;
  category?: "decision" | "rule" | "gotcha" | "fact" | "preference" | null;
  why?: string | null;
  thread_id?: string | null;
  scope?: "project" | "thread" | null;
  /** Past the end it stated (valid_to) while still active or stale: no longer served as current. */
  expired?: boolean;
  last_recalled_at?: string | null;
  recall_count?: number;
}

/** supersedes, contradicts, extends and derived join two facts; anchored a fact and a file; decided_in a fact and its thread. */
export type EdgeKind = "supersedes" | "contradicts" | "extends" | "derived" | "anchored" | "decided_in";
/** Seen from the memory asked about: "out" it points at the other, "in" the other points at it, "both" for contradicts. */
export type EdgeDirection = "out" | "in" | "both";

/** An edge as recall lists it beside an item. id: the other memory, the thread (decided_in), or null (anchored: "path@version"). */
export interface RelatedRef {
  kind: EdgeKind;
  direction: EdgeDirection;
  id: string | null;
  statement: string;
}

export interface RelatedThread {
  id: string;
  key: string;
  kind: "release" | "task" | "pr" | "ticket" | "topic";
  title: string;
  status: "open" | "blocked" | "done" | "abandoned";
  summary: string;
}

export interface RelatedFile {
  repo: string;
  path: string;
  version: string;
  anchored_at: string;
  current_version: string | null;
  current: boolean | null;
}

export interface RelatedEdge {
  kind: EdgeKind;
  direction: EdgeDirection;
  item: Memory | null;
  file: RelatedFile | null;
  thread: RelatedThread | null;
}

export interface MemoryRelated {
  memory: Memory;
  edges: RelatedEdge[];
}

export interface GraphNode {
  id: string;
  type: "fact" | "thread" | "file" | "person";
  label: string;
  grew: boolean;
  memory: Memory | null;
  said_by: string[];
  thread: RelatedThread | null;
  file: { repo: string; path: string; version: string | null; deleted: boolean } | null;
}

export interface GraphEdge {
  kind: EdgeKind;
  from: string;
  to: string;
  version: string | null;
  current: boolean | null;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  since: string | null;
}

/** What happened to a memory (GET /v1/memories/{id}/history, GET /v1/events). */
export type EventKind =
  | "created" | "restated" | "superseded" | "supersedes" | "contradicted" | "extended" | "confirmed" | "forgotten"
  | "restored" | "expired" | "corrected" | "merged" | "anchored" | "stale" | "revalidated" | "category_changed"
  | "refused" | "deleted";

export interface MemoryEvent {
  /** null only for a computed event (expired). */
  id: number | null;
  memory_id: string | null;
  kind: EventKind;
  /** "synthesis", "person:<email or identity>", "key:<name>", "system" or "consolidation". */
  actor: string;
  /** by / of / with / into / from / related name other memories; reason, files, repo, before / after. */
  detail: Record<string, unknown>;
  episode_id: string | null;
  at: string;
  /** created / restated: the evidence that source gave. */
  quotes: string[];
  /** The memories `detail` names, with their statement and status. */
  related: { role: string; id: string; statement: string; status: Memory["status"] }[];
}

export interface FeedEvent {
  id: number;
  memory_id: string | null;
  kind: EventKind;
  actor: string;
  detail: Record<string, unknown>;
  episode_id: string | null;
  at: string;
  /** The memory's statement; null when it was deleted. */
  statement: string | null;
  memory_status: Memory["status"] | null;
}

export interface EventFeed {
  events: FeedEvent[];
  /** Pass back as `cursor` for the next (older) page; null on the last one. */
  next_cursor: string | null;
}

export interface RecallResult {
  briefing: string;
  tokens: number;
  abstained: boolean;
  standing: string[];
  items: { memory: Memory; score: number; previous: string | null; conflicts: string[]; related: RelatedRef[] | null }[];
  threads: { id: string; key: string; title: string; status: string; summary: string }[];
  passages: { episode_id: string; occurred_at: string; content: string }[];
  pending: { session_id: string; seq: number; role: string; at: string; content: string }[];
}

export interface RecallOptions {
  budget?: number;
  identity?: string;
  asOf?: string | Date;
  sessionId?: string;
  includeHistory?: boolean;
  exclude?: string[];
  /** { related: true }: each item lists its edges in `related` (at most 6). */
  include?: { related?: boolean };
  branch?: string;
}

export interface RememberOptions {
  kind?: Memory["kind"];
  subjects?: string[];
  assertion?: Memory["assertion"];
  audience?: string;
  standing?: boolean;
  category?: "decision" | "rule" | "gotcha" | "fact" | "preference";
  why?: string;
  /** The id of a current memory this statement replaces, as a person said: no judge is asked, it
   *  supersedes that memory and inherits its kind, subjects, category, thread and audience unless given. */
  supersedes?: string;
}

const iso = (v: string | Date | undefined) => (v instanceof Date ? v.toISOString() : v);

export class Memvine {
  private baseUrl: string;
  private apiKey: string;
  private fetchImpl: typeof fetch;

  constructor(options: { apiKey: string; baseUrl?: string; fetch?: typeof fetch }) {
    if (!options.apiKey) throw new Error("apiKey is required (a project key starting with mv_sk_)");
    this.apiKey = options.apiKey;
    this.baseUrl = (options.baseUrl ?? "https://api.memvine.dev").replace(/\/+$/, "");
    this.fetchImpl = options.fetch ?? fetch;
  }

  /** @internal */
  async call<T>(method: string, path: string, body?: unknown, params?: Record<string, string | number | string[] | undefined>): Promise<T> {
    const url = new URL(this.baseUrl + path);
    for (const [k, v] of Object.entries(params ?? {})) {
      if (Array.isArray(v)) for (const item of v) url.searchParams.append(k, item);
      else if (v !== undefined) url.searchParams.set(k, String(v));
    }
    const res = await this.fetchImpl(url, {
      method,
      headers: { Authorization: `Bearer ${this.apiKey}`, "Content-Type": "application/json", "User-Agent": `memvine-sdk-ts/${VERSION}` },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 204) return undefined as T;
    if (!res.ok) {
      let code = "error", message = res.statusText;
      try {
        const err = ((await res.json()) as { error?: { code?: string; message?: string } }).error;
        code = err?.code ?? code;
        message = err?.message ?? message;
      } catch { /* not JSON */ }
      throw new MemvineError(res.status, code, message);
    }
    return (await res.json()) as T;
  }

  /** Send sources. Re-sending an id with the same content is free; new content replaces it. */
  ingest(episodes: Episode[]) {
    return this.call<{ items: { id: string; outcome: string }[]; queued: number }>("POST", "/v1/ingest", {
      items: episodes.map((e) => ({ ...e, timestamp: iso(e.timestamp) })),
    });
  }

  recall(query: string, o: RecallOptions = {}) {
    return this.call<RecallResult>("POST", "/v1/recall", {
      query, budget: o.budget, identity: o.identity, as_of: iso(o.asOf), session_id: o.sessionId,
      include_history: o.includeHistory, exclude: o.exclude ?? [], branch: o.branch,
      include: o.include,
    });
  }

  /** A memory and every edge it has, both directions. */
  related(id: string) {
    return this.call<MemoryRelated>("GET", `/v1/memories/${id}/related`);
  }

  /** Everything that happened to a memory, oldest first: created (with its evidence quotes),
   *  restated, superseded, corrected, forgotten, stale, ... */
  history(id: string) {
    return this.call<MemoryEvent[]>("GET", `/v1/memories/${id}/history`);
  }

  /** The project's activity feed, newest first. Page with the returned `next_cursor`. */
  events(o: { since?: string | Date; kind?: EventKind | EventKind[]; memoryId?: string; limit?: number; cursor?: string } = {}) {
    return this.call<EventFeed>("GET", "/v1/events", undefined, {
      since: iso(o.since), kind: o.kind === undefined ? undefined : Array.isArray(o.kind) ? o.kind : [o.kind],
      memory_id: o.memoryId, limit: o.limit, cursor: o.cursor,
    });
  }

  /** A neighborhood of the memory graph (at most 300 nodes); `since` flags what grew. Without
   *  `thread` or `subject` it is the open threads and recent facts; `all: true` is every thread too. */
  graph(o: { since?: string | Date; thread?: string; subject?: string; all?: boolean } = {}) {
    return this.call<Graph>("GET", "/v1/graph", undefined, {
      since: iso(o.since), thread: o.thread, subject: o.subject, all: o.all ? "true" : undefined,
    });
  }

  /** Store one fact directly; returns the memory and how it related to what was known. With
   *  `supersedes`, it replaces that memory (relation "update") and inherits what is not given. */
  remember(statement: string, o: RememberOptions = {}) {
    const fill = !o.supersedes; // a correction inherits these from the memory it replaces
    return this.call<{ memory: Memory; relation: string; related: string | null }>("POST", "/v1/memories", {
      statement, kind: o.kind ?? (fill ? "semantic" : undefined), subjects: o.subjects ?? (fill ? [] : undefined),
      assertion: o.assertion ?? "stated", audience: o.audience ?? (fill ? "project" : undefined), standing: o.standing,
      category: o.category, why: o.why, supersedes: o.supersedes,
    });
  }

  /** status "expired": past the end they stated. unused: not served by a recall in 60 days (for review). */
  memories(o: { q?: string; status?: Memory["status"] | "expired"; kind?: Memory["kind"]; unused?: boolean; limit?: number } = {}) {
    return this.call<Memory[]>("GET", "/v1/memories", undefined,
      { q: o.q, status: o.status, kind: o.kind, unused: o.unused ? "true" : undefined, limit: o.limit ?? 50 });
  }

  memory(id: string) {
    return this.call<Memory>("GET", `/v1/memories/${id}`);
  }

  retire(id: string) {
    return this.call<Memory>("PATCH", `/v1/memories/${id}`, { status: "retired" });
  }

  /** Delete a source and the memories drawn only from it. */
  deleteSource(episodeId: string) {
    return this.call<void>("DELETE", `/v1/episodes/${encodeURIComponent(episodeId)}`);
  }

  session(id: string, meta: { agent?: string; identity?: string; label?: string } = {}) {
    return new Session(this, id, meta);
  }
}

/** One live agent session: messages stored as sent, completed stretches become memory as it runs. */
export class Session {
  constructor(private client: Memvine, public readonly id: string, private meta: { agent?: string; identity?: string; label?: string }) {}

  send(role: "user" | "assistant" | "system" | "tool", content: string, at?: string | Date) {
    return this.sendMany([{ role, content, at: iso(at) }]);
  }

  sendMany(events: { role: string; content: string; at?: string }[]) {
    return this.client.call<Record<string, unknown>>("POST", `/v1/sessions/${encodeURIComponent(this.id)}/events`, { events, ...this.meta });
  }

  state() {
    return this.client.call<Record<string, unknown> & { turns: number; sealed_through: number; processed_through: number }>(
      "GET", `/v1/sessions/${encodeURIComponent(this.id)}`);
  }

  /** This session's working state, then relevant memory: hand it to an agent after a context reset. */
  brief(budget = 2000) {
    return this.client.call<{ text: string; tokens: number; session: Record<string, unknown> }>(
      "GET", `/v1/sessions/${encodeURIComponent(this.id)}/brief`, undefined, { budget });
  }

  /** Recall that leaves out this session's own pending messages (already in its context). */
  recall(query: string, o: RecallOptions = {}) {
    return this.client.recall(query, { ...o, sessionId: this.id });
  }

  close() {
    return this.client.call<{ episodes: number; sealed: [number, number][] }>("POST", `/v1/sessions/${encodeURIComponent(this.id)}/close`);
  }
}
