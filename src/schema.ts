/**
 * memvine memory entry schema.
 *
 * A memory is a markdown file with YAML frontmatter, stored under
 * `.memvine/memories/` (shared, committed) or `.memvine/local/` (personal,
 * gitignored). The frontmatter carries the lifecycle metadata that makes
 * memvine different from a notes folder: provenance (when, at which commit,
 * by which agent), scope (which paths this memory is about), and status.
 */

export type MemoryKind =
  | "gotcha"
  | "decision"
  | "convention"
  | "build"
  | "test"
  | "env"
  | "other";

export type MemoryStatus = "active" | "stale" | "superseded" | "archived";

export interface MemoryMeta {
  id: string;
  kind: MemoryKind;
  /** Path globs this memory is about, e.g. ["src/auth/**"]. Empty = repo-wide. */
  scope: string[];
  learned_at: string; // ISO timestamp
  learned_commit: string; // git HEAD short SHA when learned
  agent: string; // which tool wrote it, e.g. "claude-code"
  status: MemoryStatus;
  supersedes?: string; // id of the memory this one replaces
  confidence: "high" | "medium" | "low";
  /** Set when status becomes stale: the commit at which staleness was detected. */
  stale_since?: string;
}

export interface Memory {
  meta: MemoryMeta;
  /** The memory content itself: plain markdown. */
  body: string;
}

export const KINDS: MemoryKind[] = [
  "gotcha",
  "decision",
  "convention",
  "build",
  "test",
  "env",
  "other",
];

export function newId(): string {
  const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
  let id = "";
  for (let i = 0; i < 8; i++) {
    id += alphabet[Math.floor(Math.random() * alphabet.length)];
  }
  return `mem_${id}`;
}

export function validateMeta(meta: Partial<MemoryMeta>): string[] {
  const errors: string[] = [];
  if (!meta.id || !/^mem_[a-z0-9]{4,}$/.test(meta.id)) {
    errors.push(`invalid or missing id: ${meta.id}`);
  }
  if (!meta.kind || !KINDS.includes(meta.kind)) {
    errors.push(`invalid kind: ${meta.kind}`);
  }
  if (!meta.learned_commit) errors.push("missing learned_commit");
  if (!meta.learned_at) errors.push("missing learned_at");
  return errors;
}
