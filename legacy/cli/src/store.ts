/**
 * The memvine store: plain markdown files in `.memvine/` at the repo root.
 *
 *   .memvine/
 *     config.json          — store configuration
 *     memories/            — shared memories (committed, reviewed like code)
 *       mem_ab12cd34.md
 *     local/               — personal memories (gitignored)
 *
 * No database. No index. Git is the sync, history, and blame.
 */
import * as fs from "node:fs";
import { randomUUID } from "node:crypto";
import * as path from "node:path";
import matter from "gray-matter";
import { minimatch } from "minimatch";
import {
  Memory,
  MemoryKind,
  MemoryMeta,
  newId,
  validateMeta,
} from "./schema.js";
import { headCommit, isGitRepo, repoRoot } from "./git.js";
import { setSnapshot, snapshotScope, withFreshness } from "./staleness.js";
import { clipBytes, renderRecall } from "./render.js";

export const DIR_NAME = ".memvine";

// --- lexical retrieval (BM25) ---------------------------------------------
// Recall ranking is deterministic and dependency-free: BM25 over the memory
// bodies, so a memory needs real term overlap (weighted by term rarity), not a
// single shared word, to score. Kept explainable — no embeddings, no model.

const STOPWORDS = new Set([
  "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "for",
  "with", "is", "are", "be", "was", "were", "this", "that", "it", "its", "as",
  "at", "by", "from", "into", "how", "do", "does", "we", "you", "i", "add",
  "use", "using", "new", "get", "set",
]);

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((t) => t.length > 1 && !STOPWORDS.has(t));
}

function memoryText(m: Memory): string {
  return `${m.body} ${m.meta.kind} ${m.meta.tags.join(" ")} ${m.meta.scope.join(" ")}`;
}

/** Okapi BM25 over a fixed candidate set. */
class BM25 {
  private readonly df = new Map<string, number>();
  private readonly docTokens: string[][];
  private readonly avgdl: number;
  private readonly N: number;
  constructor(docs: string[], private readonly k1 = 1.5, private readonly b = 0.75) {
    this.docTokens = docs.map(tokenize);
    this.N = docs.length;
    this.avgdl =
      this.docTokens.reduce((s, d) => s + d.length, 0) / (this.N || 1) || 1;
    for (const toks of this.docTokens) {
      for (const t of new Set(toks)) this.df.set(t, (this.df.get(t) ?? 0) + 1);
    }
  }
  private idf(t: string): number {
    const df = this.df.get(t) ?? 0;
    return Math.log(1 + (this.N - df + 0.5) / (df + 0.5));
  }
  score(docIndex: number, queryTerms: string[]): number {
    const toks = this.docTokens[docIndex];
    if (toks.length === 0) return 0;
    const tf = new Map<string, number>();
    for (const t of toks) tf.set(t, (tf.get(t) ?? 0) + 1);
    let s = 0;
    for (const q of queryTerms) {
      const f = tf.get(q);
      if (!f) continue;
      const denom = f + this.k1 * (1 - this.b + (this.b * toks.length) / this.avgdl);
      s += (this.idf(q) * (f * (this.k1 + 1))) / denom;
    }
    return s;
  }
}

/**
 * Why a memory was returned by recall — surfaced to the caller so retrieval is
 * explainable. `exact-path` = scoped to the file you're on; `repo-wide` = a
 * project-wide fact that lexically matched; `cross-scope` = admitted by the
 * capped escape hatch (scoped to a different file but a strong lexical match);
 * `lexical` = query-only mode, no path given.
 */
export type MemorySource = "exact-path" | "repo-wide" | "cross-scope" | "lexical";

/** Recall tier order: exact-path leads, cross-scope trails. */
const TIER: Record<MemorySource, number> = {
  "exact-path": 0,
  "repo-wide": 1,
  lexical: 1,
  "cross-scope": 2,
};

interface Scored {
  m: Memory;
  rel: number;
  source: MemorySource;
}

/** Jaccard similarity over token sets — for near-duplicate removal. */
function jaccard(a: string, b: string): number {
  const A = new Set(tokenize(a));
  const B = new Set(tokenize(b));
  if (A.size === 0 || B.size === 0) return 0;
  let inter = 0;
  for (const t of A) if (B.has(t)) inter++;
  return inter / (A.size + B.size - inter);
}

export interface StoreConfig {
  version: 1;
  /** "inline" = memories ride normal commits/PRs; "branch" = dedicated branch (future). */
  commit_mode: "inline" | "branch";
  /** Byte budget for the compiled digest block. Claude Code loads 25KB max. */
  digest_budget_bytes: number;
  /** Max memories a single `recall` returns, before the token budget applies. */
  recall_max_memories: number;
  /** Byte budget for the rendered text a single `recall` returns. */
  recall_budget_bytes: number;
}

const DEFAULT_CONFIG: StoreConfig = {
  version: 1,
  commit_mode: "inline",
  digest_budget_bytes: 12_000,
  recall_max_memories: 10,
  recall_budget_bytes: 6_000,
};

export class Store {
  readonly root: string; // repo root
  readonly dir: string; // .memvine dir

  constructor(root: string) {
    this.root = root;
    this.dir = path.join(root, DIR_NAME);
    this.assertDirectory(this.dir);
  }

  private assertDirectory(directory: string): void {
    try {
      const stat = fs.lstatSync(directory);
      if (stat.isSymbolicLink() || !stat.isDirectory()) throw new Error(`Not a safe memory directory: ${directory}`);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }

  diagnostics(): string[] {
    const issues: string[] = [];
    try { this.config(); } catch (error) { issues.push(String(error)); }
    for (const name of ["memories", "local"]) {
      const dir = path.join(this.dir, name);
      try {
        this.assertDirectory(dir);
        if (!fs.existsSync(dir)) continue;
        for (const file of fs.readdirSync(dir).filter(f => f.endsWith(".md"))) {
          try { this.read(path.join(dir, file), name === "memories"); }
          catch (error) { issues.push(`${name}/${file}: ${String(error)}`); }
        }
      } catch (error) { issues.push(String(error)); }
    }
    return issues;
  }

  /** Locate an existing store at or above cwd. */
  static find(cwd: string): Store | null {
    if (!isGitRepo(cwd)) return null;
    const root = repoRoot(cwd);
    const dir = path.join(root, DIR_NAME);
    return fs.existsSync(dir) ? new Store(root) : null;
  }

  static init(cwd: string): Store {
    if (!isGitRepo(cwd)) {
      throw new Error(
        "memvine needs a git repository — run `git init` first. Git is memvine's sync and provenance engine.",
      );
    }
    const store = new Store(repoRoot(cwd));
    store.assertDirectory(path.join(store.dir, "memories"));
    store.assertDirectory(path.join(store.dir, "local"));
    fs.mkdirSync(path.join(store.dir, "memories"), { recursive: true });
    fs.mkdirSync(path.join(store.dir, "local"), { recursive: true });
    const cfgPath = path.join(store.dir, "config.json");
    if (!fs.existsSync(cfgPath)) {
      fs.writeFileSync(cfgPath, JSON.stringify(DEFAULT_CONFIG, null, 2) + "\n");
    }
    const gi = path.join(store.dir, ".gitignore");
    if (!fs.existsSync(gi)) {
      fs.writeFileSync(gi, "local/\n");
    }
    return store;
  }

  config(): StoreConfig {
    const file = path.join(this.dir, "config.json");
    if (!fs.existsSync(file)) return { ...DEFAULT_CONFIG };
    if (fs.lstatSync(file).isSymbolicLink()) throw new Error("Config must not be a symlink");
    const value = JSON.parse(fs.readFileSync(file, "utf8"));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid memvine config");
    const config = { ...DEFAULT_CONFIG, ...value };
    if (config.version !== 1 || !["inline", "branch"].includes(config.commit_mode)) throw new Error("Unsupported memvine config");
    for (const field of ["digest_budget_bytes", "recall_max_memories", "recall_budget_bytes"] as const) {
      if (!Number.isSafeInteger(config[field]) || config[field] < 1) throw new Error(`Invalid config ${field}`);
    }
    return config;
  }

  private fileFor(id: string, local: boolean): string {
    if (!/^mem_[a-z0-9]{4,}$/.test(id)) throw new Error("Invalid memory id");
    this.assertDirectory(this.dir);
    const directory = path.join(this.dir, local ? "local" : "memories");
    this.assertDirectory(directory);
    if (fs.existsSync(directory) && fs.lstatSync(directory).isSymbolicLink()) throw new Error("Memory directory must not be a symlink");
    return path.join(directory, `${id}.md`);
  }

  /**
   * Normalize an incoming path to repo-relative POSIX form for scope matching.
   * Agents commonly pass an absolute path (e.g. the file they're editing);
   * scopes are stored repo-relative with forward slashes ("src/auth/**"), so
   * without this an absolute path matches nothing and recall comes back empty.
   */
  relPath(p: string): string {
    const rel = path.isAbsolute(p) ? path.relative(this.root, p) : p;
    return rel.split(path.sep).join("/");
  }

  add(opts: {
    body: string;
    kind: MemoryKind;
    tags?: string[];
    scope?: string[];
    agent?: string;
    confidence?: MemoryMeta["confidence"];
    supersedes?: string;
    local?: boolean;
    verified?: boolean;
    evidence?: string;
  }): Memory {
    const commit = headCommit(this.root);
    const verified = opts.verified ?? false;
    const meta: MemoryMeta = {
      id: newId(),
      kind: opts.kind,
      tags: opts.tags ?? [],
      scope: opts.scope ?? [],
      learned_at: new Date().toISOString(),
      learned_commit: commit,
      validated_commit: commit, // last confirmed here == where it was learned
      agent: opts.agent ?? "unknown",
      status: "active",
      confidence: opts.confidence ?? "medium",
      verified,
      ...(opts.evidence ? { evidence: opts.evidence } : {}),
      ...(opts.supersedes ? { supersedes: opts.supersedes } : {}),
    };
    const errors = validateMeta(meta);
    if (errors.length) throw new Error(errors.join("; "));
    const snapshot = snapshotScope(this.root, meta.scope);
    if (snapshot) meta.validated_snapshot = snapshot;
    const memory: Memory = { meta, body: opts.body.trim() };
    // The gate: only a verified memory (and not an explicitly personal one)
    // lands in the committed store; unverified candidates stay in gitignored
    // local/ so raw or guessed knowledge never reaches the team by accident.
    const local = !verified || opts.local === true;
    this.write(memory, local);
    if (!local) this.retirePredecessor(memory);
    return memory;
  }

  /**
   * Promote a memory to validated, committed team knowledge after an agent has
   * re-checked it against real evidence: sets verified, records evidence and the
   * confirming commit, and moves the file from local/ into the committed store.
   */
  private retirePredecessor(memory: Memory): void {
    const predecessor = memory.meta.supersedes;
    if (!memory.meta.verified || !predecessor || predecessor === memory.meta.id) return;
    const old = this.get(predecessor);
    if (old && old.memory.meta.status !== "superseded") {
      old.memory.meta.status = "superseded";
      this.write(old.memory, old.local);
    }
  }

  /** Take a memory out of recall and digests without deleting it (history stays in git). */
  retire(id: string, status: "archived" | "superseded"): boolean {
    const found = this.get(id);
    if (!found) return false;
    if (found.memory.meta.status !== status) {
      found.memory.meta.status = status;
      this.write(found.memory, found.local);
    }
    return true;
  }

  validate(id: string, evidence?: string): { memory: Memory; promoted: boolean } | null {
    const found = this.get(id);
    if (!found) return null;
    found.memory.meta.verified = true;
    found.memory.meta.validated_commit = headCommit(this.root);
    setSnapshot(this.root, found.memory);
    found.memory.meta.validated_at = new Date().toISOString();
    found.memory.meta.status = "active";
    delete found.memory.meta.stale_since;
    if (evidence) found.memory.meta.evidence = evidence;
    const promoted = found.local;
    // Publish first. If publication or retirement fails, preserve the local
    // candidate so retrying validation can complete the operation safely.
    this.write(found.memory, false);
    this.retirePredecessor(found.memory);
    const localFile = this.fileFor(id, true);
    if (fs.existsSync(localFile)) fs.rmSync(localFile);
    return { memory: found.memory, promoted };
  }

  write(memory: Memory, local: boolean): void {
    const errors = validateMeta(memory.meta);
    if (errors.length) throw new Error(errors.join("; "));
    if (!local && !memory.meta.verified) throw new Error("Unverified memories must remain local");
    const file = this.fileFor(memory.meta.id, local);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    if (fs.existsSync(file) && fs.lstatSync(file).isSymbolicLink()) throw new Error("Memory file must not be a symlink");
    const temporary = `${file}.${randomUUID()}.tmp`;
    try {
      fs.writeFileSync(temporary, matter.stringify(memory.body + "\n", memory.meta), { flag: "wx" });
      fs.renameSync(temporary, file);
    } finally {
      if (fs.existsSync(temporary)) fs.rmSync(temporary);
    }
  }

  get(id: string): { memory: Memory; local: boolean } | null {
    for (const local of [false, true]) {
      const p = this.fileFor(id, local);
      if (fs.existsSync(p)) {
        return { memory: this.read(p, !local), local };
      }
    }
    return null;
  }

  private read(filePath: string, committed: boolean): Memory {
    if (fs.lstatSync(filePath).isSymbolicLink()) throw new Error("Memory file must not be a symlink");
    const parsed = matter(fs.readFileSync(filePath, "utf8"));
    // gray-matter caches parsed objects; never mutate its shared metadata.
    const meta = structuredClone(parsed.data) as MemoryMeta;
    for (const key of ["learned_at", "validated_at"] as const) {
      const value: unknown = meta[key];
      if (value instanceof Date && Number.isFinite(value.getTime())) meta[key] = value.toISOString();
    }
    meta.tags ??= []; // tolerate pre-tags memory files
    meta.validated_commit ??= meta.learned_commit; // pre-validated_commit files
    // Pre-verified-field files: a memory already in the committed store was, by
    // definition, shared/trusted; one in local/ is a personal or unvalidated note.
    meta.verified ??= committed;
    const errors = validateMeta(meta);
    if (errors.length) throw new Error(`${filePath}: ${errors.join("; ")}`);
    if (path.basename(filePath) !== `${meta.id}.md`) throw new Error("Memory filename and id differ");
    return { meta, body: parsed.content.trim() };
  }

  list(filter?: {
    status?: MemoryMeta["status"][];
    kind?: MemoryKind;
    /** Return memories whose scope matches this path (or repo-wide ones). */
    forPath?: string;
    includeLocal?: boolean;
  }): Memory[] {
    const dirs = [{ path: path.join(this.dir, "memories"), committed: true }];
    if (filter?.includeLocal !== false) {
      dirs.push({ path: path.join(this.dir, "local"), committed: false });
    }
    const memories: Memory[] = [];
    for (const { path: dir, committed } of dirs) {
      this.assertDirectory(dir);
      if (!fs.existsSync(dir)) continue;
      for (const f of fs.readdirSync(dir).filter((f) => f.endsWith(".md"))) {
        try {
          memories.push(this.read(path.join(dir, f), committed));
        } catch {
          // Unparseable file: skip rather than crash; `memvine doctor` reports these.
        }
      }
    }
    const forPath = filter?.forPath ? this.relPath(filter.forPath) : undefined;
    return memories
      .filter((m) => !filter?.status || filter.status.includes(m.meta.status))
      .filter((m) => !filter?.kind || m.meta.kind === filter.kind)
      .filter(
        (m) =>
          !forPath ||
          m.meta.scope.length === 0 ||
          m.meta.scope.some((g) => minimatch(forPath, g)),
      )
      .sort((a, b) => b.meta.learned_at.localeCompare(a.meta.learned_at));
  }

  /**
   * A memory's trust weight, folded into recall ranking so that unverified or
   * outdated knowledge doesn't surface as authoritative. Higher confidence
   * ranks above lower; a `stale` memory (its code changed since last confirmed)
   * is down-ranked but NOT excluded — recall is where the agent is told to
   * revalidate it. Deterministic and cheap, so recall can explain its order.
   */
  private static qualityWeight(m: Memory): number {
    const byConfidence: Record<string, number> = { high: 1, medium: 0.85, low: 0.6 };
    const confidence = byConfidence[m.meta.confidence] ?? 0.85;
    const freshness = m.meta.status === "stale" ? 0.7 : 1;
    // Unverified candidates (still in local/, not yet confirmed) rank below
    // validated knowledge, but aren't excluded — you can still recall your own.
    const trust = m.meta.verified === false ? 0.75 : 1;
    return confidence * freshness * trust;
  }

  /** Order by trust weight, then recency — used when no lexical signal separates memories. */
  private static byQuality(memories: Memory[]): Memory[] {
    return [...memories].sort(
      (a, b) =>
        Store.qualityWeight(b) - Store.qualityWeight(a) ||
        b.meta.learned_at.localeCompare(a.meta.learned_at),
    );
  }

  /** Keep >= this fraction of the top BM25 score — drops weak single-term matches. */
  private static readonly RELEVANCE_FLOOR = 0.35;
  /** A cross-scope memory must match at least this many DISTINCT query terms to be admitted. */
  private static readonly CROSS_SCOPE_MIN_TERMS = 2;
  /** No on-path memory at all → allow up to this many cross-scope candidates (above the normal floor). */
  private static readonly MAX_CROSS_NO_PATH = 3;
  /** On-path memory exists but is weak → allow this many cross-scope candidates... */
  private static readonly MAX_CROSS_WEAK_PATH = 1;
  /** ...and require each to clear this fraction of the top BM25 score. */
  private static readonly CROSS_STRONG_FLOOR = 0.65;
  /** Jaccard above which two memory bodies are treated as near-duplicates. */
  private static readonly DUP_THRESHOLD = 0.85;

  private classify(m: Memory, relPath?: string): MemorySource {
    if (!relPath) return "lexical";
    if (m.meta.scope.length === 0) return "repo-wide";
    return m.meta.scope.some((g) => minimatch(relPath, g)) ? "exact-path" : "cross-scope";
  }

  /** How many DISTINCT query terms a memory contains — the cross-scope precision gate. */
  private static distinctMatches(m: Memory, termSet: Set<string>): number {
    const toks = new Set(tokenize(memoryText(m)));
    let n = 0;
    for (const t of termSet) if (toks.has(t)) n++;
    return n;
  }

  private byQualityScored(memories: Memory[], relPath?: string): Scored[] {
    return Store.byQuality(memories).map((m) => ({
      m,
      rel: 0,
      source: this.classify(m, relPath),
    }));
  }

  /**
   * Rank memories for recall: scope-aware BM25 with a relevance floor, a
   * CONDITIONAL cross-scope escape hatch, and near-duplicate removal. When a
   * path is given, memories SCOPED to it are the reliable signal (always in
   * scope, ranked first) and repo-wide memories ride along above the floor.
   *
   * Cross-scope memories (scoped to a DIFFERENT file) come in through a gated
   * hatch, keyed on how well the ON-PATH memory answers the query, measured by
   * DISTINCT query-term matches. Every cross-scope candidate must itself match
   * >=2 distinct terms, and then:
   *   - no on-path memory at all  → up to 3 candidates above the relevance floor;
   *   - on-path memory matches <2 terms → 1 candidate, needing >=65% of top BM25;
   *   - on-path memory matches >=2 terms → hatch closed (it answers the query).
   * An earlier always-on version regressed path-assisted precision on the public
   * SWE-Bench-CL set; this gating keeps clean cases clean while recovering a
   * lesson that genuinely lives in another file. Every result carries its source
   * so the caller can show WHY it was returned. Deterministic → explainable.
   */
  private rank(query: string, forPath?: string): Scored[] {
    const all = withFreshness(this.root, this.list({ status: ["active", "stale"] }));
    const relPath = forPath ? this.relPath(forPath) : undefined;
    const terms = tokenize(query);
    if (terms.length === 0) {
      // No query terms: fall back to the scope-relevant set, trust-ranked.
      const base = relPath
        ? all.filter((m) => this.classify(m, relPath) !== "cross-scope")
        : all;
      return this.byQualityScored(base, relPath);
    }

    const bm25 = new BM25(all.map(memoryText));
    const scored: Scored[] = all.map((m, i) => ({
      m,
      rel: bm25.score(i, terms),
      source: this.classify(m, relPath),
    }));
    const top = Math.max(0, ...scored.map((s) => s.rel));
    const floor = top * Store.RELEVANCE_FLOOR;

    let included: Scored[];
    if (!relPath) {
      // Query-only: pure lexical, floor-gated.
      included = scored.filter((s) => s.rel > 0 && s.rel >= floor);
    } else {
      const exact = scored.filter((s) => s.source === "exact-path");
      const repoWide = scored.filter(
        (s) => s.source === "repo-wide" && s.rel > 0 && s.rel >= floor,
      );
      // Cross-scope escape hatch, gated on how well the ON-PATH memory answers the
      // query — measured by DISTINCT query-term matches, not relative BM25 score.
      // Every cross-scope candidate must itself match >=2 distinct terms.
      const termSet = new Set(terms);
      const candidates = scored
        .filter(
          (s) =>
            s.source === "cross-scope" &&
            s.rel > 0 &&
            Store.distinctMatches(s.m, termSet) >= Store.CROSS_SCOPE_MIN_TERMS,
        )
        .sort((a, b) => b.rel * Store.qualityWeight(b.m) - a.rel * Store.qualityWeight(a.m));
      const bestExactTerms = Math.max(
        0,
        ...exact.map((e) => Store.distinctMatches(e.m, termSet)),
      );
      let cross: Scored[];
      if (exact.length === 0) {
        // No on-path memory at all: let up to three cross-file lessons in, above
        // the normal relevance floor — the needed one may be the third.
        cross = candidates
          .filter((s) => s.rel >= floor)
          .slice(0, Store.MAX_CROSS_NO_PATH);
      } else if (bestExactTerms < Store.CROSS_SCOPE_MIN_TERMS) {
        // On-path memory exists but is weak (matches <2 terms): admit a single,
        // strongly-matching cross-file lesson only.
        cross = candidates
          .filter((s) => s.rel >= top * Store.CROSS_STRONG_FLOOR)
          .slice(0, Store.MAX_CROSS_WEAK_PATH);
      } else {
        // On-path memory matches >=2 terms — it answers the query. Hatch closed.
        cross = [];
      }
      included = [...exact, ...repoWide, ...cross];
    }
    if (included.length === 0) {
      if (!relPath) return []; // A query without any matches should abstain.
      const base = relPath
        ? all.filter((m) => this.classify(m, relPath) !== "cross-scope")
        : all;
      return this.byQualityScored(base, relPath);
    }

    included.sort(
      (a, b) =>
        TIER[a.source] - TIER[b.source] || // exact-path < repo-wide < cross-scope
        b.rel * Store.qualityWeight(b.m) - a.rel * Store.qualityWeight(a.m) ||
        b.m.meta.learned_at.localeCompare(a.m.meta.learned_at),
    );

    // Near-duplicate removal: keep the higher-ranked of any two near-identical
    // bodies, so a superseded-but-still-active restatement doesn't double up.
    const out: Scored[] = [];
    for (const s of included) {
      if (out.some((k) => jaccard(k.m.body, s.m.body) >= Store.DUP_THRESHOLD)) continue;
      out.push(s);
    }
    return out;
  }

  /** Ranked memories for recall (see rank). Kept for callers that don't need sources. */
  search(query: string, forPath?: string): Memory[] {
    return this.rank(query, forPath).map((s) => s.m);
  }

  /**
   * Recall for an agent: ranked search, then bounded by BOTH a top-k cap and a
   * byte budget. top-k alone isn't enough — a handful of long memories can still
   * blow a context window — so we stop once either limit is hit, dropping the
   * lowest-ranked first. Oversized memories are omitted rather than bypassing the budget. The
   * formatted response includes an omission count; read_memory supports paging.
   * `sources` maps each returned id to why it matched (exact-path/cross-scope/…).
   */
  recall(
    query: string,
    forPath?: string,
    opts?: { limit?: number; budgetBytes?: number; exclude?: Iterable<string> },
  ): { memories: Memory[]; omitted: number; sources: Record<string, MemorySource>; text: string } {
    const cfg = this.config();
    const limit = Math.max(1, opts?.limit ?? cfg.recall_max_memories);
    const budget = Math.max(1, opts?.budgetBytes ?? cfg.recall_budget_bytes);
    if (!Number.isSafeInteger(limit) || !Number.isSafeInteger(budget)) throw new Error("Recall limits must be finite integers");
    // `exclude`: ids the caller has already shown this session, so a repeat
    // recall surfaces what the agent has NOT seen instead of the same top-k.
    const skip = new Set(opts?.exclude ?? []);
    const ranked = this.rank(query, forPath).filter((s) => !skip.has(s.m.meta.id));
    const chosen: Memory[] = [];
    const sources: Record<string, MemorySource> = {};
    for (const { m, source } of ranked) {
      if (chosen.length >= limit) break;
      const candidateSources = { ...sources, [m.meta.id]: source };
      const candidate = [...chosen, m];
      if (Buffer.byteLength(renderRecall(candidate, candidateSources, ranked.length - candidate.length)) > budget) continue;
      chosen.push(m);
      sources[m.meta.id] = source;
    }
    const omitted = ranked.length - chosen.length;
    const text = renderRecall(chosen, sources, omitted);
    return { memories: chosen, omitted, sources, text: clipBytes(text, budget) };

  }
}
