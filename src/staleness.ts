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
import { diffFile, fileAt, hashFile, inspectChangesSince, headCommit, ChangeScan } from "./git.js";
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
  const cache: DiffCache = new Map();
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
    // Files confirmed against uncommitted content are checked even when they now match
    // validated_commit again — that is exactly the "edit was discarded" case.
    const candidates = new Set([
      ...changed.files.filter((f) => memory.meta.scope.some((g) => minimatch(f, g, { dot: true }))),
      ...Object.keys(memory.meta.validated_snapshot ?? {}),
    ]);
    const hits = [...candidates].filter((f) => touchesMemory(root, memory, f, memory.meta.validated_commit, cache));
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

type DiffCache = Map<string, { old: string | null; diff: ReturnType<typeof diffFile> }>;

/**
 * Code identifiers a memory names — `backticked` spans, `name(` calls,
 * snake_case and camelCase words. These anchor the memory to specific code.
 */
/** Keywords and everyday words that show up in backticks but name no specific code. */
const NOT_ANCHORS = new Set(("and or not in is if else elif for while do return def fn func function class const let var new " +
  "true false none null nil self this int float str string bool list dict map set array object type import from with " +
  "math left right value result error print input output args main test").split(" "));

export function anchorsOf(body: string): string[] {
  const out = new Set<string>();
  const ident = /[A-Za-z_][A-Za-z0-9_]{2,}/g;
  for (const span of body.match(/`[^`]+`/g) ?? []) for (const w of span.match(ident) ?? []) out.add(w);
  for (const m of body.matchAll(/([A-Za-z_][A-Za-z0-9_]{2,})\s*\(/g)) out.add(m[1]);
  for (const w of body.match(ident) ?? []) if (w.includes("_") || /[a-z][A-Z]/.test(w)) out.add(w);
  return [...out].filter((w) => !NOT_ANCHORS.has(w.toLowerCase()));
}

/**
 * Did the change to `file` since `base` touch code this memory is about?
 *
 * File-level staleness flags every memory scoped to a file on ANY edit, which in
 * a one-file project marks nearly the whole store stale every session and the
 * label stops meaning anything. When the memory names identifiers that exist in
 * the old file, only a change inside those identifiers' blocks (or a changed line
 * mentioning them) counts. A memory that names no code keeps file-level behavior.
 */
function touchesMemory(root: string, memory: Memory, file: string, base: string, cache: DiffCache): boolean {
  // Confirmed against uncommitted content: unchanged only while the file still hashes
  // the same. Any other content — edited further, or the edit discarded — is suspect,
  // because the memory described code that no longer exists in that form.
  const snap = memory.meta.validated_snapshot?.[file];
  if (snap) return hashFile(file, root) !== snap;
  const key = `${base}\0${file}`;
  let entry = cache.get(key);
  if (!entry) { entry = { old: fileAt(base, file, root), diff: diffFile(base, file, root) }; cache.set(key, entry); }
  if (entry.old === null || entry.diff === null) return true; // new file or git failure: be conservative
  const lines = entry.old.split("\n");
  // Anchors are plain identifiers ([A-Za-z0-9_]), so no regex escaping is needed.
  const word = (a: string) => new RegExp(`(^|[^A-Za-z0-9_])${a}([^A-Za-z0-9_]|$)`);
  const anchors = anchorsOf(memory.body).filter((a) => word(a).test(entry!.old!));
  if (anchors.length === 0) return true;
  for (const a of anchors) {
    const re = word(a);
    if (entry.diff.changedLines.some((l) => re.test(l))) return true;
    for (let i = 0; i < lines.length; i++) {
      if (!re.test(lines[i])) continue;
      const end = blockEnd(lines, i);
      if (entry.diff.oldRanges.some(([s, e]) => (s < end && e > i) || (s === e && s > i && s < end))) return true;
    }
  }
  return false;
}

/** End (exclusive) of the indented block a line opens; just the line itself if it opens none. */
function blockEnd(lines: string[], i: number): number {
  const indent = (l: string) => l.length - l.trimStart().length;
  if (!/[:{(\[]\s*$/.test(lines[i])) return i + 1;
  const base = indent(lines[i]);
  let j = i + 1;
  while (j < lines.length && (lines[j].trim() === "" || indent(lines[j]) > base)) j++;
  // Include a closing brace line at the same indentation (C-like languages).
  if (j < lines.length && /^\s*[}\])]/.test(lines[j])) j++;
  return j;
}

/**
 * Hashes of the memory's scoped files that differ from HEAD right now — recorded
 * whenever a memory is learned or re-confirmed, so knowledge written about code
 * the agent just edited (not yet committed) is not born stale.
 */
export function snapshotScope(root: string, scope: string[]): Record<string, string> | undefined {
  if (scope.length === 0) return undefined;
  const dirty = inspectChangesSince(headCommit(root), root).files.filter((f) => scope.some((g) => minimatch(f, g, { dot: true })));
  const out: Record<string, string> = {};
  for (const f of dirty) { const h = hashFile(f, root); if (h) out[f] = h; }
  return Object.keys(out).length ? out : undefined;
}


/** Refresh a memory's snapshot at (re)confirmation: set it, or clear an outdated one. */
export function setSnapshot(root: string, memory: Memory): void {
  const snapshot = snapshotScope(root, memory.meta.scope);
  if (snapshot) memory.meta.validated_snapshot = snapshot;
  else delete memory.meta.validated_snapshot;
}
