#!/usr/bin/env node
/**
 * memvine team simulator — the collaboration axis the retrieval benchmark can't
 * test: what happens when many agents write memories into one busy repo?
 *
 * It measures three things memvine's design claims to get right, and compares
 * against the obvious alternative (one shared notes file like AGENTS.md):
 *
 *   1. Merge conflicts — N agents each add memories on their own branch, then
 *      the branches merge into main. memvine stores one file per memory with a
 *      unique id, so concurrent adds are disjoint; a single shared notes file
 *      collides at its end. We count conflicts for both.
 *   2. Duplicate memories — agents independently learn overlapping facts. We
 *      count how many stored memories are near-duplicates, and show recall
 *      collapses them even though storage doesn't (the case for CI dedup).
 *   3. PR noise — when code changes, `stale --mark` rewrites memory files. We
 *      count how many files a single stale-marking commit touches (the cost the
 *      roadmap's derived/dynamic status would remove).
 *
 * Fully deterministic and dependency-free. Run: node benchmark/team-sim.mjs
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { execFileSync } from "node:child_process";
import { Store, findStale, markStale } from "../dist/index.js";

const AGENTS = 20;
const UNIQUE_PER_AGENT = 2; // distinct facts each agent learns
const COMMON_POOL = [
  "The build cache lives in .turbo and is safe to delete.",
  "Auth uses magic links; passwords were removed in 2026.",
  "Run `make migrate` before starting the dev server.",
  "The CI flake in test_payments is a known race, not a real failure.",
  "Feature flags are read from config/flags.yaml at boot.",
];

function git(dir, args) {
  return execFileSync("git", args, { cwd: dir, encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] });
}
function tryMerge(dir, branch) {
  try {
    git(dir, ["merge", "--no-edit", branch]);
    return true; // clean
  } catch {
    try { git(dir, ["merge", "--abort"]); } catch {}
    return false; // conflict
  }
}
function initRepo() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "memvine-team-"));
  git(dir, ["init", "-q", "-b", "main"]);
  git(dir, ["config", "user.email", "team@memvine.dev"]);
  git(dir, ["config", "user.name", "team"]);
  return dir;
}

// A memory body for agent `a`, slot `s`: some unique, some drawn from the shared
// pool (so different agents independently store the same fact = duplicates).
function bodyFor(a, s) {
  if (s < UNIQUE_PER_AGENT) return `Agent ${a} learned that module m${a}_${s} owns its own retry policy.`;
  return COMMON_POOL[(a + s) % COMMON_POOL.length];
}
const MEMS_PER_AGENT = UNIQUE_PER_AGENT + 2; // 2 unique + 2 from the common pool

// --- 1 & 2: memvine, one file per memory ----------------------------------
function runMemvine() {
  const dir = initRepo();
  fs.mkdirSync(path.join(dir, "src"), { recursive: true });
  fs.writeFileSync(path.join(dir, "src/app.ts"), "export const v = 1;\n");
  git(dir, ["add", "-A"]);
  git(dir, ["commit", "-q", "-m", "base"]);
  const store = Store.init(dir);
  git(dir, ["add", "-A"]);
  git(dir, ["commit", "-q", "-m", "init memvine"]);

  const bodies = [];
  for (let a = 0; a < AGENTS; a++) {
    git(dir, ["checkout", "-q", "-B", `agent${a}`, "main"]);
    for (let s = 0; s < MEMS_PER_AGENT; s++) {
      const body = bodyFor(a, s);
      bodies.push(body);
      store.add({ body, kind: "semantic", scope: ["src/app.ts"], verified: true, agent: `agent${a}` });
    }
    git(dir, ["add", "-A"]);
    git(dir, ["commit", "-q", "-m", `agent${a} memories`]);
  }

  git(dir, ["checkout", "-q", "main"]);
  let conflicts = 0;
  for (let a = 0; a < AGENTS; a++) if (!tryMerge(dir, `agent${a}`)) conflicts++;

  // Duplicates among what actually landed in the shared store.
  const stored = store.list({ status: ["active"] }).map((m) => m.body);
  const seen = new Map();
  let duplicates = 0;
  for (const b of stored) {
    const key = b.toLowerCase().replace(/\s+/g, " ").trim();
    if (seen.has(key)) duplicates++;
    else seen.set(key, true);
  }

  // Recall collapses duplicates even though storage kept them.
  const recalled = store.recall("magic links passwords auth", "src/app.ts", { limit: 10 });
  const recalledDupPool = recalled.memories.filter((m) =>
    m.body.startsWith("Auth uses magic links"),
  ).length;

  // 3: PR noise from stale-marking on a code change.
  fs.writeFileSync(path.join(dir, "src/app.ts"), "export const v = 2;\n");
  git(dir, ["add", "-A"]);
  git(dir, ["commit", "-q", "-m", "change app"]);
  markStale(store, findStale(store));
  const churn = git(dir, ["status", "--porcelain", ".memvine"])
    .split("\n").filter((l) => l.trim().endsWith(".md")).length;

  const total = stored.length;
  fs.rmSync(dir, { recursive: true, force: true });
  return { conflicts, total, duplicates, recalledDupPool, churn };
}

// --- baseline: one shared notes file (AGENTS.md-style) ---------------------
function runSharedFile() {
  const dir = initRepo();
  fs.writeFileSync(path.join(dir, "NOTES.md"), "# Team notes\n");
  git(dir, ["add", "-A"]);
  git(dir, ["commit", "-q", "-m", "base"]);

  for (let a = 0; a < AGENTS; a++) {
    git(dir, ["checkout", "-q", "-B", `agent${a}`, "main"]);
    const lines = [];
    for (let s = 0; s < MEMS_PER_AGENT; s++) lines.push(`- ${bodyFor(a, s)}`);
    fs.appendFileSync(path.join(dir, "NOTES.md"), lines.join("\n") + "\n");
    git(dir, ["add", "-A"]);
    git(dir, ["commit", "-q", "-m", `agent${a} notes`]);
  }

  git(dir, ["checkout", "-q", "main"]);
  let conflicts = 0;
  for (let a = 0; a < AGENTS; a++) if (!tryMerge(dir, `agent${a}`)) conflicts++;
  fs.rmSync(dir, { recursive: true, force: true });
  return { conflicts };
}

function main() {
  console.log(`\nmemvine team simulator — ${AGENTS} agents, ${MEMS_PER_AGENT} memories each`);
  console.log("=".repeat(72));
  const mv = runMemvine();
  const shared = runSharedFile();

  console.log(`memories written to shared store:   ${mv.total}`);
  console.log("");
  console.log("MERGE CONFLICTS merging all agent branches into main:");
  console.log(`  memvine (one file per memory):    ${mv.conflicts}`);
  console.log(`  single shared notes file:         ${shared.conflicts}`);
  console.log("");
  console.log("DUPLICATE MEMORIES (agents learned overlapping facts):");
  console.log(`  near-duplicates left in store:    ${mv.duplicates} of ${mv.total}  (${Math.round((mv.duplicates / mv.total) * 100)}%)`);
  console.log(`  but recall collapses them:        returned ${mv.recalledDupPool} copy of the recalled duplicate fact`);
  console.log("");
  console.log("PR NOISE from stale-marking one code change:");
  console.log(`  memory files rewritten:           ${mv.churn}  (derived/dynamic status would make this 0)`);
  console.log("=".repeat(72));
  console.log("\nTakeaways:");
  console.log("- Per-file memories eliminate the merge conflicts a shared notes file creates.");
  console.log("- Storage does NOT dedup, so duplicates accumulate — recall hides them, but");
  console.log("  reducing STORED duplicates needs CI dedup (roadmap).");
  console.log("- Committing stale status rewrites files = PR noise; derived status removes it.\n");
}

main();
