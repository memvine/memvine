/**
 * The staleness engine — memvine's core differentiator, and it's "just" git.
 *
 * Every memory records the commit it was learned at (`learned_commit`) and
 * the paths it describes (`scope`). A memory is *suspect* when files matching
 * its scope have changed since it was learned: the code moved, the memory
 * may now be a confident lie. We mark it `stale`; the next agent session is
 * expected to revalidate (confirm → active, wrong → supersede or archive).
 */
import { minimatch } from "minimatch";
import { inspectChangesSince, headCommit, ChangeScan } from "./git.js";
import { Memory, STALEABLE_KINDS } from "./schema.js";
import type { Store } from "./store.js";

export interface StaleReport {
  memory: Memory;
  changedFiles: string[];
  unknown?: boolean;
}

export function findStale(store: Store): StaleReport[] {
  return findStaleMemories(store.root, store.list({ status: ["active"] }));
}

/** Shared detector for persisted scans and read-time freshness snapshots. */
function findStaleMemories(root: string, memories: Memory[]): StaleReport[] {
  const reports: StaleReport[] = [];
  // The `git diff` is the expensive part of the scan. Memoize the changed-file
  // list by base commit: every memory confirmed at the same commit shares one
  // git call instead of running its own. At 1,000 memories learned at a handful
  // of commits this turns ~1,000 subprocess diffs into a handful. (Net-tree-diff
  // semantics mean cost still scales with the number of DISTINCT base commits,
  // not with the memory count.)
  const changedByBase = new Map<string, ChangeScan>();
  const changedSince = (base: string): ChangeScan => {
    let changed = changedByBase.get(base);
    if (changed === undefined) {
      changed = inspectChangesSince(base, root);
      changedByBase.set(base, changed);
    }
    return changed;
  };
  for (const memory of memories) {
    if (!["active", "stale"].includes(memory.meta.status)) continue;
    // Per-kind lifecycle, modeled on human memory: episodic memories are
    // historical facts ("we tried X and it failed") — they stay true no
    // matter how the code changes, so they never auto-stale. Prospective
    // memories expire by their condition, not by code drift. Only semantic
    // ("what is true") and procedural ("how to") depend on current code.
    if (!STALEABLE_KINDS.includes(memory.meta.kind)) continue;
    // Repo-wide memories (empty scope) never auto-stale: no way to tell
    // which changes affect them. Scoped memories are checkable.
    if (memory.meta.scope.length === 0) continue;
    // Measure from the last commit at which the memory was CONFIRMED true, not
    // from where it was first learned — otherwise revalidating a memory after a
    // refactor wouldn't clear the flag. validated_commit == learned_commit until
    // the first `revise`.
    const changed = changedSince(memory.meta.validated_commit);
    const hits = changed.files.filter((f) =>
      memory.meta.scope.some((g) => minimatch(f, g, { dot: true })),
    );
    if (hits.length > 0 || changed.unknown) {
      reports.push({ memory, changedFiles: hits, unknown: changed.unknown });
    }
  }
  return reports;
}

/**
 * Derive effective status for this read without changing stored metadata.
 * Explicitly stale entries remain stale; active scoped facts are checked
 * against their validation commit before ranking or digest selection.
 */
export function withFreshness(root: string, memories: Memory[]): Memory[] {
  const stale = new Map(findStaleMemories(root, memories).map(r => [r.memory.meta.id, r]));
  return memories.map(memory => {
    const report = stale.get(memory.meta.id);
    return report
      ? { ...memory, meta: { ...memory.meta, status: "stale" as const }, freshness: report.unknown ? "unknown" as const : "changed" as const }
      : memory;
  });
}

/** Mark the given memories stale (idempotent). Returns count marked. */
export function markStale(store: Store, reports: StaleReport[]): number {
  const now = headCommit(store.root);
  let n = 0;
  for (const { memory } of reports) {
    const found = store.get(memory.meta.id);
    if (!found || found.memory.meta.status !== "active") continue;
    found.memory.meta.status = "stale";
    found.memory.meta.stale_since = now;
    store.write(found.memory, found.local);
    n++;
  }
  return n;
}
