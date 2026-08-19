#!/usr/bin/env node
/**
 * memvine coding-memory benchmark — deterministic harness.
 *
 * Measures the two axes where memvine's value actually lives, WITHOUT needing
 * an LLM:
 *
 *   1. Retrieval selection — given a store salted with distractor memories,
 *      does `recall` surface the ONE memory relevant to the task (in top-k),
 *      and how much irrelevant memory does it inject alongside it?
 *   2. Staleness signal — when the memory's *evidence* file changes, does
 *      memvine flag it "needs revalidation"; when an *unrelated* file changes,
 *      does it stay silent? (precision / recall / unrelated-edit false-positive)
 *
 * The third axis — "does a fresh agent perform better with the memory?" — needs
 * a model. It's defined behind an adapter (adapters/model.mjs); with no model
 * configured the harness runs the deterministic axes and skips it.
 *
 * A store of size 1 makes retrieval trivial and proves nothing, so every case
 * carries distractor memories, and every "does it help?" run is compared
 * against a static-doc control. See benchmark/README.md.
 *
 * Run:  npm run build && node benchmark/run.mjs
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { Store, findStale } from "../dist/index.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const RECALL_K = 5; // top-k the harness asks recall for

// --- tiny git-backed fixture repo -----------------------------------------

function git(dir, args) {
  execFileSync("git", args, { cwd: dir, stdio: "pipe" });
}

/** Create a temp git repo, write `files` (path -> content), commit. */
function makeRepo(files) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "memvine-bench-"));
  git(dir, ["init", "-q"]);
  git(dir, ["config", "user.email", "bench@memvine.dev"]);
  git(dir, ["config", "user.name", "bench"]);
  writeFiles(dir, files);
  git(dir, ["add", "-A"]);
  git(dir, ["commit", "-q", "-m", "fixture"]);
  return dir;
}

function writeFiles(dir, files) {
  for (const [rel, content] of Object.entries(files)) {
    const abs = path.join(dir, rel);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, content);
  }
}

function applyEdit(dir, files, message) {
  writeFiles(dir, files);
  git(dir, ["add", "-A"]);
  git(dir, ["commit", "-q", "-m", message]);
}

// --- metrics helpers -------------------------------------------------------

/** Rough token estimate. Good enough to compare context sizes across cases. */
const estTokens = (text) => Math.ceil(text.length / 4);

/** How the MCP server renders recall — used to size the context it would inject. */
function renderRecall(memories) {
  return memories
    .map(
      (m) =>
        `[${m.meta.id}] (${m.meta.kind}, ${m.meta.status}, confidence=${m.meta.confidence}` +
        (m.meta.scope.length ? `, scope=${m.meta.scope.join(",")}` : "") +
        `)\n${m.body}`,
    )
    .join("\n\n---\n\n");
}

// --- one case --------------------------------------------------------------

function runCase(c) {
  const dir = makeRepo(c.files);
  try {
    const store = Store.init(dir);

    // Seed distractors first, then the target memory — order must not matter.
    const distractorIds = new Set(
      (c.distractors ?? []).map(
        (d) => store.add({ ...d, agent: "bench" }).meta.id,
      ),
    );
    const target = store.add({ ...c.memory, agent: "bench" });

    // 1. Retrieval selection --------------------------------------------------
    const { memories } = store.recall(c.task.query, c.task.path, { limit: RECALL_K });
    const returnedIds = memories.map((m) => m.meta.id);
    const rank = returnedIds.indexOf(target.meta.id); // -1 if missed
    const hitAt3 = rank >= 0 && rank < 3;
    const hitAt5 = rank >= 0 && rank < 5;
    const injected = returnedIds.filter((id) => distractorIds.has(id)).length;
    const injectionRate = returnedIds.length ? injected / returnedIds.length : 0;
    const contextTokens = estTokens(renderRecall(memories));

    // 2. Staleness signal -----------------------------------------------------
    // Unrelated edit first: the target must stay silent (false positive if not).
    applyEdit(dir, c.edits.unrelated, "unrelated change");
    const flaggedAfterUnrelated = findStale(store).some(
      (r) => r.memory.meta.id === target.meta.id,
    );
    // Then the direct-evidence edit: the target MUST flag (true positive if so).
    applyEdit(dir, c.edits.directEvidence, "change the evidence");
    const flaggedAfterDirect = findStale(store).some(
      (r) => r.memory.meta.id === target.meta.id,
    );

    return {
      name: c.name,
      hitAt3,
      hitAt5,
      rank: rank >= 0 ? rank + 1 : null,
      injectionRate,
      contextTokens,
      staleTP: flaggedAfterDirect, // should be true
      staleFP: flaggedAfterUnrelated, // should be false
    };
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// --- runner ----------------------------------------------------------------

function loadCases() {
  const dir = path.join(HERE, "cases");
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .sort()
    .map((f) => JSON.parse(fs.readFileSync(path.join(dir, f), "utf8")));
}

function pct(x) {
  return `${Math.round(x * 100)}%`;
}

function main() {
  const cases = loadCases();
  if (cases.length === 0) {
    console.error("No cases in benchmark/cases/. Add a *.json case first.");
    process.exit(1);
  }
  const results = cases.map(runCase);

  console.log("\nmemvine coding-memory benchmark");
  console.log("=".repeat(72));
  console.log(
    ["case".padEnd(22), "hit@3", "hit@5", "rank", "inject", "tokens", "stale✓", "stale✗"].join("  "),
  );
  for (const r of results) {
    console.log(
      [
        r.name.padEnd(22),
        String(r.hitAt3).padEnd(5),
        String(r.hitAt5).padEnd(5),
        String(r.rank ?? "—").padEnd(4),
        pct(r.injectionRate).padEnd(6),
        String(r.contextTokens).padEnd(6),
        (r.staleTP ? "ok" : "MISS").padEnd(6),
        r.staleFP ? "FALSE+" : "ok",
      ].join("  "),
    );
  }

  // Aggregate — staleness precision/recall over the paired edits.
  const n = results.length;
  const tp = results.filter((r) => r.staleTP).length; // direct edits correctly flagged
  const fn = n - tp; // direct edits missed
  const fp = results.filter((r) => r.staleFP).length; // unrelated edits wrongly flagged
  const tn = n - fp;
  const precision = tp + fp ? tp / (tp + fp) : 1;
  const recall = tp + fn ? tp / (tp + fn) : 1;

  console.log("-".repeat(72));
  console.log(`retrieval recall@3:            ${pct(results.filter((r) => r.hitAt3).length / n)}`);
  console.log(`retrieval recall@5:            ${pct(results.filter((r) => r.hitAt5).length / n)}`);
  console.log(`avg wrong-memory injection:    ${pct(results.reduce((s, r) => s + r.injectionRate, 0) / n)}`);
  console.log(`avg context tokens:            ${Math.round(results.reduce((s, r) => s + r.contextTokens, 0) / n)}`);
  console.log(`staleness precision:           ${pct(precision)}  (TP=${tp} FP=${fp})`);
  console.log(`staleness recall:              ${pct(recall)}  (TP=${tp} FN=${fn})`);
  console.log(`unrelated-edit false-positive: ${pct(fp / n)}  (TN=${tn})`);
  console.log("=".repeat(72));
  console.log(
    "\nAxis 3 (task-success with vs without memory) needs a model — see adapters/model.mjs.\n",
  );
}

main();
