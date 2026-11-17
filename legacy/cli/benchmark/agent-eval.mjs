#!/usr/bin/env node
/**
 * memvine coding-agent eval — the task-success axis.
 *
 * For each task, runs the SAME agent under three conditions with identical
 * model/settings, changing only what memory is in context:
 *
 *   - none    : the issue only.
 *   - static  : the issue + a static AGENTS.md holding EVERY earlier lesson
 *               (and distractors), unfiltered — the honest control.
 *   - memvine : the issue + memvine's `recall`, scoped to the task's file.
 *
 * If memvine only ties `static` on success, retrieval isn't the differentiator —
 * so the runner reports success AND the context tokens each condition spends,
 * because memvine's bet is equal success at far less context (plus staleness,
 * which this axis doesn't test).
 *
 * Real run (needs a model + Docker for grading):
 *   MEMVINE_MODEL_BASE_URL=http://localhost:8080 MEMVINE_MODEL_NAME=... \
 *     node benchmark/agent-eval.mjs
 *   # then grade the emitted predictions_<condition>.jsonl with the official
 *   # SWE-bench harness. See benchmark/AGENT-EVAL.md.
 *
 * Wiring check (no model, no Docker):
 *   MEMVINE_EVAL_MOCK=1 node benchmark/agent-eval.mjs
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { Store } from "../dist/index.js";
import * as model from "./adapters/model.mjs";
import { grade, gradesInline } from "./adapters/grader.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CONDITIONS = ["none", "static", "memvine"];
const SYSTEM =
  "You are a coding agent. Read the issue and produce a unified-diff patch that resolves it. " +
  "If PROJECT MEMORY is provided, use it when relevant.";

const estTokens = (t) => Math.ceil(t.length / 4);
const asDoc = (mems) => mems.map((m) => `- ${m.body}`).join("\n");

function tempGitRepo() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "memvine-agent-"));
  execFileSync("git", ["init", "-q"], { cwd: dir });
  execFileSync("git", ["config", "user.email", "eval@memvine.dev"], { cwd: dir });
  execFileSync("git", ["config", "user.name", "eval"], { cwd: dir });
  fs.writeFileSync(path.join(dir, "SEED"), "seed\n");
  execFileSync("git", ["add", "-A"], { cwd: dir });
  execFileSync("git", ["commit", "-q", "-m", "seed"], { cwd: dir });
  return dir;
}

function buildContext(condition, c) {
  if (condition === "none") return "";
  const all = [...c.dependencyMemories, ...(c.distractorMemories ?? [])];
  if (condition === "static") {
    // Static AGENTS.md control: every lesson, unfiltered, every time.
    return "PROJECT MEMORY (static AGENTS.md):\n" + asDoc(all);
  }
  // memvine: seed a store with the same memories, then recall scoped to the file.
  const dir = tempGitRepo();
  try {
    const store = Store.init(dir);
    for (const m of all) store.add({ ...m, verified: true, agent: "eval" });
    const r = store.recall(c.issue, c.firstFile, { limit: 5 });
    return (
      "PROJECT MEMORY (memvine recall):\n" +
      r.memories.map((m) => `- [via ${r.sources[m.meta.id]}] ${m.body}`).join("\n")
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function loadCases() {
  const dir = path.join(HERE, "agent-cases");
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .sort()
    .map((f) => JSON.parse(fs.readFileSync(path.join(dir, f), "utf8")));
}

async function main() {
  const cases = loadCases();
  if (cases.length === 0) {
    console.error("No cases in benchmark/agent-cases/. Add a *.json case first.");
    process.exit(1);
  }
  console.log(`\nmemvine coding-agent eval — ${cases.length} tasks, ${model.mock ? "MOCK" : "REAL"} model`);
  console.log("=".repeat(72));

  const predictions = Object.fromEntries(CONDITIONS.map((c) => [c, []]));
  const stats = Object.fromEntries(
    CONDITIONS.map((c) => [c, { passed: 0, graded: 0, tokens: 0 }]),
  );

  for (const c of cases) {
    for (const condition of CONDITIONS) {
      const context = buildContext(condition, c);
      const prompt = `ISSUE (${c.instance_id}):\n${c.issue}\n\n${context}`.trim();
      const patch = await model.generate({ system: SYSTEM, prompt });
      predictions[condition].push({
        instance_id: c.instance_id,
        model_name_or_path: `memvine-eval-${condition}`,
        model_patch: patch,
      });
      stats[condition].tokens += estTokens(context);
      if (gradesInline) {
        const { passed } = await grade({ task: c, patch });
        stats[condition].graded++;
        if (passed) stats[condition].passed++;
      }
    }
  }

  // Emit SWE-bench-format predictions for the real grader.
  const outDir = path.join(HERE, "out");
  fs.mkdirSync(outDir, { recursive: true });
  for (const condition of CONDITIONS) {
    const p = path.join(outDir, `predictions_${condition}.jsonl`);
    fs.writeFileSync(p, predictions[condition].map((x) => JSON.stringify(x)).join("\n") + "\n");
  }

  const n = cases.length;
  console.log(["condition".padEnd(10), "success", "avg ctx tokens"].join("  "));
  for (const condition of CONDITIONS) {
    const s = stats[condition];
    const success = gradesInline ? `${s.passed}/${s.graded}` : "(grade externally)";
    console.log(
      [condition.padEnd(10), String(success).padEnd(7), String(Math.round(s.tokens / n))].join("  "),
    );
  }
  console.log("-".repeat(72));
  console.log(`predictions written to benchmark/out/predictions_<condition>.jsonl`);
  if (gradesInline) {
    console.log(
      "\nMOCK results prove WIRING only (does the needed memory reach each condition),\n" +
        "not task success. For real numbers, run with a model + the SWE-bench grader.",
    );
  } else {
    console.log(
      "\nNext: grade each predictions file with the official SWE-bench harness on a\n" +
        "Docker host (see benchmark/AGENT-EVAL.md), then compare success across conditions.",
    );
  }
  console.log("");
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
