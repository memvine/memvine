// The console's client for the Memvine server. It authenticates with the
// organization's management key and names the project on every request
// (X-Memvine-Project), so one console serves every project.
import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { buildGraph, relatedFromDetail } from "./graph";
import type {
  AddResult, ApiKey, Audience, EdgeDirection, EdgeKind, GraphEdge, GraphFile, GraphNode, MemoryGraph, MemoryRelated, RelatedEdge, RelatedRef, RelatedThread, AuditEvent, Invite, Role, Episode, Identity, OrgInfo, OrgKey, OrgSummary, SessionSummary, EpisodeSummary, Member, MemoryDetail, MemoryFilter, MemoryItem, MemvineApi, Project,
  RecallResult, RequestLog, SampleResult, SynthesisHealth, SynthesisIssue, ThreadDetail, ThreadSummary, UsageDay,
} from "./types";

const BASE = (process.env.MEMVINE_API_URL ?? "").replace(/\/$/, "");
const CONFIGURED_KEY = process.env.MEMVINE_MANAGEMENT_KEY ?? "";
export const KEY_COOKIE = "mv_key";

/** The management key for this request: the one configured on the console server (single-tenant,
 *  local use), else the signed-in organization's key from its httpOnly cookie. */
export async function managementKey(): Promise<string> {
  if (CONFIGURED_KEY) return CONFIGURED_KEY;
  return (await cookies()).get(KEY_COOKIE)?.value ?? "";
}

export type Viewer = OrgInfo;

/** Who a key or sign-in session acts for, or null when the server refuses it. */
export async function whoIs(key: string): Promise<Viewer | null> {
  if (!key) return null;
  const res = await fetch(`${BASE}/v1/org`, { headers: { Authorization: `Bearer ${key}` }, cache: "no-store" });
  if (!res.ok) return null;
  return (await res.json()) as Viewer;
}

/** Unauthenticated calls of the sign-in and sign-up pages. Errors come back as their message. */
export async function publicCall<T>(path: string, body?: unknown, token?: string): Promise<{ ok: true; data: T } | { ok: false; status: number; message: string }> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${BASE}${path}`, { method: body === undefined ? "GET" : "POST", headers, body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store" });
  if (res.ok) return { ok: true, data: res.status === 204 ? (undefined as T) : ((await res.json()) as T) };
  let message = res.statusText;
  try { message = (await res.json()).error?.message ?? message; } catch { /* non-JSON */ }
  return { ok: false, status: res.status, message };
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init: { method?: string; body?: unknown; project?: string } = {}): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${await managementKey()}` };
  if (init.project) headers["X-Memvine-Project"] = init.project;
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`${BASE}${path}`, {
    method: init.method ?? "GET",
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
  });
  if (res.status === 204) return undefined as T;
  if (!res.ok) {
    let code = "error", message = res.statusText;
    try {
      const err = (await res.json()).error;
      code = err?.code ?? code;
      message = err?.message ?? message;
    } catch { /* non-JSON error body */ }
    // A signed-in key that stops working (revoked, organization deleted) ends the session.
    if (!CONFIGURED_KEY && (res.status === 401 || res.status === 403) && code === "management-key-required") {
      redirect("/logout");
    }
    throw new ApiError(res.status, code, `${res.status} ${code}: ${message}`);
  }
  return (await res.json()) as T;
}

// ---- server JSON (snake_case) -> console types

interface ServerMemory {
  id: string; kind: MemoryItem["kind"]; statement: string; subjects: string[]; valid_from: string; valid_to: string | null;
  recorded_at: string; status: MemoryItem["status"]; supersedes: string | null; superseded_by: string | null;
  assertion: MemoryItem["assertion"]; confidence: MemoryItem["confidence"]; audience: string;
  evidence: { episode_id: string; quote: string; identity?: string | null }[]; conflicts_with?: string[]; stale_reason?: string | null; scope?: "project" | "thread" | null;
  category?: MemoryItem["category"]; why?: string | null; thread_id?: string | null; review?: "new" | "kept";
  expired?: boolean; last_recalled_at?: string | null; recall_count?: number;
}

const toMemory = (m: ServerMemory): MemoryItem => ({
  id: m.id, kind: m.kind, statement: m.statement, subjects: m.subjects, validFrom: m.valid_from, validTo: m.valid_to,
  recordedAt: m.recorded_at, status: m.status, supersedes: m.supersedes, supersededBy: m.superseded_by,
  assertion: m.assertion, confidence: m.confidence, audience: m.audience,
  evidence: m.evidence.map((e) => ({ episodeId: e.episode_id, quote: e.quote, identity: e.identity ?? null })),
  conflictsWith: m.conflicts_with ?? [],
  staleReason: m.stale_reason ?? null,
  scope: m.scope ?? null,
  category: m.category ?? null,
  why: m.why ?? null,
  threadId: m.thread_id ?? null,
  expired: m.expired ?? false,
  lastRecalledAt: m.last_recalled_at ?? null,
  recallCount: m.recall_count ?? 0,
  review: m.review ?? "kept",
});

interface ServerThread {
  id: string; kind: ThreadSummary["kind"]; key: string; title: string; status: ThreadSummary["status"]; summary: string;
  first_seen_at: string; last_activity_at: string; session_count: number; knowledge_count: number;
}

interface ServerThreadDetail extends ServerThread {
  sections: { label: string; items: { text: string; date: string | null; evidence: { episode_id: string; quote: string }[] }[] }[];
  related: ServerThread[];
  knowledge: ServerMemory[];
  sources: { episode_id: string; label: string; occurred_at: string }[];
}

const toThread = (t: ServerThread): ThreadSummary => ({
  id: t.id, kind: t.kind, key: t.key, title: t.title, status: t.status, summary: t.summary ?? "",
  firstSeenAt: t.first_seen_at, lastActivityAt: t.last_activity_at,
  sessionCount: Number(t.session_count ?? 0), knowledgeCount: Number(t.knowledge_count ?? 0),
});

interface ServerProject {
  slug: string; name: string; created_at: string; tags: string[]; memory_count: number; source_count: number;
  key_count: number; last_activity_at: string | null; monthly_credit_cap: string | null; retention_days: number | null;
  month_credits: string; recall_min_relevance?: number | null; sample_source_count?: number;
  description?: string | null; unused_count?: number; unused_days?: number; review_count?: number;
}

const toProject = (p: ServerProject): Project => ({
  slug: p.slug, name: p.name, createdAt: p.created_at, tags: p.tags ?? [], memoryCount: Number(p.memory_count),
  sourceCount: Number(p.source_count), keyCount: Number(p.key_count), lastActivityAt: p.last_activity_at,
  monthlyCreditCap: p.monthly_credit_cap === null ? null : Number(p.monthly_credit_cap), retentionDays: p.retention_days,
  monthCredits: Number(p.month_credits ?? 0),
  recallMinRelevance: p.recall_min_relevance ?? null,
  sampleSourceCount: Number(p.sample_source_count ?? 0),
  description: p.description ?? null,
  unusedCount: Number(p.unused_count ?? 0),
  unusedDays: Number(p.unused_days ?? 60),
  reviewCount: Number(p.review_count ?? 0),
});

// ---- the memory graph (server/memvine_server/edges.py): GET /v1/graph, GET /v1/memories/{id}/related
// and recall's include.related. snake_case on the wire, camelCase in lib/types.ts.

interface ServerRelatedThread { id: string; key: string; kind: RelatedThread["kind"]; title: string; status: RelatedThread["status"]; summary: string }
interface ServerRelatedFile { repo: string; path: string; version: string; anchored_at: string; current_version: string | null; current: boolean | null }
interface ServerRelatedEdge {
  kind: EdgeKind; direction: EdgeDirection; item: ServerMemory | null; file: ServerRelatedFile | null; thread: ServerRelatedThread | null;
}
interface ServerGraphNode {
  id: string; type: GraphNode["type"]; label: string; grew: boolean; memory: ServerMemory | null; said_by?: string[];
  thread: ServerRelatedThread | null; file: GraphFile | null;
}
interface ServerGraph { nodes: ServerGraphNode[]; edges: GraphEdge[]; truncated: boolean; since: string | null }

const toRelatedThread = (t: ServerRelatedThread): RelatedThread => ({ id: t.id, key: t.key, kind: t.kind, title: t.title, status: t.status, summary: t.summary ?? "" });

const toRelatedEdge = (e: ServerRelatedEdge): RelatedEdge => ({
  kind: e.kind,
  direction: e.direction,
  item: e.item ? toMemory(e.item) : null,
  file: e.file ? {
    repo: e.file.repo, path: e.file.path, version: e.file.version, anchoredAt: e.file.anchored_at,
    currentVersion: e.file.current_version ?? null, current: e.file.current ?? null,
  } : null,
  thread: e.thread ? toRelatedThread(e.thread) : null,
});

const toGraphNode = (n: ServerGraphNode): GraphNode => ({
  id: n.id, type: n.type, label: n.label, grew: Boolean(n.grew),
  memory: n.memory ? toMemory(n.memory) : null,
  saidBy: n.said_by ?? [],
  thread: n.thread ? toRelatedThread(n.thread) : null,
  file: n.file ? { repo: n.file.repo, path: n.file.path, version: n.file.version ?? null, deleted: Boolean(n.file.deleted) } : null,
});

/** A 404 the route itself gave ("not-found": no such thread or memory), as against a server
 *  without the route at all (FastAPI's bare 404). */
const isMissingRoute = (e: unknown) => e instanceof ApiError && e.status === 404 && e.code !== "not-found";
interface ServerKey { id: string; name: string; key?: string | null; suffix: string; created_at: string; last_used_at: string | null; last_used_ip?: string | null; revoked_at: string | null; last_user_agent?: string | null; created_by_email?: string | null }

const toKey = (k: ServerKey): ApiKey => ({
  id: k.id, name: k.name, masked: `mv_sk_••••${k.suffix}`, createdAt: k.created_at, lastUsedAt: k.last_used_at, lastUsedIp: k.last_used_ip ?? null, revokedAt: k.revoked_at,
  lastUserAgent: k.last_user_agent ?? null, createdByEmail: k.created_by_email ?? null,
});

const notFoundAsNull = async <T>(p: Promise<T>): Promise<T | null> => {
  try {
    return await p;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
};

export const liveApi: MemvineApi = {
  async listProjects(): Promise<Project[]> {
    return (await call<ServerProject[]>("/v1/projects")).map(toProject);
  },
  async createProject(name, tags, options = {}) {
    const body: Record<string, unknown> = { name, tags };
    if (options.description) body.description = options.description;
    if (options.sample) body.sample = true;
    const p = toProject(await call<ServerProject>("/v1/projects", { method: "POST", body }));
    const key = await call<ServerKey>(`/v1/projects/${p.slug}/keys`, { method: "POST", body: { name: "default" } });
    return { project: { ...p, keyCount: 1 }, secret: key.key! };
  },
  async updateProject(slug, patch) {
    const body: Record<string, unknown> = {};
    if (patch.name !== undefined) body.name = patch.name;
    if (patch.tags !== undefined) body.tags = patch.tags;
    if (patch.monthlyCreditCap !== undefined) body.monthly_credit_cap = patch.monthlyCreditCap;
    if (patch.retentionDays !== undefined) body.retention_days = patch.retentionDays;
    if (patch.recallMinRelevance !== undefined) body.recall_min_relevance = patch.recallMinRelevance;
    await call(`/v1/projects/${slug}`, { method: "PATCH", body });
  },
  async deleteProject(slug) {
    await call(`/v1/projects/${slug}?confirm=${encodeURIComponent(slug)}`, { method: "DELETE" });
  },

  async listKeys(project) {
    return (await call<ServerKey[]>(`/v1/projects/${project}/keys`)).map(toKey);
  },
  async createKey(project, name) {
    const k = await call<ServerKey>(`/v1/projects/${project}/keys`, { method: "POST", body: { name } });
    return { key: toKey(k), secret: k.key! };
  },
  async revokeKey(project, id) {
    await call(`/v1/projects/${project}/keys/${id}`, { method: "DELETE" });
  },
  async keyConnection(project, id) {
    const c = await notFoundAsNull(call<{ first_call_at: string | null; first_session_at: string | null; first_recall_at: string | null; last_used_at: string | null; last_user_agent: string | null; calls: number }>(
      `/v1/projects/${project}/keys/${encodeURIComponent(id)}/connection`));
    return c && { firstCallAt: c.first_call_at, firstSessionAt: c.first_session_at, firstRecallAt: c.first_recall_at,
                  lastUsedAt: c.last_used_at, lastUserAgent: c.last_user_agent, calls: c.calls };
  },

  async listMemories(project, filter: MemoryFilter = {}) {
    const params = new URLSearchParams({ limit: "200" });
    if (filter.q) params.set("q", filter.q);
    if (filter.kind) params.set("kind", filter.kind);
    if (filter.status) params.set("status", filter.status);
    if (filter.category) params.set("category", filter.category);
    if (filter.unused) params.set("unused", "true");
    if (filter.review) params.set("review", filter.review);
    const rows = (await call<ServerMemory[]>(`/v1/memories?${params}`, { project })).map(toMemory);
    // A server that predates categories (or review) ignores the parameter; filter here so the answer is still right.
    return rows
      .filter((m) => !filter.category || m.category === filter.category)
      .filter((m) => filter.review !== "new" || (m.review === "new" && (m.status === "active" || m.status === "stale")))
      .filter((m) => filter.review !== "kept" || m.review === "kept");
  },
  async listThreads(project, filter = {}): Promise<ThreadSummary[]> {
    const params = new URLSearchParams();
    if (filter.status) params.set("status", filter.status);
    if (filter.kind) params.set("kind", filter.kind);
    if (filter.q) params.set("q", filter.q);
    const query = params.size ? `?${params}` : "";
    // A server without threads answers 404: the screen shows its empty state.
    const rows = await notFoundAsNull(call<ServerThread[]>(`/v1/threads${query}`, { project }));
    return (rows ?? []).map(toThread);
  },
  async getThread(project, id): Promise<ThreadDetail | null> {
    const t = await notFoundAsNull(call<ServerThreadDetail>(`/v1/threads/${encodeURIComponent(id)}`, { project }));
    if (!t) return null;
    return {
      ...toThread(t),
      sections: (t.sections ?? []).map((sec) => ({
        label: sec.label,
        items: sec.items.map((i) => ({ text: i.text, date: i.date, evidence: (i.evidence ?? []).map((e) => ({ episodeId: e.episode_id, quote: e.quote })) })),
      })),
      related: (t.related ?? []).map(toThread),
      knowledge: (t.knowledge ?? []).map(toMemory),
      sources: (t.sources ?? []).map((src) => ({ episodeId: src.episode_id, label: src.label, occurredAt: src.occurred_at })),
    };
  },
  async getMemory(project, id): Promise<MemoryDetail | null> {
    const fetchOne = (mid: string) => notFoundAsNull(call<ServerMemory>(`/v1/memories/${mid}`, { project }));
    const memory = await fetchOne(id);
    if (!memory) return null;
    // Walk the supersession chain in both directions.
    const chain: ServerMemory[] = [memory];
    for (let cur = memory, guard = 0; cur.supersedes && guard < 20; guard++) {
      const prev = await fetchOne(cur.supersedes);
      if (!prev) break;
      chain.unshift(prev);
      cur = prev;
    }
    for (let cur = memory, guard = 0; cur.superseded_by && guard < 20; guard++) {
      const next = await fetchOne(cur.superseded_by);
      if (!next) break;
      chain.push(next);
      cur = next;
    }
    const episodes = (
      await Promise.all([...new Set(memory.evidence.map((e) => e.episode_id))].map((eid) => liveApi.getEpisode(project, eid)))
    ).filter((e): e is Episode => e !== null);
    const conflicts = (await Promise.all((memory.conflicts_with ?? []).map(fetchOne)))
      .filter((m): m is ServerMemory => m !== null).map(toMemory);
    return { memory: toMemory(memory), chain: chain.map(toMemory), conflicts, episodes };
  },
  async deleteSession(project, id) {
    return call<{ messages: number; sources: number; memories: number }>(`/v1/sessions/${encodeURIComponent(id)}`, { method: "DELETE", project });
  },
  async listSessions(project): Promise<SessionSummary[]> {
    const rows = await call<{
      id: string; agent: string | null; identity: string | null; label: string | null; status: "live" | "synthesized";
      started_at: string; last_event_at: string; turns: number; state_through: number;
      state: { goal?: string; decisions?: string[]; open_questions?: string[]; in_play?: string[]; recent_facts?: string[]; progress_claims?: string[] };
    }[]>("/v1/sessions?limit=200", { project });
    return rows.map((r) => ({
      id: r.id, agent: r.agent, identity: r.identity, label: r.label, status: r.status, startedAt: r.started_at,
      lastEventAt: r.last_event_at, turns: r.turns, stateThrough: r.state_through,
      state: {
        goal: r.state.goal ?? "", decisions: r.state.decisions ?? [], openQuestions: r.state.open_questions ?? [],
        inPlay: r.state.in_play ?? [], recentFacts: r.state.recent_facts ?? [], progressClaims: r.state.progress_claims ?? [],
      },
    }));
  },
  async listAudiences(project): Promise<Audience[]> {
    const rows = await call<{ slug: string; kind: Audience["kind"]; members: string[]; conditions: Audience["conditions"]; memory_count: number }[]>(
      "/v1/audiences", { project });
    return rows.map((r) => ({ slug: r.slug, kind: r.kind, members: r.members, conditions: r.conditions, memoryCount: Number(r.memory_count) }));
  },
  async putAudience(project, slug, body) {
    await call(`/v1/audiences/${encodeURIComponent(slug)}`, { method: "PUT", body, project });
  },
  async deleteAudience(project, slug) {
    await call(`/v1/audiences/${encodeURIComponent(slug)}`, { method: "DELETE", project });
  },
  async listIdentities(project): Promise<Identity[]> {
    const rows = await call<{ identity: string; traits: Record<string, string>; origin: string; last_seen_at: string | null }[]>(
      "/v1/identities?limit=1000", { project });
    return rows.map((r) => ({ identity: r.identity, traits: r.traits, origin: r.origin, lastSeenAt: r.last_seen_at }));
  },
  async putIdentity(project, identity, traits) {
    await call(`/v1/identities/${encodeURIComponent(identity)}`, { method: "PUT", body: { traits }, project });
  },
  async getGraph(project, query = {}): Promise<MemoryGraph> {
    const params = new URLSearchParams();
    if (query.since) params.set("since", query.since);
    if (query.thread) params.set("thread", query.thread.replace(/^thread:/, ""));
    if (query.subject) params.set("subject", query.subject);
    if (query.all) params.set("all", "true");
    try {
      const g = await call<ServerGraph>(`/v1/graph${params.size ? `?${params}` : ""}`, { project });
      return { nodes: (g.nodes ?? []).map(toGraphNode), edges: g.edges ?? [], since: g.since ?? null, truncated: Boolean(g.truncated) };
    } catch (e) {
      // No such thread: an empty neighborhood.
      if (e instanceof ApiError && e.status === 404 && !isMissingRoute(e)) return { nodes: [], edges: [], since: null, truncated: false };
      if (!isMissingRoute(e)) throw e;
    }
    // A server without /v1/graph: draw what the memory rows already say (supersedes, contradicts,
    // decided_in, and anchored for memories whose file changed).
    const [memories, threads] = await Promise.all([liveApi.listMemories(project), liveApi.listThreads(project)]);
    return buildGraph({ memories, threads, query });
  },
  async getRelated(project, id): Promise<MemoryRelated | null> {
    try {
      const r = await call<{ memory: ServerMemory; edges: ServerRelatedEdge[] }>(`/v1/memories/${encodeURIComponent(id)}/related`, { project });
      return { memory: toMemory(r.memory), edges: (r.edges ?? []).map(toRelatedEdge) };
    } catch (e) {
      if (e instanceof ApiError && e.status === 404 && !isMissingRoute(e)) return null;
      if (!isMissingRoute(e)) throw e;
    }
    // A server without the route: build it from the memory's own detail.
    const detail = await liveApi.getMemory(project, id);
    return detail && relatedFromDetail(detail, await liveApi.listThreads(project));
  },
  async correctMemory(project, id, statement) {
    const old = await call<ServerMemory>(`/v1/memories/${encodeURIComponent(id)}`, { project });
    const created = await call<{ memory: ServerMemory; relation: string; related: string | null }>("/v1/memories", {
      method: "POST", project,
      // `supersedes`: the person said this replaces that fact, so the server supersedes it without
      // asking the judge, and the correction keeps its category and thread. kind, subjects and
      // audience are the old fact's own, for a server that does not know `supersedes` yet.
      body: { statement, supersedes: id, kind: old.kind, subjects: old.subjects.slice(0, 3), assertion: "stated", audience: old.audience },
    });
    // A server without `supersedes` reconciles it like any statement, and the judge may keep both.
    // The person still said the old one is wrong: retire it.
    if (!(created.relation === "update" && created.related === id) && created.memory.id !== id) {
      await call(`/v1/memories/${encodeURIComponent(id)}`, { method: "PATCH", body: { status: "retired" }, project });
    }
    return toMemory(created.memory);
  },
  async setMemoryStatus(project, id, status) {
    await call(`/v1/memories/${id}`, { method: "PATCH", body: { status }, project });
  },
  async confirmMemory(project, id) {
    await call(`/v1/memories/${id}`, { method: "PATCH", body: { confirm: true }, project });
  },

  async listEpisodes(project): Promise<EpisodeSummary[]> {
    const rows = await call<{
      id: string; label: string | null; identity: string | null; occurred_at: string; bytes: number; job_id: number | null;
      job_status: EpisodeSummary["jobStatus"]; job_error: string | null; job_result: EpisodeSummary["jobResult"]; memory_count: number;
      job_attempts?: number | null; job_retry_at?: string | null; thread_count?: number | null;
    }[]>("/v1/episodes?limit=200", { project });
    return rows.map((r) => ({
      id: r.id, label: r.label, identity: r.identity, occurredAt: r.occurred_at, bytes: r.bytes, jobId: r.job_id,
      jobStatus: r.job_status, jobError: r.job_error, jobResult: r.job_result, memoryCount: Number(r.memory_count),
      jobAttempts: Number(r.job_attempts ?? 0), jobRetryAt: r.job_retry_at ?? null, threadCount: Number(r.thread_count ?? 0),
    }));
  },
  async getEpisode(project, id): Promise<Episode | null> {
    const r = await notFoundAsNull(
      call<{ id: string; label: string | null; identity: string | null; occurred_at: string; content: string }>(`/v1/episodes/${id}`, { project }),
    );
    return r && { id: r.id, label: r.label, identity: r.identity, occurredAt: r.occurred_at, content: r.content };
  },
  async retryJob(project, jobId) {
    await call(`/v1/jobs/${jobId}/retry`, { method: "POST", project });
  },
  async synthesisHealth(project): Promise<SynthesisHealth | null> {
    let r: { healthy: boolean; scope: SynthesisHealth["scope"]; issues: SynthesisIssue[]; failed_count: number;
             oldest_queued_minutes: number | null; worker_last_poll_at: string | null };
    try {
      r = await call(`/v1/health/synthesis?project=${encodeURIComponent(project)}`);
    } catch (e) {
      // Members may not see it, and a server from before the route has none: no banner either way.
      if (e instanceof ApiError && (e.status === 403 || e.status === 404)) return null;
      throw e;
    }
    return { healthy: r.healthy, scope: r.scope, issues: r.issues, failedCount: r.failed_count,
             oldestQueuedMinutes: r.oldest_queued_minutes, workerLastPollAt: r.worker_last_poll_at };
  },

  async listRequests(project): Promise<RequestLog[]> {
    const rows = await call<{ id: number; at: string; method: string; path: string; status: number; latency_ms: number; key_name: string | null; detail?: string | null }[]>(
      "/v1/requests?limit=200", { project },
    );
    return rows.map((r) => ({ id: r.id, at: r.at, method: r.method, path: r.path, status: r.status, latencyMs: r.latency_ms, keyName: r.key_name, detail: r.detail ?? null }));
  },
  async getUsage(project): Promise<UsageDay[]> {
    const rows = await call<{ date: string; ingest: number; recall: number; ingest_credits: string; recall_credits: string }[]>("/v1/usage?days=30", { project });
    return rows.map((r) => ({ date: r.date, ingest: Number(r.ingest), recall: Number(r.recall),
      ingestCredits: Number(r.ingest_credits ?? 0), recallCredits: Number(r.recall_credits ?? 0) }));
  },
  async orgInfo(): Promise<OrgInfo> {
    return call<OrgInfo>("/v1/org");
  },
  async listAudit(): Promise<AuditEvent[]> {
    return call<AuditEvent[]>("/v1/audit?limit=50");
  },
  async listOrgKeys(): Promise<OrgKey[]> {
    const rows = await call<{ id: string; name: string; suffix: string; created_at: string; last_used_at: string | null; revoked_at: string | null }[]>("/v1/org/keys");
    return rows.map((k) => ({ id: k.id, name: k.name, masked: `mv_mk_••••${k.suffix}`, createdAt: k.created_at, lastUsedAt: k.last_used_at, revokedAt: k.revoked_at }));
  },
  async createOrgKey(name) {
    const k = await call<{ id: string; name: string; suffix: string; created_at: string; key: string }>("/v1/org/keys", { method: "POST", body: { name } });
    return { key: { id: k.id, name: k.name, masked: `mv_mk_••••${k.suffix}`, createdAt: k.created_at, lastUsedAt: null, revokedAt: null }, secret: k.key };
  },
  async revokeOrgKey(id) {
    await call(`/v1/org/keys/${id}`, { method: "DELETE" });
  },
  async listOrgs(): Promise<OrgSummary[]> {
    const rows = await call<{ slug: string; name: string; created_at: string; project_count: number }[]>("/v1/orgs");
    return rows.map((o) => ({ slug: o.slug, name: o.name, createdAt: o.created_at, projectCount: Number(o.project_count) }));
  },
  async createOrg(name) {
    const o = await call<{ slug: string; key: string }>("/v1/orgs", { method: "POST", body: { name } });
    return { slug: o.slug, secret: o.key };
  },
  async listMembers(): Promise<Member[]> {
    const rows = await call<{ id: string; email: string; name: string; role: Role; joined_at: string; last_login_at: string | null }[]>("/v1/org/members");
    return rows.map((m) => ({ id: m.id, email: m.email, name: m.name, role: m.role, joinedAt: m.joined_at, lastLoginAt: m.last_login_at }));
  },
  async changeRole(userId, role) {
    await call(`/v1/org/members/${userId}`, { method: "PATCH", body: { role } });
  },
  async removeMember(userId) {
    await call(`/v1/org/members/${userId}`, { method: "DELETE" });
  },
  async listInvites(): Promise<Invite[]> {
    const rows = await call<{ id: string; email: string | null; role: Role; created_by: string; created_at: string; expires_at: string; accepted_at: string | null; accepted_by: string | null }[]>("/v1/org/invites");
    return rows.map((i) => ({ id: i.id, email: i.email, role: i.role, createdBy: i.created_by, createdAt: i.created_at, expiresAt: i.expires_at, acceptedAt: i.accepted_at, acceptedBy: i.accepted_by }));
  },
  async createInvite(email, role) {
    const r = await call<{ token: string }>("/v1/org/invites", { method: "POST", body: { email, role } });
    return { token: r.token };
  },
  async revokeInvite(id) {
    await call(`/v1/org/invites/${id}`, { method: "DELETE" });
  },
  async changePassword(current, next) {
    await call("/v1/auth/password", { method: "POST", body: { current, new: next } });
  },
  async switchOrg(slug) {
    return (await call<{ token: string }>("/v1/auth/switch", { method: "POST", body: { org: slug } })).token;
  },

  async recall(project, query, budget, options = {}): Promise<RecallResult> {
    // abstain_reason, min_relevance, top_relevance and a thread's background flag are newer than
    // the rest of the response; an older server leaves them out.
    const r = await call<{
      briefing: string; tokens: number; abstained: boolean; abstain_reason?: string | null; min_relevance?: number | null; top_relevance?: number | null;
      standing?: string[];
      threads?: { id: string; key: string; title: string; summary: string; background?: boolean }[];
      items: { memory: ServerMemory; score?: number | null; previous: string | null; related?: RelatedRef[] | null }[];
    }>("/v1/recall", { method: "POST", body: options.related ? { query, budget, include: { related: true } } : { query, budget }, project });
    return {
      briefing: r.briefing, tokens: r.tokens, abstained: r.abstained,
      abstainReason: r.abstain_reason ?? null, minRelevance: r.min_relevance ?? null, topRelevance: r.top_relevance ?? null,
      standing: r.standing ?? [],
      pages: (r.threads ?? []).map((t) => ({ title: `${t.key} · ${t.title}`, summary: t.summary, background: Boolean(t.background), threadId: t.id })),
      items: r.items.map((i) => ({ ...toMemory(i.memory), previous: i.previous, score: i.score ?? null, ...(i.related ? { related: i.related } : {}) })),
    };
  },
  async loadSample(project): Promise<SampleResult> {
    return call<SampleResult>("/v1/sample", { method: "POST", project });
  },
  async deleteSample(project): Promise<SampleResult> {
    return call<SampleResult>("/v1/sample", { method: "DELETE", project });
  },
  async addNow(project, content): Promise<AddResult> {
    const id = `paste-${new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14)}-${Math.random().toString(36).slice(2, 6)}`;
    const r = await call<{
      items: { id: string; outcome: AddResult["outcome"] }[];
      instant: { status: AddResult["status"]; reason: string | null; memories: { memory: ServerMemory; edges: ServerRelatedEdge[] }[] } | null;
    }>("/v1/ingest", {
      method: "POST", project,
      body: { instant: true, items: [{ id, content, timestamp: new Date().toISOString(), label: "Pasted in the console", metadata: { source: "console" } }] },
    });
    const instant = r.instant ?? { status: "queued" as const, reason: "This server queues every source; it predates instant ingest.", memories: [] };
    return {
      sourceId: id, outcome: r.items[0]?.outcome ?? "queued", status: instant.status, reason: instant.reason,
      memories: instant.memories.map((m) => ({ memory: toMemory(m.memory), edges: (m.edges ?? []).map(toRelatedEdge) })),
    };
  },
};
