// Shapes the console reads from the Memvine API. The live client (api-live.ts)
// maps the server's snake_case JSON onto these; the mock (api-mock.ts) returns
// them directly.

export type Role = "owner" | "admin" | "member" | "billing";

export interface Project {
  slug: string;
  name: string;
  createdAt: string;
  memoryCount: number;
  tags: string[];
  sourceCount: number;
  keyCount: number;
  lastActivityAt: string | null;
  /** Credits allowed per calendar month (UTC); null is uncapped. Ingestion stops at the cap, recall never does. */
  monthlyCreditCap: number | null;
  /** Days raw sources are kept; null keeps them forever. Memories outlive the window. */
  retentionDays: number | null;
  monthCredits: number;
  /** Recall abstains unless a memory is at least this relevant (0–1); null uses the server default. */
  recallMinRelevance?: number | null;
  /** Recency in recall's ranking: a fact or gotcha's weight halves toward a floor every this many days; 0 is off, null the server default (90). */
  recallHalfLifeDays?: number | null;
  /** Sources that are sample data (loaded from the console), deletable in one action. */
  sampleSourceCount?: number;
  /** What the project is for, in the owner's words; shown under its name on the graph. */
  description?: string | null;
  /** Memories no recall served in the last `unusedDays` days (listed for review, never hidden). */
  unusedCount?: number;
  unusedDays?: number;
  /** Current memories awaiting review (review "new"). */
  reviewCount?: number;
}

/** POST /v1/ingest with `instant: true` (server/memvine_server/instant.py). status "done": every
 *  memory the source is now evidence for, each with its edges (GET /v1/memories/{id}/related's
 *  shape), a memory after those it supersedes or extends. "queued": it could not run now (`reason`). */
export interface InstantResult {
  status: "done" | "queued";
  reason: string | null;
  memories: MemoryRelated[];
}

/** What adding a pasted source did: its id, how the server took it, and what it learned. */
export interface AddResult extends InstantResult {
  sourceId: string;
  outcome: "processed" | "queued" | "replaced" | "unchanged";
}

/** The most an instant ingest takes, in characters (the server's INSTANT_MAX_CHARS). */
export const INSTANT_MAX_CHARS = 4000;

/** What loading or deleting the sample data did. `loaded` is false when the sample was already there. */
export interface SampleResult {
  loaded?: boolean;
  sources: number;
  memories: number;
}

export interface ApiKey {
  id: string;
  name: string;
  /** Prefix and last four characters only; the clear value is shown once, at creation. */
  masked: string;
  createdAt: string;
  lastUsedAt: string | null;
  /** The address the key was last used from. */
  lastUsedIp?: string | null;
  revokedAt: string | null;
  /** The User-Agent of the key's last call: memvine-claude-code/0.7.1, memvine-codex/0.7.1, memvine-sdk-ts/0.1.0... */
  lastUserAgent?: string | null;
  /** The signed-in person who created the key; null for keys made by an organization key. */
  createdByEmail?: string | null;
}

/** Whether an agent has used a key yet (GET /v1/projects/{slug}/keys/{id}/connection). */
export interface KeyConnection {
  firstCallAt: string | null;
  /** First conversation sent (a plugin's or SDK session). */
  firstSessionAt: string | null;
  /** First recall or session brief. */
  firstRecallAt: string | null;
  /** Any call, MCP included. */
  lastUsedAt: string | null;
  lastUserAgent: string | null;
  calls: number;
}

export type MemoryKind = "episodic" | "semantic" | "procedural" | "prospective" | "preference";
export type MemoryStatus = "active" | "superseded" | "retired" | "stale";
export type Assertion = "observed" | "stated" | "claimed" | "inferred";

export interface Evidence {
  episodeId: string;
  quote: string;
  /** Who said it: the source's identity (an email or a name). The graph's "said by". */
  identity?: string | null;
}

export interface MemoryItem {
  id: string;
  kind: MemoryKind;
  statement: string;
  subjects: string[];
  validFrom: string;
  validTo: string | null;
  recordedAt: string;
  status: MemoryStatus;
  supersedes: string | null;
  supersededBy: string | null;
  assertion: Assertion;
  confidence: "high" | "medium" | "low";
  audience: string;
  evidence: Evidence[];
  /** Active memories making incompatible claims about the same thing; shown side by side. */
  conflictsWith?: string[];
  /** Why a memory about code may be outdated, e.g. "src/queue.py changed since 3 Oct 2026". */
  staleReason?: string | null;
  /** What sort of durable knowledge this is; null or absent for plain memory. */
  /** "project" knowledge concerns everyone; "thread" knowledge only matters inside its thread. */
  scope?: "project" | "thread" | null;
  /** Past the end it stated (validTo) while still active or stale: recall no longer serves it as current. */
  expired?: boolean;
  /** When a recall briefing last served it, and how many times one has. */
  lastRecalledAt?: string | null;
  recallCount?: number;
  category?: KnowledgeCategory | null;
  /** The reason behind a decision, rule or gotcha, in one sentence. */
  why?: string | null;
  /** The thread of work this came from, if any. */
  threadId?: string | null;
  /** "new" until a person keeps it (Keep / Still true); synthesis writes "new", a person's own memory starts "kept". */
  review?: "new" | "kept";
}

export type KnowledgeCategory = "decision" | "rule" | "gotcha" | "fact" | "preference";

export type ThreadKind = "release" | "task" | "pr" | "ticket" | "topic";
export type ThreadStatus = "open" | "blocked" | "done" | "abandoned";

/** A piece of work (a release, task, PR, ticket or topic) followed across sessions. */
export interface ThreadSummary {
  id: string;
  kind: ThreadKind;
  /** The work's own identifier, e.g. "4.2.0", "#442" or "INV-243". */
  key: string;
  title: string;
  status: ThreadStatus;
  summary: string;
  firstSeenAt: string;
  lastActivityAt: string;
  sessionCount: number;
  knowledgeCount: number;
}

export interface ThreadSection {
  label: string;
  items: { text: string; date: string | null; evidence: { episodeId: string; quote: string }[] }[];
}

export interface ThreadDetail extends ThreadSummary {
  sections: ThreadSection[];
  related: ThreadSummary[];
  knowledge: MemoryItem[];
  sources: { episodeId: string; label: string; occurredAt: string }[];
}

export interface ThreadFilter {
  status?: ThreadStatus;
  kind?: ThreadKind;
  q?: string;
}

export interface Episode {
  id: string;
  identity: string | null;
  occurredAt: string;
  label: string | null;
  content: string;
}

export type JobStatus = "queued" | "running" | "done" | "failed";

/** One reason synthesis is not keeping up (GET /v1/health/synthesis). */
export interface SynthesisIssue {
  /** failed: jobs failed recently; stuck: a live source waited too long; worker: no worker is polling. */
  condition: "failed" | "stuck" | "worker";
  message: string;
  count?: number;
  minutes?: number | null;
  projects?: { slug: string; name: string; count: number; minutes?: number }[];
}

export interface SynthesisHealth {
  healthy: boolean;
  scope: "project" | "organization" | "server";
  issues: SynthesisIssue[];
  failedCount: number;
  oldestQueuedMinutes: number | null;
  workerLastPollAt: string | null;
}

/** A source as the Sources screen lists it: the episode plus its synthesis outcome. */
export interface EpisodeSummary {
  id: string;
  label: string | null;
  identity: string | null;
  occurredAt: string;
  bytes: number;
  jobId: number | null;
  jobStatus: JobStatus | null;
  jobError: string | null;
  jobResult: { candidates?: number; dropped_no_quote?: number; relations?: Record<string, number> } | null;
  /** Attempts made so far; a queued job with an error is waiting to retry. */
  jobAttempts?: number;
  /** When a queued job that failed an attempt tries again. */
  jobRetryAt?: string | null;
  memoryCount: number;
  /** Thread pages this source fed. */
  threadCount?: number;
}

export interface SessionState {
  goal: string;
  decisions: string[];
  openQuestions: string[];
  inPlay: string[];
  recentFacts: string[];
  progressClaims: string[];
}

/** A live or finished agent session and its working state. */
export interface SessionSummary {
  id: string;
  agent: string | null;
  identity: string | null;
  label: string | null;
  status: "live" | "synthesized";
  startedAt: string;
  lastEventAt: string;
  turns: number;
  stateThrough: number;
  state: SessionState;
}

export interface AudienceCondition {
  trait: string;
  op: "is" | "is_not" | "is_one_of" | "is_not_one_of" | "exists" | "does_not_exist";
  value?: string | string[] | null;
}

/** A scope inside a project. Memory sent to it is recalled only by identities it reaches. */
export interface Audience {
  slug: string;
  kind: "fixed" | "rule";
  members: string[];
  conditions: AudienceCondition[];
  memoryCount: number;
}

export interface Identity {
  identity: string;
  traits: Record<string, string>;
  origin: string;
  lastSeenAt: string | null;
}

export interface OrgInfo {
  operator: boolean;
  org: { slug: string; name: string } | null;
  /** The signed-in person; null for an organization key or the operator. */
  user: { id: string; email: string; name: string } | null;
  role: Role;
  orgs: { slug: string; name: string; role: Role }[];
}

export interface Invite {
  id: string;
  email: string | null;
  role: Role;
  createdBy: string;
  createdAt: string;
  expiresAt: string;
  acceptedAt: string | null;
  acceptedBy: string | null;
}

export interface AuditEvent {
  at: string;
  actor: string;
  action: string;
  target: string;
  project: string | null;
}

export interface OrgKey {
  id: string;
  name: string;
  masked: string;
  createdAt: string;
  lastUsedAt: string | null;
  revokedAt: string | null;
}

export interface OrgSummary {
  slug: string;
  name: string;
  createdAt: string;
  projectCount: number;
}

export interface RequestLog {
  id: number;
  at: string;
  method: string;
  path: string;
  status: number;
  latencyMs: number;
  keyName: string | null;
  /** What the call did where that matters, e.g. why a recall abstained. */
  detail?: string | null;
}

export interface UsageDay {
  date: string;
  ingest: number;
  recall: number;
  ingestCredits: number;
  recallCredits: number;
}

export interface Member {
  id: string;
  name: string;
  email: string;
  role: Role;
  joinedAt: string;
  lastLoginAt: string | null;
}

/** A thread page the briefing includes. */
export interface RecallPage {
  /** The thread's key and title. */
  title: string;
  summary: string;
  /** Served as context around the answer rather than as the answer itself. */
  background: boolean;
  /** The thread's id. */
  threadId: string;
}

export interface RecallResult {
  briefing: string;
  /** `related` is present when recall was asked for it (include.related); at most 6 per item. */
  items: (MemoryItem & { previous?: string | null; score?: number | null; related?: RelatedRef[] | null })[];
  tokens: number;
  abstained: boolean;
  /** Why recall served nothing relevant, when it abstained: "empty_store", "no_candidates" or "below_threshold". */
  abstainReason?: string | null;
  /** The relevance a match needed to be served, and the best match's. */
  minRelevance?: number | null;
  topRelevance?: number | null;
  /** Standing instructions: always on, included in every recall, even one that abstains. */
  standing: string[];
  pages: RecallPage[];
}

export interface MemoryFilter {
  q?: string;
  kind?: MemoryKind;
  /** "expired" is not a stored status: active or stale memories past their stated end. "active" leaves those out. */
  status?: MemoryStatus | "expired";
  category?: KnowledgeCategory;
  /** Only memories no recall served in the project's unusedDays. */
  unused?: boolean;
  /** "new": current memories no person has kept yet. */
  review?: "new" | "kept";
}

export interface MemoryDetail {
  memory: MemoryItem;
  /** Oldest first, ending with the current value. Includes `memory` itself. */
  chain: MemoryItem[];
  conflicts: MemoryItem[];
  episodes: Episode[];
  /** What happened to this memory, oldest first (GET /v1/memories/{id}/history). */
  history: MemoryEventItem[];
}

// ---------------------------------------------------------------- memory history and activity
// Mirrors server/memvine_server/events.py (KINDS) and models.py (MemoryEvent, EventFeed).

export const MEMORY_EVENT_KINDS = [
  "created", "restated", "superseded", "supersedes", "contradicted", "extended", "confirmed", "forgotten", "restored",
  "expired", "corrected", "merged", "anchored", "stale", "revalidated", "category_changed", "refused", "deleted",
] as const;
export type MemoryEventKind = (typeof MEMORY_EVENT_KINDS)[number];

export interface MemoryEventItem {
  /** null only for a computed event (expired). */
  id: number | null;
  memoryId: string | null;
  kind: MemoryEventKind;
  /** "synthesis", "person:<email or identity>", "key:<name>", "system" or "consolidation". */
  actor: string;
  detail: Record<string, unknown>;
  episodeId: string | null;
  at: string;
  /** created / restated: the evidence that source gave. */
  quotes: string[];
  /** The memories `detail` names (by, of, with, into, from, related). */
  related: { role: string; id: string; statement: string; status: MemoryStatus }[];
  /** Feed only: the memory's statement (null once it was deleted) and status. */
  statement?: string | null;
  memoryStatus?: MemoryStatus | null;
}

export interface EventFilter {
  kind?: MemoryEventKind;
  memoryId?: string;
  since?: string;
  cursor?: string;
  limit?: number;
}

export interface EventPage {
  events: MemoryEventItem[];
  /** Pass back as `cursor` for older events; null on the last page. */
  nextCursor: string | null;
}

// ---------------------------------------------------------------- the memory graph
// Edge names, as the API, console and docs spell them (docs/memory-graph-plan.md, "Shared
// decisions"). "said by" and "why" are fields on the node, never edges. These mirror the server's
// models (server/memvine_server/models.py: RelatedRef, RelatedEdge, MemoryRelated, GraphOut),
// camelCased by lib/api-live.ts.

export type EdgeKind = "supersedes" | "contradicts" | "anchored" | "decided_in" | "extends" | "derived";
export const EDGE_KINDS: EdgeKind[] = ["supersedes", "contradicts", "extends", "derived", "decided_in", "anchored"];

/** "out": this memory is the edge's source (it supersedes / extends / is derived from / is anchored
 *  to / was decided in the other end); "in": the other end points at it; "both": contradicts. */
export type EdgeDirection = "out" | "in" | "both";

/** An edge beside a recalled item (POST /v1/recall with include.related), at most 6 per item. */
export interface RelatedRef {
  kind: EdgeKind;
  direction: EdgeDirection;
  /** The other memory, or the thread for decided_in; null for anchored. */
  id: string | null;
  /** The other memory's statement; the thread title for decided_in; "path@version" for anchored,
   *  with " (changed)" or " (deleted)" when the file no longer matches. */
  statement: string;
}

/** The file of an anchored edge. */
export interface RelatedFile {
  repo: string;
  path: string;
  /** The git blob the memory is anchored to. */
  version: string;
  anchoredAt: string;
  /** The blob last reported for the file; null when deleted or never reported. */
  currentVersion: string | null;
  /** Whether the anchored version is still the latest reported one; null when none was reported since. */
  current: boolean | null;
}

/** The thread of a decided_in edge, and of a thread node. */
export interface RelatedThread {
  id: string;
  key: string;
  kind: ThreadKind;
  title: string;
  status: ThreadStatus;
  summary: string;
}

/** One edge of GET /v1/memories/{id}/related: `item` for an edge to another memory, `file` for
 *  anchored, `thread` for decided_in. */
export interface RelatedEdge {
  kind: EdgeKind;
  direction: EdgeDirection;
  item: MemoryItem | null;
  file: RelatedFile | null;
  thread: RelatedThread | null;
}

/** GET /v1/memories/{id}/related. Edges come ordered supersedes, contradicts, extends, derived, decided_in, anchored. */
export interface MemoryRelated {
  memory: MemoryItem;
  edges: RelatedEdge[];
}

export type GraphNodeType = "fact" | "thread" | "file" | "person";

/** A file node: its latest reported version (null when deleted or never reported). */
export interface GraphFile {
  repo: string;
  path: string;
  version: string | null;
  deleted: boolean;
}

/** A node of GET /v1/graph. id: the memory id for a fact, "thread:<uuid>", "file:<repo>:<path>" or
 *  "person:<identity>". Exactly one of memory / thread / file is set for those types; a person
 *  carries only its label. */
export interface GraphNode {
  id: string;
  type: GraphNodeType;
  label: string;
  /** Added or changed since the graph's `since`. */
  grew: boolean;
  memory: MemoryItem | null;
  /** Facts: identities of the sources it came from, drawn as edges to "person:<identity>". */
  saidBy: string[];
  thread: RelatedThread | null;
  file: GraphFile | null;
}

/** An edge of GET /v1/graph. supersedes and extends run newer -> older; derived inferred -> cited;
 *  contradicts once per pair; anchored fact -> file; decided_in fact -> thread. */
export interface GraphEdge {
  kind: EdgeKind;
  from: string;
  to: string;
  /** anchored: the blob the fact is anchored to. */
  version: string | null;
  /** anchored: still the file's latest reported version (false: changed, drawn broken; null: none reported since). */
  current: boolean | null;
}

/** GET /v1/graph?since=&thread=&subject=: a neighborhood, capped at 300 nodes. */
export interface MemoryGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** More of the neighborhood exists than the node cap allowed. */
  truncated: boolean;
  /** Nodes added or changed after this are flagged `grew`. */
  since: string | null;
}

export interface GraphQuery {
  since?: string;
  /** A thread id (with or without the "thread:" prefix). */
  thread?: string;
  subject?: string;
  /** Every thread whatever its status, not only the open ones (still at most 300 nodes). */
  all?: boolean;
}

/** Every backend implements this; lib/api.ts picks one. */
export interface MemvineApi {
  listProjects(): Promise<Project[]>;
  /** Creates the project and its first API key; the clear key is returned once. */
  createProject(name: string, tags: string[], options?: { description?: string | null; sample?: boolean }): Promise<{ project: Project; secret: string }>;
  updateProject(slug: string, patch: { name?: string; tags?: string[]; monthlyCreditCap?: number | null; retentionDays?: number | null; recallMinRelevance?: number | null; recallHalfLifeDays?: number | null }): Promise<void>;
  deleteProject(slug: string): Promise<void>;
  listKeys(project: string): Promise<ApiKey[]>;
  createKey(project: string, name: string): Promise<{ key: ApiKey; secret: string }>;
  revokeKey(project: string, id: string): Promise<void>;
  /** Null for a key that is not in the project (or a server without the route). */
  keyConnection(project: string, id: string): Promise<KeyConnection | null>;
  listMemories(project: string, filter?: MemoryFilter): Promise<MemoryItem[]>;
  listThreads(project: string, filter?: ThreadFilter): Promise<ThreadSummary[]>;
  getThread(project: string, id: string): Promise<ThreadDetail | null>;
  listSessions(project: string): Promise<SessionSummary[]>;
  deleteSession(project: string, id: string): Promise<{ messages: number; sources: number; memories: number }>;
  listAudiences(project: string): Promise<Audience[]>;
  putAudience(project: string, slug: string, body: { kind: "fixed" | "rule"; members?: string[]; conditions?: AudienceCondition[] }): Promise<void>;
  deleteAudience(project: string, slug: string): Promise<void>;
  listIdentities(project: string): Promise<Identity[]>;
  putIdentity(project: string, identity: string, traits: Record<string, string>): Promise<void>;
  getMemory(project: string, id: string): Promise<MemoryDetail | null>;
  /** The project's activity feed, newest first (GET /v1/events). */
  listEvents(project: string, filter?: EventFilter): Promise<EventPage>;
  setMemoryStatus(project: string, id: string, status: "active" | "retired"): Promise<void>;
  /** The node and its edges (GET /v1/memories/{id}/related). */
  getRelated(project: string, id: string): Promise<MemoryRelated | null>;
  /** The project's memory graph (GET /v1/graph). */
  getGraph(project: string, query?: GraphQuery): Promise<MemoryGraph>;
  /** "Wrong": store what is true instead (POST /v1/memories) so it supersedes the old fact. Returns the new fact. */
  correctMemory(project: string, id: string, statement: string): Promise<MemoryItem>;
  confirmMemory(project: string, id: string): Promise<void>;
  listEpisodes(project: string): Promise<EpisodeSummary[]>;
  getEpisode(project: string, id: string): Promise<Episode | null>;
  retryJob(project: string, jobId: number): Promise<void>;
  /** Whether synthesis keeps up in this project; null when the viewer may not see it (members). */
  synthesisHealth(project: string): Promise<SynthesisHealth | null>;
  listRequests(project: string): Promise<RequestLog[]>;
  getUsage(project: string): Promise<UsageDay[]>;
  listMembers(): Promise<Member[]>;
  orgInfo(): Promise<OrgInfo>;
  changeRole(userId: string, role: Role): Promise<void>;
  removeMember(userId: string): Promise<void>;
  listInvites(): Promise<Invite[]>;
  createInvite(email: string | null, role: Role): Promise<{ token: string }>;
  revokeInvite(id: string): Promise<void>;
  changePassword(current: string, next: string): Promise<void>;
  switchOrg(slug: string): Promise<string>;
  listAudit(): Promise<AuditEvent[]>;
  listOrgKeys(): Promise<OrgKey[]>;
  createOrgKey(name: string): Promise<{ key: OrgKey; secret: string }>;
  revokeOrgKey(id: string): Promise<void>;
  listOrgs(): Promise<OrgSummary[]>;
  createOrg(name: string): Promise<{ slug: string; secret: string }>;
  recall(project: string, query: string, budget: number, options?: { related?: boolean }): Promise<RecallResult>;
  /** Fill the project with sample memory (no model call, no credits); a no-op when it is already there. */
  loadSample(project: string): Promise<SampleResult>;
  /** Delete the sample sources and the memories drawn from them. */
  deleteSample(project: string): Promise<SampleResult>;
  /** POST /v1/ingest with instant: one pasted source (at most INSTANT_MAX_CHARS), learned from within the request. */
  addNow(project: string, content: string): Promise<AddResult>;
}
