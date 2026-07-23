/**
 * The digest compiler: renders the most valuable active memories into a
 * size-budgeted block inside CLAUDE.md / AGENTS.md, between markers, so
 * agents that don't speak MCP still benefit. Native startup budgets are
 * tight (Claude Code loads ~25KB), so the digest is the curated top slice —
 * memvine decides what deserves those bytes.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { Memory } from "./schema.js";
import { Store } from "./store.js";

const BEGIN = "<!-- memvine:begin (auto-generated — do not edit between markers; run `memvine compile`) -->";
const END = "<!-- memvine:end -->";

const CONFIDENCE_RANK = { high: 0, medium: 1, low: 2 } as const;

function renderMemory(m: Memory): string {
  const scope = m.meta.scope.length ? ` _(scope: ${m.meta.scope.join(", ")})_` : "";
  return `- **[${m.meta.kind}]**${scope} ${m.body.replace(/\s+/g, " ").trim()}`;
}

export function buildDigest(store: Store, budgetBytes: number): string {
  const memories = store
    .list({ status: ["active"], includeLocal: false })
    .sort(
      (a, b) =>
        CONFIDENCE_RANK[a.meta.confidence] - CONFIDENCE_RANK[b.meta.confidence] ||
        b.meta.learned_at.localeCompare(a.meta.learned_at),
    );
  const header =
    "## Project memory (memvine)\n\n" +
    "Learned by coding agents, maintained by [memvine](https://github.com/memvine/memvine). " +
    "Full store with provenance: `.memvine/`.\n\n";
  let out = header;
  let included = 0;
  for (const m of memories) {
    const line = renderMemory(m) + "\n";
    if (Buffer.byteLength(out + line, "utf8") > budgetBytes) break;
    out += line;
    included++;
  }
  const dropped = memories.length - included;
  if (dropped > 0) {
    out += `\n_${dropped} more memor${dropped === 1 ? "y" : "ies"} in \`.memvine/\` — ask your agent to recall them via MCP._\n`;
  }
  return out.trimEnd();
}

/** Insert or replace the digest block in the target file. Creates the file if absent. */
export function compileInto(store: Store, fileName: string): string {
  const target = path.join(store.root, fileName);
  const digest = `${BEGIN}\n${buildDigest(store, store.config().digest_budget_bytes)}\n${END}`;
  let content = fs.existsSync(target) ? fs.readFileSync(target, "utf8") : "";
  const beginIdx = content.indexOf(BEGIN);
  const endIdx = content.indexOf(END);
  if (beginIdx !== -1 && endIdx !== -1) {
    content =
      content.slice(0, beginIdx) + digest + content.slice(endIdx + END.length);
  } else {
    content = content.trimEnd() + (content.trim() ? "\n\n" : "") + digest + "\n";
  }
  fs.writeFileSync(target, content);
  return target;
}
