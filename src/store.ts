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

export interface StoreConfig {
  version: 1;
  /** "inline" = memories ride normal commits/PRs; "branch" = dedicated branch (future). */
  commit_mode: "inline" | "branch";
  /** Byte budget for the compiled digest block. Claude Code loads 25KB max. */
  digest_budget_bytes: number;
}

const DEFAULT_CONFIG: StoreConfig = {
  version: 1,
  commit_mode: "inline",
  digest_budget_bytes: 12_000,
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

  add(opts: {
    body: string;
    kind: MemoryKind;
    scope?: string[];
    agent?: string;
    confidence?: MemoryMeta["confidence"];
    supersedes?: string;
    local?: boolean;
  }): Memory {
    const meta: MemoryMeta = {
      id: newId(),
      kind: opts.kind,
      scope: opts.scope ?? [],
      learned_at: new Date().toISOString(),
      learned_commit: headCommit(this.root),
      agent: opts.agent ?? "unknown",
      status: "active",
      confidence: opts.confidence ?? "medium",
      ...(opts.supersedes ? { supersedes: opts.supersedes } : {}),
    };
    const errors = validateMeta(meta);
    if (errors.length) throw new Error(errors.join("; "));
    const memory: Memory = { meta, body: opts.body.trim() };
    this.write(memory, opts.local ?? false);
    if (opts.supersedes) {
      const old = this.get(opts.supersedes);
      if (old) {
        old.memory.meta.status = "superseded";
        this.write(old.memory, old.local);
      }
    }
    return memory;
  }

  write(memory: Memory, local: boolean): void {
    const file = matter.stringify(memory.body + "\n", memory.meta);
    fs.writeFileSync(this.fileFor(memory.meta.id, local), file);
  }

  get(id: string): { memory: Memory; local: boolean } | null {
    for (const local of [false, true]) {
      const p = this.fileFor(id, local);
      if (fs.existsSync(p)) {
        return { memory: this.read(p), local };
      }
    }
    return null;
  }

  private read(filePath: string): Memory {
    const parsed = matter(fs.readFileSync(filePath, "utf8"));
    return {
      meta: parsed.data as MemoryMeta,
      body: parsed.content.trim(),
    };
  }

  list(filter?: {
    status?: MemoryMeta["status"][];
    kind?: MemoryKind;
    /** Return memories whose scope matches this path (or repo-wide ones). */
    forPath?: string;
    includeLocal?: boolean;
  }): Memory[] {
    const dirs = [path.join(this.dir, "memories")];
    if (filter?.includeLocal !== false) dirs.push(path.join(this.dir, "local"));
    const memories: Memory[] = [];
    for (const dir of dirs) {
      if (!fs.existsSync(dir)) continue;
      for (const f of fs.readdirSync(dir).filter((f) => f.endsWith(".md"))) {
        try {
          memories.push(this.read(path.join(dir, f)));
        } catch {
          // Unparseable file: skip rather than crash; `memvine doctor` (future) reports these.
        }
      }
    }
    return memories
      .filter((m) => !filter?.status || filter.status.includes(m.meta.status))
      .filter((m) => !filter?.kind || m.meta.kind === filter.kind)
      .filter(
        (m) =>
          !filter?.forPath ||
          m.meta.scope.length === 0 ||
          m.meta.scope.some((g) => minimatch(filter.forPath!, g)),
      )
      .sort((a, b) => b.meta.learned_at.localeCompare(a.meta.learned_at));
  }

  /** Simple full-text + scope search for recall. */
  search(query: string, forPath?: string): Memory[] {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    return this.list({ status: ["active", "stale"], forPath })
      .map((m) => {
        const haystack = (m.body + " " + m.meta.kind).toLowerCase();
        const score = terms.filter((t) => haystack.includes(t)).length;
        return { m, score };
      })
      .filter((x) => x.score > 0 || terms.length === 0)
      .sort((a, b) => b.score - a.score)
      .map((x) => x.m);
  }
}
