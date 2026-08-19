#!/usr/bin/env node
/** memvine CLI: init / add / list / stale / compile / serve */
import { Command } from "commander";
import { Store } from "./store.js";
import { findStale, markStale } from "./staleness.js";
import { compileInto } from "./compile.js";
import { serve } from "./mcp.js";
import { KINDS, MemoryKind } from "./schema.js";

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
  .version("0.1.0");

program
  .command("init")
  .description("Initialize a .memvine store in this git repository")
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
    console.log("Next: add it to your agent as an MCP server:");
    console.log("  claude mcp add memvine -- memvine serve");
  });

program
  .command("add <body>")
  .description("Manually add a memory (agents usually do this via MCP)")
  .option(
    "-k, --kind <kind>",
    "episodic (what happened) | semantic (what is true) | procedural (how to) | prospective (do later)",
    "semantic",
  )
  .option("-t, --tags <tags...>", "freeform domain labels, e.g. test auth")
  .option("-s, --scope <globs...>", "path globs this memory is about")
  .option("-c, --confidence <level>", "high | medium | low", "medium")
  .option("-l, --local", "personal memory (gitignored, not shared)")
  .option("--verified", "store as verified team knowledge (committed) instead of an unverified candidate")
  .option("-e, --evidence <text>", "how it was confirmed, e.g. 'tests green at a1b4c9e' (implies --verified)")
  .action((body: string, opts) => {
    const verified = opts.verified || Boolean(opts.evidence);
    const m = requireStore().add({
      body,
      kind: opts.kind as MemoryKind,
      tags: opts.tags,
      scope: opts.scope,
      confidence: opts.confidence,
      verified,
      evidence: opts.evidence,
      local: opts.local,
      agent: "cli",
    });
    const where = m.meta.verified && !opts.local ? "verified, committed" : "unverified candidate, local";
    console.log(`Stored ${m.meta.id} (${m.meta.kind}, ${where}, learned@${m.meta.learned_commit})`);
  });

program
  .command("validate <id>")
  .description("Promote an unverified candidate to verified, committed team knowledge")
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
        : `Validated ${id} — already committed; refreshed evidence.`,
    );
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
      console.log("All scoped memories are fresh.");
      return;
    }
    for (const r of reports) {
      console.log(`${r.memory.meta.id}  needs revalidation (confirmed@${r.memory.meta.validated_commit})`);
      console.log(`  changed since: ${r.changedFiles.join(", ")}`);
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
  .command("serve")
  .description("Run the memvine MCP server (stdio) for your coding agent")
  .action(async () => {
    await serve(requireStore());
  });

program.parse();
