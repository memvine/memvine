#!/usr/bin/env node
/** memvine CLI: init / add / list / stale / compile / serve */
import { configureCodex } from "./codex.js";
import { Command } from "commander";
import { Store } from "./store.js";
import { findStale, markStale } from "./staleness.js";
import { compileInto, digestParts } from "./compile.js";
import { serve } from "./mcp.js";
import { git } from "./git.js";
import { KINDS, MemoryKind } from "./schema.js";
import { renderRecall } from "./render.js";

const program = new Command();

function requireStore(): Store {
  const store = Store.find(process.cwd());
  if (!store) {
    console.error("No .memvine store found. Run `memvine init` in your repo first.");
    process.exit(1);
  }
  return store;
}

program
  .name("memvine")
  .description(
    "Git-native memory for coding agents — what your agent learns lives in your repo, travels with the clone, and expires when the code changes.",
  )
  .version("0.3.0");

program
  .command("init")
  .description("Initialize a .memvine store in this git repository")
  .option("--codex", "also register Memvine in this project’s Codex MCP configuration")
  .option(
    "--no-compile",
    "don't write the memvine usage block into CLAUDE.md / AGENTS.md",
  )
  .action((opts) => {
    const store = Store.init(process.cwd());
    console.log(`Initialized memvine store at ${store.dir}`);
    if (opts.compile !== false) {
      for (const f of ["CLAUDE.md", "AGENTS.md"]) {
        console.log(`Wrote the memvine usage block into ${compileInto(store, f)}`);
      }
      console.log(
        "  ^ tells your agent to recall/remember every task. Re-run `memvine compile` after new memories.",
      );
    }
    if (opts.codex) {
      try {
        const result = configureCodex(store.root);
        console.log(`${result.created ? "Configured" : "Already configured"} Codex: ${result.file}`);
        console.log("Open this repository as a trusted project in Codex, restart the client, and start a fresh task.");
        console.log("Ask: Call Memvine recall for this project. Verify an actual tool result.");
        console.log("Configuration contains machine-specific paths; do not copy it unchanged to another clone or machine.");
      } catch (error) {
        console.error(`Store initialized, but Codex setup failed: ${(error as Error).message}`);
        process.exitCode = 1;
      }
    } else {
      console.log("Connect Codex: memvine init --codex");
      console.log("Connect Claude Code: claude mcp add memvine -- memvine serve");
    }
  });

program
  .command("add <body>")
  .description("Manually add a memory (agents usually do this via MCP)")
  .option(
    "-k, --kind <kind>",
    "episodic (what happened) | semantic (what is true) | procedural (how to) | prospective (do later)",
    "semantic",
  )
  .option("-t, --tags <tags...>", "freeform domain labels, e.g. test auth (or comma-separated: \"test,-r flag\")")
  .option("-s, --scope <globs...>", "path globs this memory is about (space- or comma-separated)")
  .option("-c, --confidence <level>", "high | medium | low", "medium")
  .option("-l, --local", "personal memory (gitignored, not shared)")
  .option("--verified", "store as verified team knowledge (committed) instead of an unverified candidate")
  .option("-e, --evidence <text>", "how it was confirmed, e.g. 'tests green at a1b4c9e' (implies --verified)")
  .option("--supersedes <id>", "id of the memory this one replaces (it is retired)")
  .action((body: string, opts) => {
    const verified = opts.verified || Boolean(opts.evidence);
    const store = requireStore();
    if (opts.supersedes && !store.get(opts.supersedes)) {
      console.error(`No memory with id ${opts.supersedes} to supersede.`);
      process.exit(1);
    }
    const m = store.add({
      body,
      kind: opts.kind as MemoryKind,
      // Commas also separate values: a later space-separated value starting with "-"
      // would be parsed as an option, so "-t 'build,-r flag'" is the safe spelling.
      tags: splitList(opts.tags),
      scope: splitList(opts.scope),
      confidence: opts.confidence,
      verified,
      evidence: opts.evidence,
      local: opts.local,
      supersedes: opts.supersedes,
      agent: "cli",
    });
    // add() only auto-retires the predecessor of VERIFIED memories; an explicit
    // --supersedes from the CLI is the caller saying the old one is replaced.
    if (opts.supersedes) store.retire(opts.supersedes, "superseded");
    const where = m.meta.verified && !opts.local ? "verified, shared file; not automatically committed" : "local memory";
    console.log(`Stored ${m.meta.id} (${m.meta.kind}, ${where}, learned@${m.meta.learned_commit})`);
  });

program
  .command("validate <id>")
  .description("Promote an unverified candidate to verified, shared file; not automatically committed team knowledge")
  .option("-e, --evidence <text>", "how it was confirmed, e.g. 'PR #123', 'user confirmed'")
  .action((id: string, opts) => {
    const res = requireStore().validate(id, opts.evidence);
    if (!res) {
      console.error(`No memory with id ${id}.`);
      process.exit(1);
    }
    console.log(
      res.promoted
        ? `Validated ${id} — promoted to the committed store. Commit .memvine/ to share it.`
        : `Validated ${id} — already shared; refreshed evidence.`,
    );
  });

program
  .command("retire <ids...>")
  .description("Archive memories that no longer apply — a finished next step, a fixed bug, a contradicted fact")
  .action((ids: string[]) => {
    const store = requireStore();
    let failed = 0;
    for (const id of ids) {
      if (store.retire(id, "archived")) console.log(`Retired ${id}`);
      else { console.error(`No memory with id ${id}.`); failed++; }
    }
    if (failed) process.exit(1);
  });

program
  .command("recall <query>")
  .description("Recall memories relevant to a query — what the MCP recall tool returns, for hooks and scripts")
  .option("-p, --path <path>", "repo-relative file path to scope results")
  .option("-n, --limit <n>", "max memories to return", (v: string) => parseInt(v, 10))
  .option("--scoped-only", "only memories whose scope matches --path (drops repo-wide and lexical matches)")
  .option("-x, --exclude <ids>", "comma-separated memory ids to skip (already shown this session)")
  .action((query: string, opts) => {
    const store = requireStore();
    const exclude = String(opts.exclude ?? "").split(",").map((s) => s.trim()).filter(Boolean);
    const res = store.recall(query, opts.path, { limit: opts.limit, exclude });
    let text = res.text;
    if (opts.scopedOnly) {
      const keep = res.memories.filter((m) => ["exact-path", "cross-scope"].includes(res.sources[m.meta.id]));
      text = keep.length ? renderRecall(keep, res.sources, 0) : "No memories found.";
    }
    console.log(text);
  });

program
  .command("list")
  .description("List memories")
  .option("-a, --all", "include superseded and archived")
  .option("-p, --path <path>", "only memories scoped to this path")
  .action((opts) => {
    const store = requireStore();
    const memories = store.list({
      status: opts.all ? undefined : ["active", "stale"],
      forPath: opts.path,
    });
    if (memories.length === 0) {
      console.log("No memories yet.");
      return;
    }
    for (const m of memories) {
      const scope = m.meta.scope.length ? ` scope=${m.meta.scope.join(",")}` : "";
      const tags = m.meta.tags.length ? ` tags=${m.meta.tags.join(",")}` : "";
      const trust = m.meta.verified ? "verified" : "candidate";
      console.log(
        `${m.meta.id}  [${m.meta.status}/${trust}] (${m.meta.kind}, ${m.meta.confidence}${tags}${scope}, learned@${m.meta.learned_commit})`,
      );
      console.log(`  ${m.body.split("\n")[0].slice(0, 100)}`);
    }
  });

program
  .command("stale")
  .description("Detect memories whose scoped files changed since they were last confirmed")
  .option("--mark", "mark detected memories as stale (default: report only)")
  .action((opts) => {
    const store = requireStore();
    const reports = findStale(store);
    if (reports.length === 0) {
      console.log("No newly stale scoped memories detected.");
      return;
    }
    for (const r of reports) {
      console.log(`${r.memory.meta.id}  needs revalidation (confirmed@${r.memory.meta.validated_commit})`);
      console.log(`  changed since: ${r.changedFiles.join(", ")}${r.unknown ? " (freshness unknown: Git history unavailable)" : ""}`);
      console.log(`  ${r.memory.body.split("\n")[0].slice(0, 100)}`);
    }
    if (opts.mark) {
      console.log(`\nMarked ${markStale(store, reports)} memories stale.`);
    } else {
      console.log("\nRun `memvine stale --mark` to mark these stale.");
    }
  });

program
  .command("compile")
  .description("Render the top active memories into CLAUDE.md / AGENTS.md digest blocks")
  .option("-t, --target <files...>", "target files", ["CLAUDE.md", "AGENTS.md"])
  .action((opts) => {
    const store = requireStore();
    for (const f of opts.target as string[]) {
      console.log(`Compiled digest into ${compileInto(store, f)}`);
    }
  });

program
  .command("digest")
  .description("Print a session briefing of the store — every memory in full while the budget allows, the rest as titles — for SessionStart hooks")
  .option("-b, --budget <bytes>", "byte budget", (v: string) => parseInt(v, 10))
  .option("--json", "print {text, fullIds, titledIds} so a hook can exclude shown memories from later recalls")
  .action((opts) => {
    const store = requireStore();
    const res = digestParts(store, opts.budget ?? store.config().digest_budget_bytes, { includeStale: true, mode: "session" });
    if (opts.json) console.log(JSON.stringify(res));
    else if (res.text) console.log(res.text);
  });

program
  .command("serve")
  .description("Run the memvine MCP server (stdio) for your coding agent")
  .action(async () => {
    await serve(requireStore());
  });

program
  .command("doctor")
  .description("Diagnose store data, Git sharing, and unavailable freshness history")
  .action(() => {
    const store = requireStore();
    console.log(`Repository: ${store.root}\nStore: ${store.dir}`);
    const issues = store.diagnostics();
    for (const issue of issues) console.log(`ERROR: ${issue}`);
    const all = store.list();
    const shared = store.list({ includeLocal: false });
    console.log(`${shared.length} shared; ${all.length - shared.length} local (personal or unverified).`);
    const unknown = findStale(store).filter(r => r.unknown);
    if (unknown.length) console.log(`WARNING: ${unknown.length} scoped memories have unavailable Git history. Fetch the validation commits, then recheck.`);
    const pending = git(["status", "--porcelain", "--", ".memvine/memories", ".memvine/config.json"], store.root);
    if (pending) console.log("WARNING: shared memory/config changes are not committed. Review and commit them to share through Git.");
    console.log("MCP uses this repository. Start serve from here; init does not configure an MCP client.");
    if (issues.length) process.exitCode = 1;
  });

program.parse();

function splitList(values?: string[]): string[] | undefined {
  return values?.flatMap((v) => v.split(",")).map((v) => v.trim()).filter(Boolean);
}
