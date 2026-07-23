/**
 * The memvine MCP server: how coding agents talk to the store.
 *
 * Design principle: memvine never calls an LLM itself. Judgment calls
 * (is this a duplicate? does it contradict an existing memory?) are made
 * by the CALLING agent — the tool descriptions instruct it to check before
 * writing. The agent that's already running pays for its own thinking;
 * memvine stays pure git + files, free forever.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { KINDS, MemoryKind } from "./schema.js";
import { Store } from "./store.js";
import { findStale, markStale } from "./staleness.js";

function fmt(memories: ReturnType<Store["list"]>): string {
  if (memories.length === 0) return "No memories found.";
  return memories
    .map(
      (m) =>
        `[${m.meta.id}] (${m.meta.kind}, ${m.meta.status}, confidence=${m.meta.confidence}` +
        (m.meta.scope.length ? `, scope=${m.meta.scope.join(",")}` : "") +
        `, learned@${m.meta.learned_commit})\n${m.body}`,
    )
    .join("\n\n---\n\n");
}

export async function serve(store: Store): Promise<void> {
  const server = new McpServer({ name: "memvine", version: "0.1.0" });

  server.tool(
    "recall",
    "Recall project memories relevant to what you're working on. Call this at the START of a task, and again when entering an unfamiliar part of the codebase. Returns memories with provenance (id, kind, status, the commit they were learned at). Treat memories with status=stale with suspicion: the code they describe has changed since they were learned — verify them, then either revise them (revise tool) or confirm them (mark them active again via revise).",
    {
      query: z
        .string()
        .describe(
          "Free-text description of what you're doing, e.g. 'auth integration tests failing'",
        ),
      path: z
        .string()
        .optional()
        .describe(
          "Repo-relative file path you're working on, to scope results, e.g. 'src/auth/login.ts'",
        ),
    },
    async ({ query, path }) => ({
      content: [{ type: "text", text: fmt(store.search(query, path)) }],
    }),
  );

  server.tool(
    "remember",
    "Store a durable project memory for future sessions (yours, other machines', and teammates' agents — memories are committed to the repo). BEFORE storing: call recall to check for duplicates or contradictions. If this new knowledge CONTRADICTS an existing memory, do not just add it — pass supersedes with the old memory's id so the old one is retired with a pointer to its replacement. Store things worth re-learning: build quirks, architectural decisions, gotchas, conventions, environment setup. Do NOT store secrets, credentials, or anything you'd not commit to the repo.",
    {
      body: z.string().describe("The memory itself, in plain markdown. Be specific and self-contained."),
      kind: z.enum(KINDS as [MemoryKind, ...MemoryKind[]]).describe("Category of memory"),
      scope: z
        .array(z.string())
        .optional()
        .describe(
          "Path globs this memory is about, e.g. ['src/auth/**']. IMPORTANT for staleness detection: scoped memories get flagged when their code changes. Omit only for truly repo-wide knowledge.",
        ),
      confidence: z.enum(["high", "medium", "low"]).optional(),
      supersedes: z
        .string()
        .optional()
        .describe("id of an existing memory this replaces (use when new knowledge contradicts old)"),
      local: z
        .boolean()
        .optional()
        .describe("true = personal memory (gitignored, not shared with the team)"),
    },
    async (args) => {
      const m = store.add({
        body: args.body,
        kind: args.kind,
        scope: args.scope,
        confidence: args.confidence,
        supersedes: args.supersedes,
        local: args.local,
        agent: "mcp",
      });
      return {
        content: [
          {
            type: "text",
            text: `Stored ${m.meta.id} (${m.meta.kind}, learned@${m.meta.learned_commit}).${args.supersedes ? ` Superseded ${args.supersedes}.` : ""}`,
          },
        ],
      };
    },
  );

  server.tool(
    "revise",
    "Update an existing memory after revalidating it. Use when: a stale memory turned out still true (set status=active), the content needs correcting (pass new body), or the memory no longer applies at all (set status=archived).",
    {
      id: z.string().describe("Memory id, e.g. mem_ab12cd34"),
      body: z.string().optional().describe("Corrected content (omit to keep current)"),
      status: z.enum(["active", "archived"]).optional(),
    },
    async ({ id, body, status }) => {
      const found = store.get(id);
      if (!found) {
        return { content: [{ type: "text", text: `No memory with id ${id}.` }] };
      }
      if (body !== undefined) found.memory.body = body.trim();
      if (status !== undefined) {
        found.memory.meta.status = status;
        if (status === "active") delete found.memory.meta.stale_since;
      }
      // Revalidation refreshes provenance to now.
      found.memory.meta.learned_at = new Date().toISOString();
      store.write(found.memory, found.local);
      return { content: [{ type: "text", text: `Revised ${id}.` }] };
    },
  );

  server.tool(
    "check_stale",
    "Scan for memories whose scoped files have changed since they were learned, and mark them stale. Run at session start. Returns the list of newly-stale memories so you can revalidate the relevant ones as you encounter their territory.",
    {},
    async () => {
      const reports = findStale(store);
      const n = markStale(store, reports);
      const text =
        n === 0
          ? "No newly stale memories."
          : `Marked ${n} memories stale:\n\n` +
            reports
              .map(
                (r) =>
                  `[${r.memory.meta.id}] changed files: ${r.changedFiles.join(", ")}\n${r.memory.body.slice(0, 200)}`,
              )
              .join("\n\n");
      return { content: [{ type: "text", text }] };
    },
  );

  const transport = new StdioServerTransport();
  await server.connect(transport);
}
