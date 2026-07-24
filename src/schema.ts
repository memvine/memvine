/**
 * memvine memory entry schema.
 *
 * A memory is a markdown file with YAML frontmatter, stored under
 * `.memvine/memories/` (shared, committed) or `.memvine/local/` (personal,
 * gitignored). The frontmatter carries the lifecycle metadata that makes
 * memvine different from a notes folder: provenance (when, at which commit,
 * by which agent), scope (which paths this memory is about), and status.
 *
 * ## Memory types — modeled on human memory
 *
 * Cognitive science divides long-term memory into distinct systems, and
 * agent memory maps onto them cleanly:
 *
 * - `episodic`    — what HAPPENED: events and experiences from sessions.
 *                   ("Tried Node 22 in March — broke the linter, rolled back.")
 *                   Historical facts: they never go stale.
 * - `semantic`    — what IS TRUE: facts, decisions, conventions about the
 *                   codebase. ("Auth uses magic links, chosen over passwords.")
 *                   Goes stale when the code it describes changes.
 * - `procedural`  — HOW TO do something here: runbooks, workflows.
 *                   ("To deploy: make stage, wait for green, promote.")
 *                   Goes stale when the code it describes changes.
 * - `prospective` — what to do LATER, when a condition arrives.
 *                   ("When billing v2 ships, delete the LAUNCH_FLAG hack.")
 *                   Archived once fulfilled.
 *
 * Domain labels (build, test, auth, deploy, …) are freeform `tags`, not kinds.
 */

export type MemoryKind = "episodic" | "semantic" | "procedural" | "prospective";

export type MemoryStatus = "active" | "stale" | "superseded" | "archived";

export interface MemoryMeta {
  id: string;
  kind: MemoryKind;
  /** Freeform domain labels, e.g. ["test", "auth"]. */
  tags: string[];
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
  "episodic",
  "semantic",
  "procedural",
  "prospective",
];

/** Kinds whose truth depends on the current code — eligible for staleness. */
export const STALEABLE_KINDS: MemoryKind[] = ["semantic", "procedural"];

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
