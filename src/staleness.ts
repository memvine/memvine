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
import { changedFilesSince, headCommit } from "./git.js";
import { Memory, STALEABLE_KINDS } from "./schema.js";
import { Store } from "./store.js";

export interface StaleReport {
  memory: Memory;
  changedFiles: string[];
}

export function findStale(store: Store): StaleReport[] {
  const reports: StaleReport[] = [];
  for (const memory of store.list({ status: ["active"] })) {
    // Per-kind lifecycle, modeled on human memory: episodic memories are
    // historical facts ("we tried X and it failed") — they stay true no
    // matter how the code changes, so they never auto-stale. Prospective
    // memories expire by their condition, not by code drift. Only semantic
    // ("what is true") and procedural ("how to") depend on current code.
    if (!STALEABLE_KINDS.includes(memory.meta.kind)) continue;
    // Repo-wide memories (empty scope) never auto-stale: no way to tell
    // which changes affect them. Scoped memories are checkable.
    if (memory.meta.scope.length === 0) continue;
    const changed = changedFilesSince(memory.meta.learned_commit, store.root);
    const hits = changed.filter((f) =>
      memory.meta.scope.some((g) => minimatch(f, g)),
    );
    if (hits.length > 0) {
      reports.push({ memory, changedFiles: hits });
    }
  }
  return reports;
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
