// In-memory backend seeded from ./mock-data, used when MEMVINE_API_URL is not
// set. State lives for the life of the dev server process.
import "server-only";
import { randomBytes } from "node:crypto";
import { seedData, seedMembers, seedProjects, type MockThread, type ProjectData } from "./mock-data";
import { buildGraph, relatedFromGraph, relatedRefs } from "./graph";
import { buildSample, SAMPLE_PREFIX } from "./sample-data";
import { simulateInstant } from "./mock-instant";
import { BUILTIN_CATEGORIES, INSTANT_MAX_CHARS, MAX_PROJECT_CATEGORIES } from "./types";

const CATEGORY_SLUG = /^[a-z0-9-]{2,24}$/;
import type {
  ApiKey, AskResult, Episode, EpisodeSummary, EventPage, GraphQuery, MemoryEventItem, Profile, SynthesisHealth, SynthesisIssue, MemoryDetail, MemoryFilter, MemoryItem, MemvineApi, Project, RecallResult, SampleResult, ThreadDetail, ThreadSummary,
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
  withConsolidation(data);
  return data;
}

/** A mock of one background consolidation run (consolidation.py), applied once per project: a reworded
 *  restatement of a rule merged into it, and the newest unreviewed fact flagged as no longer passing the bar. */
function withConsolidation(data: ProjectData) {
  if (data.consolidation || data.memories.length === 0) return;
  const at = new Date(Date.now() - 3 * 3_600_000).toISOString();
  const stats = { merged: 0, linked: 0, flagged: 0 };
  const rule = data.memories.find((m) => m.category === "rule" && m.status === "active");
  if (rule) {
    const away: MemoryItem = {
      ...structuredClone(rule), id: `${rule.id}_restated`, statement: `${rule.statement.replace(/\.$/, "")} (said again in other words).`,
      status: "retired", retiredReason: "merged", mergedInto: rule.id, review: "new", flag: null,
    };
    data.memories.push(away);
    recordEvent(data, away.id, "merged", { into: rule.id, judged: "rule" }, "consolidation", at);
    recordEvent(data, rule.id, "merged", { from: away.id, judged: "rule" }, "consolidation", at);
    stats.merged += 1;
  }
  const fact = [...data.memories].sort((a, b) => b.recordedAt.localeCompare(a.recordedAt))
    .find((m) => awaitingReview(m) && m.category === "fact" && !m.flag);
  if (fact) {
    fact.flag = "no_longer_passes";
    fact.flagReason = "Not in the code: it describes how the code works, which the repository already says.";
    recordEvent(data, fact.id, "flagged", { flag: fact.flag }, "consolidation", at);
    stats.flagged += 1;
  }
  data.consolidation = { at, stats };
}

/** Seeded memories carry no review state: the last week's are new (awaiting review), older ones kept. */
function withReview(data: ProjectData) {
  for (const m of data.memories) if (m.review === undefined) m.review = m.recordedAt >= "2026-10-21" ? "new" : "kept";
}

/** The project's events, oldest first: the seed's history derived from its memories (created,
 *  superseded / supersedes, contradicted, forgotten) plus what the console's actions recorded. */
function allEvents(data: ProjectData): MemoryEventItem[] {
  const byId = new Map(data.memories.map((m) => [m.id, m]));
  const ref = (role: string, id: string | null | undefined) => {
    const m = id ? byId.get(id) : undefined;
    return m ? [{ role, id: m.id, statement: m.statement, status: m.status }] : [];
  };
  const ev = (m: MemoryItem, kind: MemoryEventItem["kind"], at: string, extra: Partial<MemoryEventItem> = {}): MemoryEventItem => ({
    id: 0, memoryId: m.id, kind, actor: "synthesis", detail: {}, episodeId: null, at, quotes: [], related: [], ...extra,
  });
  const derived: MemoryEventItem[] = [];
  for (const m of data.memories) {
    if (data.events?.some((e) => e.memoryId === m.id && e.kind === "created")) continue;  // made in this console session
    const first = m.evidence[0];
    derived.push(ev(m, "created", m.recordedAt, {
      episodeId: first?.episodeId ?? null, quotes: m.evidence.filter((e) => e.episodeId === first?.episodeId).map((e) => e.quote),
      detail: { relation: m.supersedes ? "update" : "new" },
    }));
    for (const e of m.evidence.slice(1).filter((e, i, all) => e.episodeId !== first?.episodeId && all.findIndex((x) => x.episodeId === e.episodeId) === i)) {
      derived.push(ev(m, "restated", m.recordedAt, { episodeId: e.episodeId, quotes: [e.quote] }));
    }
    if (m.supersedes) derived.push(ev(m, "supersedes", m.recordedAt, { detail: { of: m.supersedes }, related: ref("of", m.supersedes) }));
    if (m.supersededBy && !data.events?.some((e) => e.memoryId === m.id && e.kind === "corrected")) {
      derived.push(ev(m, "superseded", m.validTo ?? m.recordedAt, { detail: { by: m.supersededBy }, related: ref("by", m.supersededBy) }));
    }
    for (const other of m.conflictsWith ?? []) derived.push(ev(m, "contradicted", m.recordedAt, { detail: { with: other }, related: ref("with", other) }));
    if (m.status === "stale" && m.staleReason) derived.push(ev(m, "stale", m.recordedAt, { actor: "system", detail: { reason: m.staleReason } }));
  }
  const events = [...derived, ...(data.events ?? []).map((e) => ({ ...e, related: e.related.length ? e.related : [...ref("by", e.detail.by as string), ...ref("of", e.detail.of as string)] }))];
  events.sort((a, b) => a.at.localeCompare(b.at));
  return events.map((e, i) => ({ ...e, id: i + 1 }));
}

function recordEvent(data: ProjectData, memoryId: string | null, kind: MemoryEventItem["kind"], detail: Record<string, unknown> = {},
                     actor = "person:you@example.com", at = new Date().toISOString()) {
  (data.events ??= []).push({ id: 0, memoryId, kind, actor, detail, episodeId: null, at, quotes: [], related: [] });
}

const awaitingReview = (m: MemoryItem) => m.review === "new" && (m.status === "active" || m.status === "stale");
const flagged = (m: MemoryItem) => Boolean(m.flag) && (m.status === "active" || m.status === "stale");

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

/** A profile as the server would build it, derived from the mock's memories: their statements stand
 *  in for the model's restatement (each line cites the one memory it came from). */
function mockProfile(slug: string, subjectKind: "person" | "project", subject: string): Profile {
  const data = projectData(slug);
  const project = store().projects.find((p) => p.slug === slug);
  const current = data.memories.filter((m) => (m.status === "active" || m.status === "stale") && !m.expired && m.audience === "project"
    && m.scope !== "thread" && m.kind !== "episodic" && m.kind !== "prospective");
  const durable = (m: MemoryItem) => m.kind === "preference" || ["preference", "rule", "decision"].includes(m.category ?? "");
  let picked: MemoryItem[];
  if (subjectKind === "person") {
    const name = subject.split("@")[0].split(/[._-]/)[0];
    const named = name.length >= 3 ? new RegExp(`\\b${name}\\b`, "i") : null;
    picked = current.filter((m) => (durable(m) && m.evidence.some((e) => e.identity === subject)) || (named && named.test(m.statement))).slice(0, 6);
  } else {
    picked = current.filter((m) => m.category === "rule" || m.category === "decision")
      .sort((a, b) => Number(b.category === "rule") - Number(a.category === "rule")).slice(0, 7);
  }
  const lines = picked.map((m) => ({ text: m.statement + (m.status === "stale" ? " (may be outdated)" : ""), memoryIds: [m.id] }));
  const description = subjectKind === "project" ? project?.description ?? null : null;
  const body = [...(description ? [`- ${description}`] : []), ...lines.map((l) => `- ${l.text}`)];
  const head = subjectKind === "person" ? `=== memvine · about ${subject} ===` : "=== memvine · this project ===";
  const text = body.length ? [head, ...body].join("\n") : "";
  return { subjectKind, subject, lines, description, text, tokens: Math.ceil(text.length / 4), dirty: false, builtAt: "2026-10-27T09:00:00Z" };
}

export const mockApi: MemvineApi = {
  async listProjects() {
    return store().projects.map((p) => {
      const d = projectData(p.slug);
      const monthCredits = d.usage.reduce((s, u) => s + u.ingestCredits + u.recallCredits, 0);
      return { ...p, unusedCount: d.memories.filter((m) => isUnused(m)).length, unusedDays: UNUSED_DAYS, reviewCount: d.memories.filter(awaitingReview).length, flaggedCount: d.memories.filter(flagged).length, consolidatedAt: d.consolidation?.at ?? null, lastConsolidation: d.consolidation?.stats ?? null, memoryCount: d.memories.length, sourceCount: d.episodes.length, sampleSourceCount: d.episodes.filter((e) => e.id.startsWith(SAMPLE_PREFIX)).length, keyCount: d.keys.filter((k) => !k.revokedAt).length, monthCredits };
    });
  },
  async createProject(name, tags, options = {}) {
    const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "project";
    if (store().data[slug]) throw new Error(`409 slug-taken: a project with slug '${slug}' exists`);
    store().projects.push({ slug, name, createdAt: new Date().toISOString(), memoryCount: 0, tags: [...new Set(tags)].sort(), sourceCount: 0, keyCount: 0, lastActivityAt: null, monthlyCreditCap: null, retentionDays: null, monthCredits: 0, teamSpace: Boolean(options.teamSpace), sharePolicy: "ask", personal: Boolean(options.personal) });
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
    if (patch.recallHalfLifeDays !== undefined) p.recallHalfLifeDays = patch.recallHalfLifeDays;
    if (patch.memoryMode !== undefined) p.memoryMode = patch.memoryMode;
    if (patch.sharePolicy !== undefined) p.sharePolicy = patch.sharePolicy;
    if (patch.memoryInstructions !== undefined) {
      const next = patch.memoryInstructions?.trim() || null;
      if (next !== (p.memoryInstructions ?? null)) {
        recordEvent(projectData(slug), null, "project_settings", { field: "memory_instructions", before_len: p.memoryInstructions?.length ?? 0, after_len: next?.length ?? 0 });
      }
      p.memoryInstructions = next;
    }
  },
  async createCategory(project, category) {
    const p = store().projects.find((x) => x.slug === project);
    if (!p) throw new Error("404 not-found");
    const categories = p.categories ?? [];
    if (!CATEGORY_SLUG.test(category.slug)) throw new Error("422 a slug is 2-24 of a-z, 0-9 and -");
    if ((BUILTIN_CATEGORIES as string[]).includes(category.slug)) throw new Error(`400 category-builtin: '${category.slug}' is a built-in category`);
    if (categories.some((c) => c.slug === category.slug)) throw new Error(`409 category-exists: this project already has '${category.slug}'`);
    if (categories.length >= MAX_PROJECT_CATEGORIES) throw new Error(`409 category-limit: at most ${MAX_PROJECT_CATEGORIES} categories`);
    const created = { ...category, name: category.name.trim(), description: category.description.trim(), createdAt: new Date().toISOString() };
    p.categories = [...categories, created];
    recordEvent(projectData(project), null, "project_settings", { field: "category", action: "created", slug: category.slug });
    return created;
  },
  async updateCategory(project, slug, patch) {
    const c = store().projects.find((x) => x.slug === project)?.categories?.find((x) => x.slug === slug);
    if (!c) throw new Error("404 not-found");
    if (patch.name) c.name = patch.name.trim();
    if (patch.description !== undefined) c.description = patch.description.trim();
    recordEvent(projectData(project), null, "project_settings", { field: "category", action: "updated", slug });
  },
  async deleteCategory(project, slug) {
    const p = store().projects.find((x) => x.slug === project);
    if (!p?.categories?.some((c) => c.slug === slug)) throw new Error("404 not-found");
    p.categories = p.categories.filter((c) => c.slug !== slug);
    recordEvent(projectData(project), null, "project_settings", { field: "category", action: "deleted", slug });
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
      createdAt: new Date().toISOString(), lastUsedAt: null, revokedAt: null, createdByEmail: "you@company.com",
    };
    projectData(project).keys.push(key);
    return { key, secret };
  },
  async revokeKey(project, id) {
    const key = projectData(project).keys.find((k) => k.id === id);
    if (key && !key.revokedAt) key.revokedAt = new Date().toISOString();
  },
  async keyConnection(project, id) {
    const key = projectData(project).keys.find((k) => k.id === id);
    if (!key) return null;
    // A demo of the connect page's check: a new key "connects" 15 seconds after it is made, as if
    // the plugin had sent its first session; a seeded key that was used reads as connected.
    const since = key.lastUsedAt ?? (Date.now() - Date.parse(key.createdAt) > 15_000 ? new Date(Date.parse(key.createdAt) + 15_000).toISOString() : null);
    if (!since) return { firstCallAt: null, firstSessionAt: null, firstRecallAt: null, lastUsedAt: null, lastUserAgent: null, calls: 0 };
    return { firstCallAt: since, firstSessionAt: since, firstRecallAt: since, lastUsedAt: key.lastUsedAt ?? since,
             lastUserAgent: key.lastUserAgent ?? "memvine-claude-code/0.7.1", calls: 2 };
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
      .filter((m) => !filter.flag || (flagged(m) && (filter.flag === "any" || m.flag === filter.flag)))
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
    const history = allEvents(data).filter((e) => e.memoryId === id);
    const aged = withAging(memory);
    if (aged.expired && aged.validTo) history.push({ id: null, memoryId: id, kind: "expired", actor: "system", detail: { computed: true },
                                                     episodeId: null, at: aged.validTo, quotes: [], related: [] });
    return {
      memory: aged, chain, conflicts: (memory.conflictsWith ?? []).map((cid) => byId.get(cid)).filter((m): m is MemoryItem => Boolean(m)),
      episodes: data.episodes.filter((e) => episodeIds.has(e.id)), history,
    };
  },
  async listEvents(project, filter = {}): Promise<EventPage> {
    const data = projectData(project);
    const statements = new Map(data.memories.map((m) => [m.id, m]));
    const matching = allEvents(data).reverse()
      .filter((e) => !filter.kind || e.kind === filter.kind)
      .filter((e) => !filter.memoryId || e.memoryId === filter.memoryId)
      .filter((e) => !filter.since || e.at >= filter.since);
    const start = Number(filter.cursor ?? 0) || 0;
    const limit = filter.limit ?? 50;
    const page = matching.slice(start, start + limit).map((e) => {
      const m = e.memoryId ? statements.get(e.memoryId) : undefined;
      return { ...e, statement: m?.statement ?? null, memoryStatus: m?.status ?? null };
    });
    return { events: page, nextCursor: start + limit < matching.length ? String(start + limit) : null };
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
  async listProfiles(project) {
    return [mockProfile(project, "project", ""), ...projectData(project).identities.map((i) => mockProfile(project, "person", i.identity))];
  },
  async getProfile(project, identity) {
    return mockProfile(project, "person", identity);
  },
  async getProjectProfile(project) {
    return mockProfile(project, "project", "");
  },
  async rebuildProfiles(project, target) {
    return target ? 1 : 1 + projectData(project).identities.length;
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
    recordEvent(data, next.id, "created", { relation: "correction", related: old.id });
    data.events!.at(-1)!.quotes = [statement];
    recordEvent(data, next.id, "supersedes", { of: old.id, correction: true });
    recordEvent(data, old.id, "corrected", { by: next.id });
    return next;
  },
  async setMemoryStatus(project, id, status) {
    const data = projectData(project);
    const memory = data.memories.find((m) => m.id === id);
    if (!memory || memory.status === "superseded") return;
    if (status === "active" && memory.retiredReason === "merged") {
      // Un-merge: current again (the server also takes the evidence the merge copied off the kept memory).
      recordEvent(data, id, "unmerged", { from: memory.mergedInto });
      if (memory.mergedInto) recordEvent(data, memory.mergedInto, "unmerged", { related: id });
      Object.assign(memory, { status: "active", retiredReason: null, mergedInto: null });
      return;
    }
    if (status === "retired" && memory.status !== "retired") {
      recordEvent(data, id, "forgotten");
      Object.assign(memory, { retiredReason: "forgotten", flag: null, flagReason: null, flagRelated: null });
    }
    if (status === "active" && memory.status === "retired") recordEvent(data, id, "restored");
    if (status === "active" && memory.status === "stale") recordEvent(data, id, "revalidated", { reason: "marked active" });
    memory.status = status;
    memory.validTo = status === "retired" ? new Date().toISOString() : null;
  },
  async confirmMemory(project, id) {
    const data = projectData(project);
    const memory = data.memories.find((m) => m.id === id);
    if (memory) {
      recordEvent(data, id, "confirmed");
      memory.assertion = "observed";
      memory.confidence = "high";
      memory.review = "kept";
      if (memory.flag) recordEvent(data, id, "unflagged", { flag: memory.flag, reason: "kept" });
      Object.assign(memory, { flag: null, flagReason: null, flagRelated: null });
    }
  },
  async dismissFlag(project, id) {
    const data = projectData(project);
    const memory = data.memories.find((m) => m.id === id);
    if (memory?.flag) {
      recordEvent(data, id, "unflagged", { flag: memory.flag, reason: "dismissed" });
      Object.assign(memory, { flag: null, flagReason: null, flagRelated: null });
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
  async synthesisHealth(project): Promise<SynthesisHealth> {
    // The mock worker is always polling; seeded failures and long waits read as the server reports them.
    const episodes = await mockApi.listEpisodes(project);
    const failed = episodes.filter((e) => e.jobStatus === "failed").length;
    const elapsedMin = (Date.now() - store().startedAt) / 60_000;
    const oldest = episodes.some((e) => e.jobStatus === "queued") ? elapsedMin : null;
    const issues: SynthesisIssue[] = [];
    if (failed) {
      issues.push({ condition: "failed", count: failed,
                    message: `${failed} source${failed === 1 ? "" : "s"} failed to synthesize in the last day — see Sources` });
    }
    if (oldest !== null && oldest > 30) {
      issues.push({ condition: "stuck", minutes: oldest, message: `Synthesis is stuck: the oldest source has waited ${Math.floor(oldest)} min` });
    }
    return { healthy: issues.length === 0, scope: "project", issues, failedCount: failed, oldestQueuedMinutes: oldest,
             workerLastPollAt: new Date().toISOString() };
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
    const settings = store().projects.find((p) => p.slug === project);
    const MIN_RELEVANCE = settings?.recallMinRelevance ?? 0.3;
    // As the server (recall.py: recency_factor): facts and gotchas said long ago rank lower among
    // comparably relevant ones, never below 0.6 of their weight; not for a history question.
    const halfLife = settings?.recallHalfLifeDays ?? 90;
    const recency = (m: (typeof data.memories)[number]) => {
      const decays = m.category ? m.category === "fact" || m.category === "gotcha" : m.kind === "semantic" || m.kind === "episodic";
      if (historical || halfLife <= 0 || !decays) return 1;
      const ageDays = Math.max(0, (Date.now() - Date.parse(m.recordedAt)) / 86_400_000);
      return 0.6 + 0.4 * 0.5 ** (ageDays / halfLife);
    };
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
      .map((m) => {
        const rel = relevance(`${m.statement} ${m.subjects.join(" ")}`) * (m.assertion === "claimed" ? 0.5 : 1) * (m.status === "active" ? 1 : 0.6);
        return { m, rel, score: rel * recency(m) * (m.review === "kept" ? 1.1 : 1) };
      })
      .filter((s) => s.rel > 0)
      .sort((a, b) => b.score - a.score || b.m.recordedAt.localeCompare(a.m.recordedAt));
    // The gate uses relevance, never the recency-weighted score.
    const top = Math.max(0, ...scored.map((s) => s.rel));
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

  /** Mock of POST /v1/ask: the mock recall, then a canned answer in place of the model's: the best
   *  match as it stands, what it replaced when it replaced something, and when it was recorded. */
  async ask(project, query, budgetTokens, options = {}): Promise<AskResult> {
    const recall = await mockApi.recall(project, query, budgetTokens, options);
    if (recall.abstained) return { answer: null, answered: false, reason: "nothing_relevant", recall };
    const top = recall.items[0];
    if (!top) return { answer: recall.pages[0]?.summary ?? null, answered: recall.pages.length > 0, reason: recall.pages.length > 0 ? null : "nothing_relevant", recall };
    const sentence = (text: string) => text.replace(/\.$/, "");
    const answer = [
      `${sentence(top.statement)} (recorded ${day(top.recordedAt)}${top.assertion === "claimed" ? ", an assistant's unverified claim" : ""}).`,
      ...(top.previous ? [`This changed: before, it was "${sentence(top.previous)}".`] : []),
    ].join("\n\n");
    return { answer, answered: true, reason: null, recall };
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
