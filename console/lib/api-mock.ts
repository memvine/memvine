// In-memory backend seeded from ./mock-data, used when MEMVINE_API_URL is not
// set. State lives for the life of the dev server process.
import "server-only";
import { randomBytes } from "node:crypto";
import { seedData, seedMembers, seedProjects, type MockThread, type ProjectData } from "./mock-data";
import { buildGraph, relatedFromGraph, relatedRefs } from "./graph";
import { buildSample, SAMPLE_PREFIX } from "./sample-data";
import { simulateInstant } from "./mock-instant";
import { INSTANT_MAX_CHARS } from "./types";
import type {
  ApiKey, Episode, EpisodeSummary, GraphQuery, MemoryDetail, MemoryFilter, MemoryItem, MemvineApi, Project, RecallResult, SampleResult, ThreadDetail, ThreadSummary,
} from "./types";

function threadSummary(data: ProjectData, t: MockThread): ThreadSummary {
  return {
    id: t.id, kind: t.kind, key: t.key, title: t.title, status: t.status, summary: t.summary, firstSeenAt: t.firstSeenAt,
    lastActivityAt: t.lastActivityAt, sessionCount: t.sessionCount,
    knowledgeCount: data.memories.filter((m) => m.threadId === t.id && m.category && m.status !== "superseded").length,
  };
}

interface Store {
  projects: Project[];
  data: Record<string, ProjectData>;
  /** When the store was seeded (ms); pending sources' synthesis is timed from it. */
  startedAt: number;
}

// Kept on globalThis so dev-server hot reloads don't reset created keys or edits.
const globalStore = globalThis as unknown as { __memvineStore?: Store };
function store(): Store {
  if (!globalStore.__memvineStore) {
    globalStore.__memvineStore = { projects: structuredClone(seedProjects), data: structuredClone(seedData), startedAt: Date.now() };
  }
  return globalStore.__memvineStore;
}

function projectData(slug: string): ProjectData {
  const data = store().data[slug];
  if (!data) throw new Error(`unknown project: ${slug}`);
  withSpeakers(data);
  withReview(data);
  return data;
}

/** Seeded memories carry no review state: the last week's are new (awaiting review), older ones kept. */
function withReview(data: ProjectData) {
  for (const m of data.memories) if (m.review === undefined) m.review = m.recordedAt >= "2026-10-21" ? "new" : "kept";
}

const awaitingReview = (m: MemoryItem) => m.review === "new" && (m.status === "active" || m.status === "stale");

/** Evidence carries who said it, as the server reports it: the identity of the quote's source. */
function withSpeakers(data: ProjectData) {
  const identityOf = new Map(data.episodes.map((e) => [e.id, e.identity]));
  for (const m of data.memories) {
    for (const e of m.evidence) if (e.identity === undefined) e.identity = identityOf.get(e.episodeId) ?? null;
  }
}

/** GET /v1/graph over the mock store. */
function graphOf(data: ProjectData, query: GraphQuery = {}) {
  return buildGraph({
    memories: data.memories,
    threads: data.threads.map((t) => threadSummary(data, t)),
    anchors: data.anchors,
    links: data.links,
    query,
  });
}

const mockInvites: import("./types").Invite[] = [];
const mockOrgKeys: import("./types").OrgKey[] = [
  { id: "ok_1", name: "default", masked: "mv_mk_••••9c2e", createdAt: "2026-09-01T00:00:00Z", lastUsedAt: "2026-10-25T08:00:00Z", revokedAt: null },
];

// Memory aging, as the server derives it (store.py EXPIRED / UNUSED): an active or stale memory past
// its stated end is expired; one older than UNUSED_DAYS that no recall served in that time is unused.
const UNUSED_DAYS = 60;
const isExpired = (m: MemoryItem) => (m.status === "active" || m.status === "stale") && Boolean(m.validTo) && m.validTo! <= new Date().toISOString();
const isUnused = (m: MemoryItem) => {
  const cutoff = new Date(Date.now() - UNUSED_DAYS * 86_400_000).toISOString();
  return (m.status === "active" || m.status === "stale") && !isExpired(m) && m.recordedAt < cutoff
    && (!m.lastRecalledAt || m.lastRecalledAt < cutoff);
};
const withAging = (m: MemoryItem): MemoryItem => ({ ...m, expired: isExpired(m) });

const HISTORY_WORDS = /\b(was|were|before|previous|previously|changed|history|used to|originally)\b/i;
const STOP = new Set(["the", "a", "an", "is", "are", "what", "when", "who", "how", "do", "does", "we", "our", "of", "to", "for", "in", "on", "and", "or", "it", "this", "that"]);
const terms = (text: string) => text.toLowerCase().split(/[^a-z0-9$#-]+/).filter((t) => t.length > 1 && !STOP.has(t));
const day = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

export const mockApi: MemvineApi = {
  async listProjects() {
    return store().projects.map((p) => {
      const d = projectData(p.slug);
      const monthCredits = d.usage.reduce((s, u) => s + u.ingestCredits + u.recallCredits, 0);
      return { ...p, unusedCount: d.memories.filter((m) => isUnused(m)).length, unusedDays: UNUSED_DAYS, reviewCount: d.memories.filter(awaitingReview).length, memoryCount: d.memories.length, sourceCount: d.episodes.length, sampleSourceCount: d.episodes.filter((e) => e.id.startsWith(SAMPLE_PREFIX)).length, keyCount: d.keys.filter((k) => !k.revokedAt).length, monthCredits };
    });
  },
  async createProject(name, tags, options = {}) {
    const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "project";
    if (store().data[slug]) throw new Error(`409 slug-taken: a project with slug '${slug}' exists`);
    store().projects.push({ slug, name, createdAt: new Date().toISOString(), memoryCount: 0, tags: [...new Set(tags)].sort(), sourceCount: 0, keyCount: 0, lastActivityAt: null, monthlyCreditCap: null, retentionDays: null, monthCredits: 0 });
    store().data[slug] = { keys: [], memories: [], episodes: [], threads: [], sessions: [], audiences: [], identities: [], failedEpisodes: new Set(), requests: [], usage: [] };
    const { secret } = await mockApi.createKey(slug, "default");
    if (options.sample) await mockApi.loadSample(slug);
    const project = (await mockApi.listProjects()).find((p) => p.slug === slug)!;
    return { project, secret };
  },
  async updateProject(slug, patch) {
    const p = store().projects.find((x) => x.slug === slug);
    if (!p) throw new Error("404 not-found");
    if (patch.name) p.name = patch.name;
    if (patch.tags) p.tags = [...new Set(patch.tags)].sort();
    if (patch.monthlyCreditCap !== undefined) p.monthlyCreditCap = patch.monthlyCreditCap;
    if (patch.retentionDays !== undefined) p.retentionDays = patch.retentionDays;
    if (patch.recallMinRelevance !== undefined) p.recallMinRelevance = patch.recallMinRelevance;
  },
  async deleteProject(slug) {
    store().projects = store().projects.filter((p) => p.slug !== slug);
    delete store().data[slug];
  },

  async listKeys(project) {
    return [...projectData(project).keys].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  },
  async createKey(project, name) {
    const secret = `mv_sk_${randomBytes(24).toString("base64url")}`;
    const key: ApiKey = {
      id: `key_${randomBytes(4).toString("hex")}`, name, masked: `mv_sk_••••${secret.slice(-4)}`,
      createdAt: new Date().toISOString(), lastUsedAt: null, revokedAt: null,
    };
    projectData(project).keys.push(key);
    return { key, secret };
  },
  async revokeKey(project, id) {
    const key = projectData(project).keys.find((k) => k.id === id);
    if (key && !key.revokedAt) key.revokedAt = new Date().toISOString();
  },

  async listMemories(project, filter: MemoryFilter = {}) {
    const q = filter.q?.trim().toLowerCase();
    return projectData(project)
      .memories.filter((m) => !filter.kind || m.kind === filter.kind)
      .filter((m) => !filter.status || (filter.status === "expired" ? isExpired(m)
        : m.status === filter.status && !((filter.status === "active" || filter.status === "stale") && isExpired(m))))
      .filter((m) => !filter.unused || isUnused(m))
      .filter((m) => !filter.category || m.category === filter.category)
      .filter((m) => filter.review !== "new" || awaitingReview(m))
      .filter((m) => filter.review !== "kept" || m.review === "kept")
      .filter((m) => !q || m.statement.toLowerCase().includes(q))
      .sort((a, b) => b.recordedAt.localeCompare(a.recordedAt))
      .map(withAging);
  },
  async getMemory(project, id): Promise<MemoryDetail | null> {
    const data = projectData(project);
    const byId = new Map(data.memories.map((m) => [m.id, m]));
    const memory = byId.get(id);
    if (!memory) return null;
    let head = memory;
    while (head.supersedes && byId.has(head.supersedes)) head = byId.get(head.supersedes)!;
    const chain: MemoryItem[] = [head];
    while (chain.at(-1)!.supersededBy && byId.has(chain.at(-1)!.supersededBy!)) chain.push(byId.get(chain.at(-1)!.supersededBy!)!);
    const episodeIds = new Set(memory.evidence.map((e) => e.episodeId));
    return {
      memory: withAging(memory), chain, conflicts: (memory.conflictsWith ?? []).map((cid) => byId.get(cid)).filter((m): m is MemoryItem => Boolean(m)),
      episodes: data.episodes.filter((e) => episodeIds.has(e.id)),
    };
  },
  async listThreads(project, filter = {}) {
    const data = projectData(project);
    const q = filter.q?.trim().toLowerCase();
    return data.threads
      .filter((t) => !filter.status || t.status === filter.status)
      .filter((t) => !filter.kind || t.kind === filter.kind)
      .filter((t) => !q || `${t.title} ${t.key} ${t.summary}`.toLowerCase().includes(q))
      .map((t) => threadSummary(data, t))
      .sort((a, b) => b.lastActivityAt.localeCompare(a.lastActivityAt));
  },
  async getThread(project, id): Promise<ThreadDetail | null> {
    const data = projectData(project);
    const t = data.threads.find((x) => x.id === id);
    if (!t) return null;
    const { relatedIds, sourceIds, ...rest } = t;
    return {
      ...rest,
      ...threadSummary(data, t),
      related: relatedIds
        .map((rid) => data.threads.find((x) => x.id === rid))
        .filter((x): x is MockThread => Boolean(x))
        .map((x) => threadSummary(data, x)),
      knowledge: data.memories
        .filter((m) => m.threadId === t.id && m.status !== "superseded")
        .sort((a, b) => b.recordedAt.localeCompare(a.recordedAt)),
      sources: sourceIds
        .map((eid) => data.episodes.find((e) => e.id === eid))
        .filter((e): e is Episode => Boolean(e))
        .map((e) => ({ episodeId: e.id, label: e.label ?? e.id, occurredAt: e.occurredAt })),
    };
  },
  async listAudiences(project) {
    const data = projectData(project);
    return data.audiences.map((a) => ({ ...a, memoryCount: data.memories.filter((m) => m.audience === a.slug).length }));
  },
  async putAudience(project, slug, body) {
    const data = projectData(project);
    const next = { slug, kind: body.kind, members: body.kind === "fixed" ? [...new Set(body.members ?? [])] : [], conditions: body.kind === "rule" ? body.conditions ?? [] : [], memoryCount: 0 };
    data.audiences = [...data.audiences.filter((a) => a.slug !== slug), next].sort((a, b) => a.slug.localeCompare(b.slug));
  },
  async deleteAudience(project, slug) {
    const data = projectData(project);
    if (data.memories.some((m) => m.audience === slug)) throw new Error("409 audience-in-use: memories still name this audience");
    data.audiences = data.audiences.filter((a) => a.slug !== slug);
  },
  async listIdentities(project) {
    return projectData(project).identities;
  },
  async putIdentity(project, identity, traits) {
    const data = projectData(project);
    data.identities = [...data.identities.filter((i) => i.identity !== identity), { identity, traits, origin: "console", lastSeenAt: null }];
  },
  async deleteSession() {
    return { messages: 0, sources: 0, memories: 0 };
  },
  async listSessions(project) {
    return [...projectData(project).sessions].sort((a, b) => b.lastEventAt.localeCompare(a.lastEventAt));
  },
  async getRelated(project, id) {
    return relatedFromGraph(graphOf(projectData(project)), id);
  },
  async getGraph(project, query = {}) {
    return graphOf(projectData(project), query);
  },
  /** Mock of "Wrong": POST /v1/memories with what is true instead and `supersedes`: the new fact
   *  supersedes the old one (which keeps its valid_to) and inherits its kind, subjects, category,
   *  thread, scope and audience; no judge is asked. */
  async correctMemory(project, id, statement) {
    const data = projectData(project);
    const old = data.memories.find((m) => m.id === id);
    if (!old) throw new Error("404 not-found: No such memory to supersede");
    if (old.status !== "active" && old.status !== "stale") throw new Error(`400 memory-not-current: That memory is ${old.status}`);
    const now = new Date().toISOString();
    const next: MemoryItem = {
      ...old, id: `mem_${randomBytes(6).toString("hex")}`, statement, validFrom: now, validTo: null, recordedAt: now,
      status: "active", supersedes: old.id, supersededBy: null, assertion: "stated", confidence: "high",
      evidence: [{ episodeId: "console", quote: statement, identity: "you@example.com" }], conflictsWith: [], staleReason: null, why: null,
      review: "kept",
    };
    Object.assign(old, { status: "superseded", supersededBy: next.id, validTo: now });
    data.memories.push(next);
    return next;
  },
  async setMemoryStatus(project, id, status) {
    const memory = projectData(project).memories.find((m) => m.id === id);
    if (!memory || memory.status === "superseded") return;
    memory.status = status;
    memory.validTo = status === "retired" ? new Date().toISOString() : null;
  },
  async confirmMemory(project, id) {
    const memory = projectData(project).memories.find((m) => m.id === id);
    if (memory) {
      memory.assertion = "observed";
      memory.confidence = "high";
      memory.review = "kept";
    }
  },

  async listEpisodes(project): Promise<EpisodeSummary[]> {
    const data = projectData(project);
    return [...data.episodes]
      .sort((a, b) => b.occurredAt.localeCompare(a.occurredAt))
      .map((e, i) => {
        const memoryCount = data.memories.filter((m) => m.evidence.some((v) => v.episodeId === e.id)).length;
        const threadCount = data.threads.filter((t) => t.sourceIds.includes(e.id)).length;
        const failed = data.failedEpisodes.has(e.id);
        // A pending source is queued until its synthesis starts, then synthesizing until it is done.
        const [startsAt, finishesAt] = data.pendingEpisodes?.[e.id] ?? [0, 0];
        const elapsed = (Date.now() - store().startedAt) / 1000;
        const status = failed ? "failed" : elapsed >= finishesAt ? "done" : elapsed >= startsAt ? "running" : "queued";
        return {
          id: e.id, label: e.label, identity: e.identity, occurredAt: e.occurredAt, bytes: e.content.length,
          jobId: i + 1, jobStatus: status, jobAttempts: failed ? 3 : status === "queued" ? 0 : 1, jobRetryAt: null,
          jobError: failed ? "LLM request timed out after 120 s (attempt 3 of 3)\nTraceback (most recent call last):\n  ...\nTimeoutError" : null,
          jobResult: status === "done" ? { candidates: memoryCount, dropped_no_quote: 0 } : null,
          memoryCount: status === "done" ? memoryCount : 0, threadCount: status === "done" ? threadCount : 0,
        };
      });
  },
  async getEpisode(project, id): Promise<Episode | null> {
    return projectData(project).episodes.find((e) => e.id === id) ?? null;
  },
  async retryJob(project, jobId) {
    const episodes = await mockApi.listEpisodes(project);
    const target = episodes.find((e) => e.jobId === jobId);
    if (!target) return;
    const data = projectData(project);
    data.failedEpisodes.delete(target.id);
    // Synthesize again over the next 20 seconds, so the Sources screen shows the progress.
    const elapsed = (Date.now() - store().startedAt) / 1000;
    data.pendingEpisodes = { ...data.pendingEpisodes, [target.id]: [elapsed + 8, elapsed + 20] };
  },

  async listRequests(project) {
    return [...projectData(project).requests].sort((a, b) => b.at.localeCompare(a.at));
  },
  async getUsage(project) {
    return projectData(project).usage;
  },
  async orgInfo() {
    return { operator: false, org: { slug: "demo", name: "Demo organization" }, user: { id: "u1", email: "you@example.com", name: "You" },
             role: "owner" as const, orgs: [{ slug: "demo", name: "Demo organization", role: "owner" as const }] };
  },
  async listAudit() {
    const at = (h: number) => new Date(Date.now() - h * 3600_000).toISOString();
    return [
      { at: at(1), actor: "org key (demo)", action: "memory.update", target: "3f2c1a9e-0b7d-4c55-9a51-1e2f3a4b5c6d", project: "checkout-service" },
      { at: at(5), actor: "org key (demo)", action: "key.create", target: "checkout-service/keys/ci", project: "checkout-service" },
      { at: at(30), actor: "org key (demo)", action: "project.create", target: "checkout-service", project: "checkout-service" },
    ];
  },
  async listOrgKeys() {
    return mockOrgKeys;
  },
  async createOrgKey(name) {
    const secret = `mv_mk_${randomBytes(24).toString("base64url")}`;
    const key = { id: `ok_${randomBytes(4).toString("hex")}`, name, masked: `mv_mk_••••${secret.slice(-4)}`, createdAt: new Date().toISOString(), lastUsedAt: null, revokedAt: null };
    mockOrgKeys.unshift(key);
    return { key, secret };
  },
  async revokeOrgKey(id) {
    if (mockOrgKeys.filter((k) => !k.revokedAt && k.id !== id).length === 0) throw new Error("409 last-key: create another key before revoking the last one");
    const k = mockOrgKeys.find((x) => x.id === id);
    if (k && !k.revokedAt) k.revokedAt = new Date().toISOString();
  },
  async listOrgs() {
    return [];
  },
  async createOrg() {
    throw new Error("Organizations are managed by the operator of a live server.");
  },
  async listMembers() {
    return seedMembers;
  },
  async changeRole(userId, role) {
    const m = seedMembers.find((x) => x.id === userId);
    if (m) m.role = role;
  },
  async removeMember(userId) {
    const i = seedMembers.findIndex((x) => x.id === userId);
    if (i >= 0) seedMembers.splice(i, 1);
  },
  async listInvites() {
    return mockInvites;
  },
  async createInvite(email, role) {
    const token = `mv_in_mock${Math.random().toString(36).slice(2, 10)}`;
    mockInvites.unshift({ id: token, email, role, createdBy: "you@example.com", createdAt: new Date().toISOString(),
                          expiresAt: new Date(Date.now() + 7 * 864e5).toISOString(), acceptedAt: null, acceptedBy: null });
    return { token };
  },
  async revokeInvite(id) {
    const i = mockInvites.findIndex((x) => x.id === id);
    if (i >= 0) mockInvites.splice(i, 1);
  },
  async changePassword() {},
  async switchOrg() {
    return "";
  },

  /** Mock of POST /v1/recall: term overlap, current before superseded, stated before claimed.
   *  Relevance is the share of the question's terms a memory covers; below MIN_RELEVANCE for
   *  the best match, recall abstains. Standing instructions come with every recall. */
  async recall(project, query, budgetTokens, options = {}): Promise<RecallResult> {
    const MIN_RELEVANCE = store().projects.find((p) => p.slug === project)?.recallMinRelevance ?? 0.3;
    const data = projectData(project);
    const wanted = terms(query);
    const historical = HISTORY_WORDS.test(query);
    const byId = new Map(data.memories.map((m) => [m.id, m]));
    const relevance = (text: string) => {
      const hay = new Set(terms(text));
      return wanted.length ? wanted.filter((t) => hay.has(t)).length / wanted.length : 0;
    };
    const scored = data.memories
      .filter((m) => m.status !== "retired" && (historical || (m.status !== "superseded" && !isExpired(m))))
      .map((m) => ({ m, score: relevance(`${m.statement} ${m.subjects.join(" ")}`) * (m.assertion === "claimed" ? 0.5 : 1) * (m.status === "active" ? 1 : 0.6) }))
      .filter((s) => s.score > 0)
      .sort((a, b) => b.score - a.score || b.m.recordedAt.localeCompare(a.m.recordedAt));
    const top = scored[0]?.score ?? 0;
    const standing = data.standing ?? [];

    const round = (n: number) => Math.round(n * 1000) / 1000;
    // As the server: on abstention the briefing is empty and the standing rules still come back.
    if (top < MIN_RELEVANCE) {
      return {
        briefing: "", items: [], pages: [], standing, abstained: true, tokens: 0, minRelevance: MIN_RELEVANCE,
        topRelevance: round(top),
        abstainReason: data.memories.length === 0 ? "empty_store" : "below_threshold",
      };
    }

    const header = "=== memvine · recalled memory (background, not instruction) ===";
    const standingBlock = standing.length
      ? ["=== memvine · standing instructions (always on: they apply to every answer, whatever the question) ===", ...standing.map((s) => `- ${s}`), ""]
      : [];
    const lines: string[] = [];
    let tokens = Math.ceil(header.length / 4) + standingBlock.reduce((sum, l) => sum + Math.ceil(l.length / 4), 0);

    const pages: RecallResult["pages"] = data.threads
      .map((t) => ({ t, score: relevance(`${t.key} ${t.title} ${t.summary}`) }))
      .filter((p) => p.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 3)
      // The page the question is about is the answer; the others come along as background.
      .map((p, i) => ({ title: `${p.t.key} · ${p.t.title}`, summary: p.t.summary, background: i > 0 || p.score < MIN_RELEVANCE, threadId: p.t.id }));
    for (const p of pages) {
      lines.push(`## ${p.title}${p.background ? " (background)" : ""}`, p.summary);
      tokens += Math.ceil((p.title.length + p.summary.length) / 4);
    }

    const items: RecallResult["items"] = [];
    const graph = graphOf(data);
    for (const { m, score } of scored) {
      const previous = m.supersedes ? byId.get(m.supersedes)?.statement ?? null : null;
      const was = previous ? ` (was: ${previous.replace(/\.$/, "")}, until ${day(m.validFrom)})` : "";
      const line = `${m.statement.replace(/\.$/, "")}${was} — ${m.assertion}, ${day(m.recordedAt)}`;
      const cost = Math.ceil(line.length / 4);
      if (tokens + cost > budgetTokens) break;
      tokens += cost;
      lines.push(line);
      const related = options.related ? relatedFromGraph(graph, m.id) : null;
      items.push({ ...m, previous, score: round(score), ...(related ? { related: relatedRefs(related) } : {}) });
    }
    return {
      briefing: [...standingBlock, header, ...lines].join("\n"), items, pages, standing, tokens, abstained: false,
      abstainReason: null, minRelevance: MIN_RELEVANCE, topRelevance: round(top),
    };
  },

  /** Mock of POST /v1/sample: the same sample the server writes, free and instant; a no-op when present. */
  async loadSample(project): Promise<SampleResult> {
    const data = projectData(project);
    const sample = buildSample();
    const present = new Set(data.episodes.filter((e) => e.id.startsWith(SAMPLE_PREFIX)).map((e) => e.id));
    if (present.size === sample.episodes.length && sample.episodes.every((e) => present.has(e.id))) {
      return { loaded: false, sources: sample.episodes.length, memories: sample.memories.length };
    }
    if (present.size) await mockApi.deleteSample(project);
    data.episodes.push(...sample.episodes);
    data.memories.push(...sample.memories);
    data.standing = [...(data.standing ?? []), ...sample.standing];
    data.threads.push(...sample.threads);
    return { loaded: true, sources: sample.episodes.length, memories: sample.memories.length };
  },
  /** Mock of POST /v1/ingest with instant: true (mock-instant.ts), taking about as long as a fast model. */
  async addNow(project, content) {
    const data = projectData(project);
    if (content.length > INSTANT_MAX_CHARS) throw new Error(`400 instant-too-large: instant takes one episode of at most ${INSTANT_MAX_CHARS.toLocaleString("en")} characters`);
    await new Promise((r) => setTimeout(r, 1200));
    const { sourceId, memories } = simulateInstant(data, content, (d) => graphOf(d));
    return { sourceId, outcome: "processed", status: "done", reason: null, memories };
  },
  /** Mock of DELETE /v1/sample: the sample sources and the memories drawn only from them. */
  async deleteSample(project): Promise<SampleResult> {
    const data = projectData(project);
    const ids = new Set(data.episodes.filter((e) => e.id.startsWith(SAMPLE_PREFIX)).map((e) => e.id));
    const gone = data.memories.filter((m) => m.evidence.length > 0 && m.evidence.every((v) => ids.has(v.episodeId)));
    const goneStatements = new Set(gone.map((m) => m.statement));
    data.episodes = data.episodes.filter((e) => !ids.has(e.id));
    data.threads = data.threads.filter((t) => !t.sourceIds.some((s) => ids.has(s)));
    data.memories = data.memories.filter((m) => !gone.includes(m));
    data.standing = (data.standing ?? []).filter((s) => !goneStatements.has(s));
    return { sources: ids.size, memories: gone.length };
  },
};
