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
  /** Byte budget for the memory bodies a single `recall` returns. */
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
    try {
      return {
        ...DEFAULT_CONFIG,
        ...JSON.parse(
          fs.readFileSync(path.join(this.dir, "config.json"), "utf8"),
        ),
      };
    } catch {
      return DEFAULT_CONFIG;
    }
  }

  private fileFor(id: string, local: boolean): string {
    return path.join(this.dir, local ? "local" : "memories", `${id}.md`);
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
    const memory: Memory = { meta, body: opts.body.trim() };
    // The gate: only a verified memory (and not an explicitly personal one)
    // lands in the committed store; unverified candidates stay in gitignored
    // local/ so raw or guessed knowledge never reaches the team by accident.
    const local = opts.local ?? !verified;
    this.write(memory, local);
    if (opts.supersedes) {
      const old = this.get(opts.supersedes);
      if (old) {
        old.memory.meta.status = "superseded";
        this.write(old.memory, old.local);
      }
    }
    return memory;
  }

  /**
   * Promote a memory to validated, committed team knowledge after an agent has
   * re-checked it against real evidence: sets verified, records evidence and the
   * confirming commit, and moves the file from local/ into the committed store.
   */
  validate(id: string, evidence?: string): { memory: Memory; promoted: boolean } | null {
    const found = this.get(id);
    if (!found) return null;
    found.memory.meta.verified = true;
    found.memory.meta.validated_commit = headCommit(this.root);
    if (evidence) found.memory.meta.evidence = evidence;
    const promoted = found.local;
    if (promoted) fs.rmSync(this.fileFor(id, true)); // remove the local copy
    this.write(found.memory, false); // write into the committed store
    return { memory: found.memory, promoted };
  }

  write(memory: Memory, local: boolean): void {
    const file = matter.stringify(memory.body + "\n", memory.meta);
    fs.writeFileSync(this.fileFor(memory.meta.id, local), file);
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
    const parsed = matter(fs.readFileSync(filePath, "utf8"));
    const meta = parsed.data as MemoryMeta;
    meta.tags ??= []; // tolerate pre-tags memory files
    meta.validated_commit ??= meta.learned_commit; // pre-validated_commit files
    // Pre-verified-field files: a memory already in the committed store was, by
    // definition, shared/trusted; one in local/ is a personal or unvalidated note.
    meta.verified ??= committed;
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
      if (!fs.existsSync(dir)) continue;
      for (const f of fs.readdirSync(dir).filter((f) => f.endsWith(".md"))) {
        try {
          memories.push(this.read(path.join(dir, f), committed));
        } catch {
          // Unparseable file: skip rather than crash; `memvine doctor` (future) reports these.
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
  /** Jaccard above which two memory bodies are treated as near-duplicates. */
  private static readonly DUP_THRESHOLD = 0.85;

  /**
   * Recall search: scope-aware BM25 with a relevance floor and near-duplicate
   * removal. When a path is given, memories SCOPED to that path are the reliable
   * signal — they're always in scope regardless of wording — while repo-wide
   * memories ride along only if they lexically match above the floor. Ranking
   * folds in trust weight so unverified/stale/low-confidence memories need
   * clearly more relevance to outrank a trusted one. Deterministic → explainable.
   */
  search(query: string, forPath?: string): Memory[] {
    // Candidates are already scope-filtered: path-scoped matches + repo-wide.
    const candidates = this.list({ status: ["active", "stale"], forPath });
    const terms = tokenize(query);
    if (terms.length === 0) return Store.byQuality(candidates);

    const bm25 = new BM25(candidates.map(memoryText));
    const scored = candidates.map((m, i) => ({
      m,
      rel: bm25.score(i, terms),
      // A non-empty scope survived the forPath filter only by matching it.
      scoped: !!forPath && m.meta.scope.length > 0,
    }));

    const top = Math.max(0, ...scored.map((s) => s.rel));
    const floor = top * Store.RELEVANCE_FLOOR;
    // Path-scoped memories are relevant by location; repo-wide ones must earn
    // their place lexically (and clear the floor relative to the best match).
    const included = scored.filter(
      (s) => s.scoped || (s.rel > 0 && s.rel >= floor),
    );
    if (included.length === 0) return Store.byQuality(candidates);

    included.sort(
      (a, b) =>
        Number(b.scoped) - Number(a.scoped) || // scoped tier leads
        b.rel * Store.qualityWeight(b.m) - a.rel * Store.qualityWeight(a.m) ||
        b.m.meta.learned_at.localeCompare(a.m.meta.learned_at),
    );

    // Near-duplicate removal: keep the higher-ranked of any two near-identical
    // bodies, so a superseded-but-still-active restatement doesn't double up.
    const out: Memory[] = [];
    for (const { m } of included) {
      if (out.some((k) => jaccard(k.body, m.body) >= Store.DUP_THRESHOLD)) continue;
      out.push(m);
    }
    return out;
  }

  /**
   * Recall for an agent: ranked search, then bounded by BOTH a top-k cap and a
   * byte budget. top-k alone isn't enough — a handful of long memories can still
   * blow a context window — so we stop once either limit is hit, dropping the
   * lowest-ranked first. The highest-ranked memory is always included (even if it
   * alone exceeds the budget) so a relevant hit is never silently swallowed.
   */
  recall(
    query: string,
    forPath?: string,
    opts?: { limit?: number; budgetBytes?: number },
  ): { memories: Memory[]; omitted: number } {
    const cfg = this.config();
    const limit = Math.max(1, opts?.limit ?? cfg.recall_max_memories);
    const budget = Math.max(1, opts?.budgetBytes ?? cfg.recall_budget_bytes);
    const ranked = this.search(query, forPath);
    const chosen: Memory[] = [];
    let bytes = 0;
    for (const m of ranked) {
      if (chosen.length >= limit) break;
      const cost = Buffer.byteLength(m.body, "utf8");
      if (chosen.length > 0 && bytes + cost > budget) break;
      chosen.push(m);
      bytes += cost;
    }
    return { memories: chosen, omitted: ranked.length - chosen.length };
  }
}
