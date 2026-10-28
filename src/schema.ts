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
  learned_commit: string; // git HEAD SHA when first learned (immutable provenance)
  /**
   * SHA at which this memory was last confirmed true — set to
   * learned_commit at creation, and advanced to HEAD each time an agent
   * revalidates it (via `revise`). Staleness is measured from HERE, not from
   * learned_commit, so a memory re-confirmed after a refactor doesn't
   * immediately re-flag on the same changes.
   */
  validated_commit: string;
  validated_at?: string;
  agent: string; // which tool wrote it, e.g. "claude-code"
  status: MemoryStatus;
  supersedes?: string; // id of the memory this one replaces
  confidence: "high" | "medium" | "low";
  /**
   * Whether this memory has been confirmed against real evidence — a test, code
   * actually read, a PR, or explicit user confirmation. Unverified memories are
   * treated as *candidates*: they stay in the gitignored local store and are
   * down-ranked in recall, so raw or guessed knowledge never reaches the team
   * until an agent validates it. Defaults by location for pre-field files.
   */
  verified: boolean;
  /** Short note on the evidence, e.g. a commit, PR number, test name, or "user confirmed". */
  evidence?: string;
  /** Set when status becomes stale: the commit at which staleness was detected. */
  stale_since?: string;
  /**
   * Content hashes (git blob ids) of scoped files that had UNCOMMITTED changes when
   * the memory was learned or last confirmed. validated_commit alone would call
   * such a memory stale the moment it is written; a file still matching its hash
   * is unchanged since confirmation, committed or not.
   */
  validated_snapshot?: Record<string, string>;
}

export interface Memory {
  /** Derived at read time; never persisted as proof of freshness. */
  freshness?: "changed" | "unknown";
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
  for (const key of ["learned_commit", "validated_commit"] as const) {
    if (typeof meta[key] !== "string" || !/^[a-f0-9]{7,40}$/.test(meta[key]!)) errors.push(`invalid ${key}`);
  }
  for (const key of ["learned_at", "validated_at"] as const) {
    if ((key === "learned_at" || meta[key] !== undefined) &&
        (typeof meta[key] !== "string" || !Number.isFinite(Date.parse(meta[key]!)))) errors.push(`invalid ${key}`);
  }
  for (const key of ["scope", "tags"] as const) {
    if (!Array.isArray(meta[key]) || !meta[key]!.every(value => typeof value === "string")) errors.push(`invalid ${key}`);
  }
  if (!["active", "stale", "superseded", "archived"].includes(meta.status ?? "")) errors.push("invalid status");
  if (!["high", "medium", "low"].includes(meta.confidence ?? "")) errors.push("invalid confidence");
  if (typeof meta.verified !== "boolean") errors.push("invalid verified");
  if (typeof meta.agent !== "string") errors.push("invalid agent");
  if (meta.supersedes !== undefined && !/^mem_[a-z0-9]{4,}$/.test(meta.supersedes)) errors.push("invalid supersedes");
  if (meta.evidence !== undefined && typeof meta.evidence !== "string") errors.push("invalid evidence");
  if (meta.stale_since !== undefined && (typeof meta.stale_since !== "string" || !/^[a-f0-9]{7,40}$/.test(meta.stale_since))) errors.push("invalid stale_since");
  if (meta.validated_snapshot !== undefined && (typeof meta.validated_snapshot !== "object" || meta.validated_snapshot === null || Array.isArray(meta.validated_snapshot) ||
      !Object.values(meta.validated_snapshot).every((h) => typeof h === "string" && /^[a-f0-9]{40,64}$/.test(h)))) errors.push("invalid validated_snapshot");
  return errors;
}
